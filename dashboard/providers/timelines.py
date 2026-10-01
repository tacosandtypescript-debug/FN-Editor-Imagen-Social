"""Proveedores de descubrimiento de publicaciones.

Tres fuentes intercambiables:

* ``browser``  — lee la timeline pública en el navegador real del usuario.
  Se verificó que funciona con Chrome visible; en modo oculto X responde con
  error, así que ``headless`` no sirve para x.com.
* ``nitter``   — RSS de instancias Nitter, sin autenticación. Es la red de
  seguridad cuando el navegador falla o no está instalado.
* ``xapi``     — API v2 oficial de X. Requiere token de pago; queda disponible
  pero desactivada por defecto.

X reescribió su interfaz y ya no publica atributos ``data-testid``: el
extractor de navegador se apoya en la clase ``whitespace-pre-wrap`` del bloque
de texto y en los enlaces ``/usuario/status/ID``, que sí siguen presentes.
"""

from __future__ import annotations

import html
import json
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .. import config
from .base import ProviderError, ProviderStatus, TweetRecord

USER_AGENT = "EditImg-Dashboard/0.1 (+https://github.com/tacosandtypescript-debug/FN-Editor-Imagen-Social)"
STATUS_IN_TEXT = re.compile(r"/status/(\d+)")
HANDLE_IN_PATH = re.compile(r"^/([A-Za-z0-9_]{1,20})/")
IMG_SRC = re.compile(r'<img\b[^>]*?src="([^"]+)"', re.IGNORECASE)
#: Para limpiar el texto hay que consumir la etiqueta completa; si solo se
#: recorta hasta `src`, los atributos posteriores quedan como texto visible.
IMG_TAG_ALL = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
BR_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
ANY_TAG = re.compile(r"<[^>]+>")

#: Solo un navegador a la vez: Chromium no admite bien sesiones concurrentes
#: sobre el mismo perfil y además evita ráfagas contra X.
_BROWSER_LOCK = threading.Lock()

#: JS validado contra el DOM actual de x.com. Recibe el array de `article`
#: (eso es lo que entrega `evaluate_all`) y devuelve un objeto por artículo.
EXTRACT_JS = r"""
els => els.map(el => {
  const textNode = el.querySelector('div[class*="whitespace-pre-wrap"]');
  let status = null, permalink = null, author = null;
  for (const link of el.querySelectorAll('a[href*="/status/"]')) {
    const href = link.getAttribute('href') || '';
    const m = href.match(/^\/([^\/]+)\/status\/(\d+)/);
    if (m) {
      author = m[1];
      status = m[2];
      permalink = 'https://x.com/' + m[1] + '/status/' + m[2];
      break;
    }
  }
  let relative = null;
  for (const node of el.querySelectorAll('*')) {
    if (node.children.length === 0) {
      const value = (node.textContent || '').trim();
      if (/^\d+\s?[smhd]$/.test(value)) relative = value;
    }
  }
  const media = [];
  for (const img of el.querySelectorAll('img[src*="pbs.twimg.com/media"], img[src*="pbs.twimg.com/amplify_video_thumb"]')) {
    const src = img.getAttribute('src');
    if (src && !media.includes(src)) media.push(src);
  }
  const pinned = /pin/i.test(el.textContent.slice(0, 40));
  return {
    text: textNode ? textNode.innerText : null,
    status: status,
    permalink: permalink,
    author: author,
    relative_time: relative,
    media: media.slice(0, 24),
    pinned: pinned,
  };
})
"""


# ----------------------------------------------------------------------
# Nitter RSS
# ----------------------------------------------------------------------
class NitterTimeline:
    """Descubre publicaciones leyendo el RSS de instancias Nitter."""

    name = "nitter"

    def __init__(self, instances: tuple[str, ...] | None = None) -> None:
        settings = config.Settings()
        self.instances = tuple(instances or settings.nitter_instances)
        self._preferred: str | None = None

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            self.name,
            bool(self.instances),
            f"{len(self.instances)} instancia(s) configurada(s); son servicios de terceros y pueden caer",
        )

    def fetch(self, handle: str) -> list[dict]:
        if not self.instances:
            raise ProviderError("no hay instancias Nitter configuradas")
        ordered = list(self.instances)
        if self._preferred and self._preferred in ordered:
            ordered.remove(self._preferred)
            ordered.insert(0, self._preferred)

        errors: list[str] = []
        for base in ordered:
            base = base.rstrip("/")
            url = f"{base}/{handle}/rss"
            try:
                body = _http_get(url, accept="application/rss+xml, application/xml")
                tweets = parse_nitter_rss(body, source_handle=handle)
            except (HTTPError, URLError, ET.ParseError, OSError, ValueError) as exc:
                errors.append(f"{base}: {str(exc)[:80]}")
                continue
            self._preferred = base
            return [tweet.as_dict() for tweet in tweets]
        raise ProviderError("ninguna instancia Nitter respondió (" + "; ".join(errors) + ")")


def parse_nitter_rss(body: bytes | str, source_handle: str) -> list[TweetRecord]:
    """Convierte el RSS de Nitter en registros normalizados."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    root = ET.fromstring(body)
    records: list[TweetRecord] = []
    for item in root.iter("item"):
        guid = _text(item, "guid")
        link = _text(item, "link") or ""
        match = STATUS_IN_TEXT.search(link) or STATUS_IN_TEXT.search(guid or "")
        if not match:
            continue
        tweet_id = match.group(1)

        title = _text(item, "title") or ""
        description = _text(item, "description") or ""
        text = _html_to_text(description)
        # El título se recorta en publicaciones largas; la descripción es más fiel.
        if len(title.strip()) > len(text.strip()):
            text = title.strip()

        media = [html.unescape(src) for src in IMG_SRC.findall(description)]

        author = _text(item, "creator") or ""
        author = author.lstrip("@").strip() or None
        if not author:
            path_match = HANDLE_IN_PATH.match(link)
            author = path_match.group(1) if path_match else None

        pub_date = _text(item, "pubDate")
        records.append(
            TweetRecord(
                tweet_id=tweet_id,
                source_handle=source_handle,
                text=text.strip() or None,
                url=f"https://x.com/{author or source_handle}/status/{tweet_id}",
                author_handle=author or source_handle,
                posted_at=_iso_from_rfc822(pub_date),
                relative_time=None,
                media=media,
            )
        )
    return records


# ----------------------------------------------------------------------
# Navegador
# ----------------------------------------------------------------------
class BrowserTimeline:
    """Lee la timeline en el navegador real, con el perfil persistente."""

    name = "browser"

    def __init__(self) -> None:
        self.settings = config.Settings()

    # -- disponibilidad -------------------------------------------------
    @staticmethod
    def playwright_available() -> tuple[bool, str]:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False, (
                "Playwright no está instalado. Actívalo con: "
                "pip install -r requirements-dashboard.txt"
            )
        return True, "Playwright disponible"

    def status(self) -> ProviderStatus:
        ok, detail = self.playwright_available()
        if not ok:
            return ProviderStatus(self.name, False, detail)
        if self.settings.browser_headless:
            return ProviderStatus(
                self.name,
                True,
                "headless activado: x.com suele rechazarlo, usa DASHBOARD_BROWSER_HEADLESS=0",
            )
        profile = config.PROFILES_DIR / "x"
        return ProviderStatus(
            self.name,
            True,
            f"Chrome «{self.settings.browser_channel}» con perfil en {profile}",
        )

    # -- lectura --------------------------------------------------------
    def fetch(self, handle: str) -> list[dict]:
        """Lee una sola cuenta. Mantiene el contrato común de proveedor."""
        resultados = self.fetch_many([handle])
        resultado = resultados.get(handle)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    def fetch_many(self, handles: list[str], on_progress=None) -> dict:
        """Lee varias cuentas **en una sola sesión de navegador**.

        Es importante: abrir y cerrar Chrome una vez por cuenta multiplica el
        tiempo y hace parpadear ventanas sin parar. Con una sesión, el mismo
        navegador recorre las cuentas una detrás de otra.

        Avisa del progreso antes de cada cuenta, porque esto tarda minutos.

        Devuelve `{handle: lista}` y, si una cuenta falla, `{handle: excepción}`
        para que un problema puntual no tumbe el resto.
        """
        ok, detail = self.playwright_available()
        if not ok:
            raise ProviderError(detail)

        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        profile = config.PROFILES_DIR / "x"
        profile.mkdir(parents=True, exist_ok=True)
        results: dict = {}
        total = len(handles)

        with _BROWSER_LOCK:
            try:
                with sync_playwright() as playwright:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile),
                        channel=self.settings.browser_channel or None,
                        headless=self.settings.browser_headless,
                        locale="es-ES",
                        viewport={"width": 1280, "height": 1000},
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                    try:
                        page = context.pages[0] if context.pages else context.new_page()
                        for position, handle in enumerate(handles, 1):
                            _notify(
                                on_progress,
                                {
                                    "provider": self.name,
                                    "handle": handle,
                                    "index": position,
                                    "total": total,
                                },
                            )
                            try:
                                results[handle] = self._read_handle(page, handle)
                            except ProviderError as exc:
                                results[handle] = exc
                            except (PlaywrightTimeout, PlaywrightError) as exc:
                                results[handle] = ProviderError(
                                    f"fallo del navegador en @{handle}: {str(exc)[:200]}"
                                )
                    finally:
                        context.close()
            except PlaywrightTimeout as exc:
                raise ProviderError(f"tiempo de espera agotado al abrir el navegador: {exc}") from exc
            except PlaywrightError as exc:
                raise ProviderError(f"no se pudo abrir el navegador: {str(exc)[:300]}") from exc
        return results

    def _read_handle(self, page, handle: str) -> list[dict]:
        """Extrae las publicaciones de una cuenta usando una página ya abierta."""
        url = f"https://x.com/{handle}"
        page.goto(url, wait_until="domcontentloaded", timeout=self.settings.browser_timeout_ms)
        page.wait_for_timeout(self.settings.browser_settle_ms)
        for _ in range(max(0, self.settings.browser_max_scrolls)):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(2500)
        raw = page.locator("article").evaluate_all(EXTRACT_JS)
        if _looks_blocked(page):
            raise ProviderError(
                "X pidió iniciar sesión o bloqueó la lectura. Abre el perfil "
                f"{config.PROFILES_DIR / 'x'} con Chrome, inicia sesión una vez y "
                "vuelve a intentarlo."
            )

        records: list[TweetRecord] = []
        seen: set[str] = set()
        for entry in raw or []:
            tweet_id = str(entry.get("status") or "").strip()
            if not tweet_id or tweet_id in seen:
                continue
            seen.add(tweet_id)
            author = (entry.get("author") or handle).strip()
            records.append(
                TweetRecord(
                    tweet_id=tweet_id,
                    source_handle=handle,
                    text=(entry.get("text") or "").strip() or None,
                    url=entry.get("permalink") or f"https://x.com/{author}/status/{tweet_id}",
                    author_handle=author,
                    # X ya no expone la fecha exacta en el DOM: solo la relativa.
                    posted_at=None,
                    relative_time=entry.get("relative_time"),
                    media=[src for src in (entry.get("media") or []) if src],
                )
            )
        if not records:
            raise ProviderError(
                f"no se encontró ninguna publicación en @{handle}; "
                "comprueba que la cuenta existe y es pública"
            )
        return [record.as_dict() for record in records]


# ----------------------------------------------------------------------
# API v2 de X (opcional)
# ----------------------------------------------------------------------
class XApiTimeline:
    """API v2 oficial. Requiere token de acceso (de pago)."""

    name = "xapi"

    def __init__(self, bearer: str | None = None) -> None:
        settings = config.Settings()
        self.bearer = (bearer if bearer is not None else settings.x_api_bearer).strip()

    def status(self) -> ProviderStatus:
        if not self.bearer:
            return ProviderStatus(
                self.name, False, "falta X_API_BEARER_TOKEN en dashboard/.env"
            )
        return ProviderStatus(self.name, True, "token configurado")

    def fetch(self, handle: str) -> list[dict]:
        if not self.bearer:
            raise ProviderError("falta X_API_BEARER_TOKEN")
        user = json.loads(
            _http_get(
                f"https://api.x.com/2/users/by/username/{handle}",
                bearer=self.bearer,
            ).decode("utf-8")
        )
        user_id = (user.get("data") or {}).get("id")
        if not user_id:
            raise ProviderError(f"la API de X no encontró al usuario @{handle}")
        payload = json.loads(
            _http_get(
                f"https://api.x.com/2/users/{user_id}/tweets"
                "?max_results=25&tweet.fields=created_at,text,attachments"
                "&expansions=attachments.media_keys&media.fields=url,preview_image_url",
                bearer=self.bearer,
            ).decode("utf-8")
        )
        media_by_key = {
            item["media_key"]: (item.get("url") or item.get("preview_image_url"))
            for item in ((payload.get("includes") or {}).get("media") or [])
        }
        records: list[TweetRecord] = []
        for item in payload.get("data") or []:
            keys = ((item.get("attachments") or {}).get("media_keys")) or []
            records.append(
                TweetRecord(
                    tweet_id=str(item["id"]),
                    source_handle=handle,
                    text=item.get("text"),
                    url=f"https://x.com/{handle}/status/{item['id']}",
                    author_handle=handle,
                    posted_at=item.get("created_at"),
                    media=[media_by_key[key] for key in keys if media_by_key.get(key)],
                )
            )
        return [record.as_dict() for record in records]


# ----------------------------------------------------------------------
# Registro con respaldo en cadena
# ----------------------------------------------------------------------
def build_provider(name: str):
    chosen = (name or "browser").strip().lower()
    if chosen == "browser":
        return BrowserTimeline()
    if chosen == "nitter":
        return NitterTimeline()
    if chosen == "xapi":
        return XApiTimeline()
    raise ProviderError(f"proveedor de descubrimiento desconocido: {chosen}")


def fallback_chain(preferred: str) -> list[str]:
    """Orden de intentos: el elegido primero, luego alternativas razonables."""
    order = [preferred]
    for candidate in ("browser", "nitter", "xapi"):
        if candidate not in order:
            order.append(candidate)
    return order


def fetch_timeline(handle: str, preferred: str | None = None) -> dict:
    """Obtiene la timeline de una cuenta intentando los proveedores en orden.

    Devuelve ``{"provider": ..., "tweets": [...], "attempts": [...]}``.
    """
    settings = config.Settings()
    first = (preferred or settings.timeline_provider or "browser").strip().lower()
    attempts: list[dict] = []
    for name in fallback_chain(first):
        provider = build_provider(name)
        status = provider.status()
        if not status.available:
            attempts.append({"provider": name, "ok": False, "detail": status.detail})
            continue
        try:
            tweets = provider.fetch(handle)
        except ProviderError as exc:
            attempts.append({"provider": name, "ok": False, "detail": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - un proveedor roto no debe tumbar el sondeo
            attempts.append({"provider": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
            continue
        return {"provider": name, "tweets": tweets, "attempts": attempts}
    detail = "; ".join(f"{a['provider']}: {a['detail']}" for a in attempts) or "sin proveedores"
    raise ProviderError(f"no se pudo leer @{handle} ({detail})")


def _notify(callback, payload: dict) -> None:
    """Avisa del progreso sin dejar que un fallo del aviso rompa el sondeo."""
    if callback is None:
        return
    try:
        callback(payload)
    except Exception:  # noqa: BLE001
        pass


def fetch_timelines_batch(
    handles: list[str],
    preferred: str | None = None,
    on_progress=None,
) -> dict:
    """Lee varias cuentas agrupando cada proveedor en **una sola pasada**.

    Dos cosas a la vez, que es lo que interesa:

    * El proveedor de navegador abre Chrome **una vez** para todas las cuentas;
      hacerlo por cuenta multiplicaría el tiempo y haría parpadear ventanas.
    * Si una cuenta concreta falla con un proveedor, se reintenta con el
      siguiente sin repetir las que ya salieron bien.

    `on_progress` recibe un aviso por cada cuenta que se empieza a leer: como
    el lote del navegador tarda minutos, sin esto la interfaz parece colgada.

    Devuelve ``{"provider": resumen, "results": {handle: lista|excepcion},
    "used": {handle: proveedor}, "attempts": [...]}``.
    """
    if not handles:
        return {"provider": None, "results": {}, "used": {}, "attempts": []}

    settings = config.Settings()
    first = (preferred or settings.timeline_provider or "browser").strip().lower()
    attempts: list[dict] = []
    results: dict = {handle: ProviderError("sin intentar") for handle in handles}
    used: dict = {}

    for name in fallback_chain(first):
        pending = [handle for handle in handles if isinstance(results.get(handle), Exception)]
        if not pending:
            break

        provider = build_provider(name)
        status = provider.status()
        if not status.available:
            attempts.append({"provider": name, "ok": False, "detail": status.detail})
            continue

        _notify(on_progress, {"provider": name, "phase": "starting", "total": len(pending)})
        try:
            if hasattr(provider, "fetch_many"):
                partial = provider.fetch_many(pending, on_progress=on_progress)
            else:
                partial = {}
                for position, handle in enumerate(pending, 1):
                    _notify(
                        on_progress,
                        {"provider": name, "handle": handle, "index": position, "total": len(pending)},
                    )
                    try:
                        partial[handle] = provider.fetch(handle)
                    except Exception as exc:  # noqa: BLE001
                        partial[handle] = exc
        except ProviderError as exc:
            attempts.append({"provider": name, "ok": False, "detail": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - un proveedor roto no debe tumbar el sondeo
            attempts.append(
                {"provider": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            )
            continue

        solved = 0
        for handle in pending:
            value = partial.get(handle)
            if value is None:
                continue
            results[handle] = value
            if isinstance(value, Exception):
                attempts.append({"provider": name, "handle": handle, "ok": False, "detail": str(value)[:200]})
            else:
                used[handle] = name
                solved += 1
        if not solved:
            attempts.append({"provider": name, "ok": False, "detail": "ninguna cuenta se pudo leer"})

    resolved = [handle for handle in handles if not isinstance(results[handle], Exception)]
    if not resolved:
        detail = "; ".join(
            f"{item['provider']}: {item.get('detail', '')[:80]}" for item in attempts[-4:]
        ) or "sin proveedores"
        raise ProviderError(f"no se pudo leer ninguna cuenta ({detail})")

    providers_used = sorted(set(used.values()))
    summary = providers_used[0] if len(providers_used) == 1 else "mixto:" + "+".join(providers_used)
    return {"provider": summary, "results": results, "used": used, "attempts": attempts}


def available_providers() -> list[ProviderStatus]:
    return [
        BrowserTimeline().status(),
        NitterTimeline().status(),
        XApiTimeline().status(),
    ]


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------
def _http_get(url: str, accept: str = "*/*", bearer: str | None = None, timeout: int = 30) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _text(element: ET.Element, tag: str) -> str | None:
    for child in element:
        if child.tag.split("}")[-1] == tag:
            return (child.text or "").strip() or None
    return None


def _html_to_text(markup: str) -> str:
    if not markup:
        return ""
    without_images = IMG_TAG_ALL.sub("", markup)
    with_breaks = BR_TAG.sub("\n", without_images)
    text = ANY_TAG.sub("", with_breaks)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _iso_from_rfc822(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _looks_blocked(page) -> bool:
    """Detecta el muro de inicio de sesión o un error de X."""
    try:
        body = (page.inner_text("body") or "")[:4000].lower()
    except Exception:  # noqa: BLE001
        return False
    markers = (
        "iniciar sesión o registrarse",
        "sign in to x",
        "something went wrong",
        "algo salió mal",
        "rate limit",
    )
    has_posts = False
    try:
        has_posts = page.locator("article").count() > 0
    except Exception:  # noqa: BLE001
        has_posts = False
    if has_posts:
        return False
    return any(marker in body for marker in markers)


def profile_directories() -> dict[str, str]:
    """Rutas de los perfiles persistentes del navegador."""
    return {
        "x": str(config.PROFILES_DIR / "x"),
        "chatgpt": str(config.PROFILES_DIR / "chatgpt"),
    }
