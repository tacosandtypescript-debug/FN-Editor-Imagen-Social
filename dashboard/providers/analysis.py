"""Proveedores de análisis editorial.

Convierten el texto de una publicación en los datos que necesita el
compositor: titular, contexto, caption y formato sugerido.

Tres implementaciones intercambiables:

* ``openai``   — cualquier endpoint compatible con OpenAI. Sirve tanto para la
  API oficial como para un modelo local (Ollama, LM Studio, vLLM), que es la
  vía gratuita y privada.
* ``chatgpt``  — conduce chatgpt.com en el navegador con la sesión ya iniciada
  del usuario. Es experimental: depende del DOM de una web ajena.
* ``manual``   — no usa IA; deja el texto original para redactarlo a mano.

Sea cual sea el proveedor, la salida pasa por `sanitize_analysis`, que aplica
las reglas del compositor canónico (sin palabras funcionales coloreadas y como
máximo dos colores de acento) para que el render no falle.
"""

from __future__ import annotations

import json
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .. import config
from ..pipeline import repo
from .base import Analysis, ProviderError, ProviderStatus

#: Paleta de reserva si el preset no se puede leer.
FALLBACK_PALETTE = ("#8B3DFF", "#FF7A00", "#E83DFF", "#FFD166")
DEFAULT_FORMATS = ("9:16", "1:1", "16:9", "4:5")
#: Relleno verificable cuando el modelo no propone cinco etiquetas.
PAD_HASHTAGS = ("#fortnite", "#fortnitebr", "#epicgames", "#gaming")
_BROWSER_LOCK = threading.Lock()

SYSTEM_PROMPT = """Eres el editor de tarjetas sociales de Fortnite del proyecto EditImg.
Recibes el texto de una publicación de X y devuelves el texto listo para
componer una tarjeta. Respondes SIEMPRE con un único objeto JSON válido, sin
texto alrededor y sin bloques de código.

Formato exacto de la respuesta:
{
  "top": "TITULAR {PALABRA|HEX}",
  "bottom": "CONTEXTO · DD/MM",
  "caption": "titulo y fecha y hashtags",
  "hashtags": ["#khetzalgg", "#...", "#...", "#...", "#..."],
  "suggested_format": "9:16",
  "reasoning": "una frase"
}

Reglas obligatorias:
- "top" es el titular: 3 a 12 palabras, en mayúsculas, sin punto final.
- "bottom" es el contexto: breve, puede incluir la fecha con formato · DD/MM.
- Para destacar una palabra escribe exactamente {PALABRA|HEX}.
- Usa como máximo DOS palabras resaltadas en total (una en "top", otra en "bottom").
- Colores permitidos, solo estos: __PALETTE__.
- NUNCA resaltes palabras funcionales: de, del, la, las, lo, los, el, un, una,
  unos, unas, a, al, ante, bajo, con, contra, desde, e, en, entre, hacia,
  hasta, o, u, para, por, que, se, sin, sobre, y.
- "hashtags": EXACTAMENTE cinco etiquetas únicas, en minúsculas, y una de ellas
  debe ser #khetzalgg.
- "suggested_format" debe ser uno de: __FORMATS__, según la orientación de las
  imágenes de la publicación.
- No inventes datos, cifras ni nombres que no aparezcan en la publicación.
- No incluyas hashtags dentro de "top" ni de "bottom".
- Escribe en español."""


def build_system_prompt() -> str:
    """Prompt del sistema con la paleta y los formatos resueltos.

    Se usa `replace` y no `str.format` a propósito: el prompt contiene un
    ejemplo JSON con llaves literales, que `format` interpretaría como campos
    y haría fallar la llamada.
    """
    return (
        SYSTEM_PROMPT
        .replace("__PALETTE__", ", ".join(palette_colors()))
        .replace("__FORMATS__", ", ".join(DEFAULT_FORMATS))
    )


class ManualAnalysis:
    """Sin IA: deja el texto del tweet para redactarlo en la interfaz."""

    name = "manual"

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.name, True, "sin IA: redactas tú el texto")

    def analyse(self, tweet: dict) -> Analysis:
        raw_text = (tweet.get("text") or "").strip()
        first_line = raw_text.splitlines()[0] if raw_text else "TITULAR PENDIENTE"
        title = first_line.upper()[:80] or "TITULAR PENDIENTE"
        return Analysis(
            top=title,
            bottom=date_context(tweet),
            caption=f"{title.title()} · {date_context(tweet)}",
            hashtags=["#khetzalgg"],
            suggested_format=None,
            reasoning="Sin análisis automático: texto preparado para edición manual.",
            provider=self.name,
        )


class OpenAICompatibleAnalysis:
    """Endpoint compatible con OpenAI (API oficial o modelo local)."""

    name = "openai"

    def __init__(self) -> None:
        self.settings = config.Settings()

    def status(self) -> ProviderStatus:
        base = (self.settings.openai_base_url or "").strip()
        if not base:
            return ProviderStatus(self.name, False, "falta DASHBOARD_OPENAI_BASE_URL")
        local = any(host in base for host in ("localhost", "127.0.0.1", "0.0.0.0"))
        if not self.settings.openai_api_key and not local:
            return ProviderStatus(
                self.name,
                False,
                "falta OPENAI_API_KEY (o apunta a un modelo local en localhost)",
            )
        return ProviderStatus(self.name, True, f"{base} · modelo {self.settings.openai_model}")

    def analyse(self, tweet: dict) -> Analysis:
        status = self.status()
        if not status.available:
            raise ProviderError(status.detail)

        palette = palette_colors()
        user_prompt = build_user_prompt(tweet, palette)
        body = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.4,
        }
        headers = {"Content-Type": "application/json"}
        if self.settings.openai_api_key:
            headers["Authorization"] = f"Bearer {self.settings.openai_api_key}"

        endpoint = self.settings.openai_base_url.rstrip("/") + "/chat/completions"
        request = Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.analysis_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = _read_error(exc)
            raise ProviderError(f"el proveedor de análisis respondió {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ProviderError(f"no se pudo contactar con el proveedor de análisis: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise ProviderError(f"fallo de red en el análisis: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("el proveedor de análisis devolvió algo ilegible") from exc

        content = _extract_content(payload)
        if not content:
            raise ProviderError("el proveedor de análisis no devolvió texto")
        return analysis_from_payload(content, provider=self.name)


class BrowserChatGPTAnalysis:
    """Conduce chatgpt.com con la sesión del navegador del usuario.

    Experimental. Depende del marcado de una web ajena, así que puede dejar de
    funcionar sin aviso; por eso nunca es el proveedor por defecto.
    """

    name = "chatgpt"
    PROMPT_SELECTOR = "#prompt-textarea, div[contenteditable='true']"
    SEND_SELECTOR = "[data-testid='send-button'], button[aria-label*='Enviar'], button[aria-label*='Send']"
    REPLY_SELECTOR = "[data-message-author-role='assistant']"

    def __init__(self) -> None:
        self.settings = config.Settings()

    def status(self) -> ProviderStatus:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return ProviderStatus(
                self.name, False, "requiere Playwright (requirements-dashboard.txt)"
            )
        profile = config.PROFILES_DIR / "chatgpt"
        return ProviderStatus(
            self.name,
            True,
            f"experimental: usa tu sesión en el perfil {profile}; inicia sesión una vez",
        )

    def analyse(self, tweet: dict) -> Analysis:
        if not self.status().available:
            raise ProviderError(self.status().detail)

        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        palette = palette_colors()
        prompt = (
            build_system_prompt()
            + "\n\n--- PUBLICACIÓN ---\n"
            + build_user_prompt(tweet, palette)
        )
        profile = config.PROFILES_DIR / "chatgpt"
        profile.mkdir(parents=True, exist_ok=True)

        with _BROWSER_LOCK:
            try:
                with sync_playwright() as playwright:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile),
                        channel=self.settings.browser_channel or None,
                        headless=False,
                        locale="es-ES",
                        viewport={"width": 1280, "height": 1000},
                    )
                    try:
                        page = context.pages[0] if context.pages else context.new_page()
                        page.goto(
                            "https://chatgpt.com/",
                            wait_until="domcontentloaded",
                            timeout=self.settings.browser_timeout_ms,
                        )
                        page.wait_for_timeout(4000)
                        box = page.locator(self.PROMPT_SELECTOR).first
                        try:
                            box.wait_for(timeout=15000)
                        except PlaywrightTimeout as exc:
                            raise ProviderError(
                                "chatgpt.com no mostró la caja de escritura: lo más "
                                "probable es que la sesión no esté iniciada en el perfil "
                                f"{profile}. Ábrelo una vez, inicia sesión y reinténtalo."
                            ) from exc
                        box.click()
                        box.fill(prompt) if _is_fillable(box) else box.type(prompt)
                        send = page.locator(self.SEND_SELECTOR).first
                        if send.count() and send.is_enabled():
                            send.click()
                        else:
                            page.keyboard.press("Enter")
                        reply = _wait_for_reply(page, self.REPLY_SELECTOR, self.settings.analysis_timeout_seconds)
                    finally:
                        context.close()
            except PlaywrightTimeout as exc:
                raise ProviderError(f"chatgpt.com no respondió a tiempo: {exc}") from exc
            except PlaywrightError as exc:
                raise ProviderError(
                    "el navegador no pudo completar el análisis en chatgpt.com: "
                    f"{str(exc)[:300]}. Comprueba que has iniciado sesión en el perfil "
                    f"{profile}."
                ) from exc

        if not reply:
            raise ProviderError(
                "no se pudo leer la respuesta de ChatGPT. Inicia sesión en el perfil "
                f"{profile} y vuelve a intentarlo."
            )
        return analysis_from_payload(reply, provider=self.name)


# ----------------------------------------------------------------------
# Construcción y saneado
# ----------------------------------------------------------------------
def palette_colors() -> tuple[str, ...]:
    """Colores de acento del preset, leídos del propio compositor."""
    try:
        composer = repo.compose_image()
        cfg = composer.load_cfg(config.PRESETS["9:16"])
        palette = cfg.get("palette") or {}
        colors = tuple(str(value).upper() for value in palette.values() if value)
        return colors or FALLBACK_PALETTE
    except Exception:  # noqa: BLE001 - la paleta nunca debe impedir el análisis
        return FALLBACK_PALETTE


def build_user_prompt(tweet: dict, palette: tuple[str, ...]) -> str:
    text = (tweet.get("text") or "").strip() or "(sin texto)"
    media = tweet.get("media") or []
    lines = [
        f"Autor: @{tweet.get('author_handle') or tweet.get('source_handle') or 'desconocido'}",
        f"Fecha: {tweet.get('posted_at') or tweet.get('relative_time') or 'desconocida'}",
        f"Enlace: {tweet.get('url') or ''}",
        f"Número de imágenes: {len(media)}",
        "",
        "Texto de la publicación:",
        text,
    ]
    return "\n".join(lines)


def sanitize_analysis(analysis: Analysis) -> Analysis:
    """Aplica el contrato del compositor a la propuesta del modelo.

    Nunca lanza: si algo no cumple, lo corrige. Así un modelo que se salte las
    reglas no rompe la generación de la tarjeta.
    """
    composer = repo.compose_image()
    seg = composer.SEG
    word_re = composer.HIGHLIGHT_WORD_RE
    function_words = composer.SPANISH_FUNCTION_WORDS
    palette = {color.upper() for color in palette_colors()}

    used_colors: list[str] = []

    def clean(text: str) -> str:
        value = str(text or "")

        def replace(match: re.Match) -> str:
            word = match.group(1)
            color = "#" + match.group(2).upper()
            words = [token.casefold() for token in word_re.findall(word)]
            if any(token in function_words for token in words):
                return word  # palabras funcionales: se quedan sin color
            snapped = _nearest_palette(color, palette)
            if snapped not in used_colors:
                if len(used_colors) >= 2:
                    return word  # más de dos acentos: se descarta el sobrante
                used_colors.append(snapped)
            return "{" + word + "|" + snapped.lstrip("#") + "}"

        return seg.sub(replace, value).strip()

    top = clean(analysis.top)
    bottom = clean(analysis.bottom)
    hashtags = normalise_hashtags(analysis.hashtags, analysis.caption)
    caption = (analysis.caption or "").strip() or f"{top} · {bottom}"
    # Los hashtags viven en el caption, nunca dentro de la imagen. El borrado
    # respeta límites de palabra: si no, quitar "#fortnite" mutilaría
    # "#fortnitemares" y dejaría "mares" suelto.
    for tag in hashtags:
        caption = re.sub(r"(?<!\w)" + re.escape(tag) + r"(?!\w)", "", caption, flags=re.IGNORECASE)
    caption = re.sub(r"\s{2,}", " ", caption).strip(" ·-")
    caption = (caption + " " + " ".join(hashtags)).strip()

    suggested = str(analysis.suggested_format or "").strip()
    if suggested not in DEFAULT_FORMATS:
        suggested = "9:16"

    return Analysis(
        top=top or "TITULAR PENDIENTE",
        bottom=bottom or "CONTEXTO PENDIENTE",
        caption=caption,
        hashtags=hashtags,
        suggested_format=suggested,
        suggested_style=analysis.suggested_style,
        reasoning=analysis.reasoning,
        provider=analysis.provider,
        raw=analysis.raw,
    )


def normalise_hashtags(hashtags, caption: str = "") -> list[str]:
    """Garantiza exactamente cinco etiquetas únicas, con #khetzalgg dentro.

    Si el modelo devuelve menos de cinco se completan con etiquetas genéricas
    verificables de Fortnite, que es el respaldo que ya documenta el
    repositorio cuando no se pueden confirmar tendencias del día.
    """
    found: list[str] = []
    for source in (hashtags or []), re.findall(r"#\w+", caption or ""):
        for raw in source:
            tag = "#" + str(raw).strip().lstrip("#").lower()
            tag = re.sub(r"[^\w#]", "", tag, flags=re.UNICODE)
            if len(tag) > 1 and tag not in found:
                found.append(tag)
    brand = "#khetzalgg"
    others = [tag for tag in found if tag != brand]
    for pad in PAD_HASHTAGS:
        if len(others) >= 4:
            break
        if pad != brand and pad not in others:
            others.append(pad)
    return [brand, *others[:4]]


def analysis_from_payload(content: str, provider: str) -> Analysis:
    """Extrae el JSON de la respuesta del modelo y lo sanea."""
    data = _first_json_object(content)
    if not isinstance(data, dict):
        raise ProviderError("el modelo no devolvió un objeto JSON reconocible")
    analysis = Analysis(
        top=str(data.get("top") or "").strip(),
        bottom=str(data.get("bottom") or "").strip(),
        caption=str(data.get("caption") or "").strip(),
        hashtags=list(data.get("hashtags") or []),
        suggested_format=str(data.get("suggested_format") or "").strip() or None,
        suggested_style=str(data.get("suggested_style") or "").strip() or None,
        reasoning=str(data.get("reasoning") or "").strip() or None,
        provider=provider,
        raw=content[:4000],
    )
    if not analysis.top or not analysis.bottom:
        raise ProviderError("el modelo no devolvió titular y contexto")
    return sanitize_analysis(analysis)


def suggest_format(tweet: dict) -> str:
    """Formato sugerido según la orientación declarada de los medios."""
    return "9:16"


def date_context(tweet: dict) -> str:
    posted = str(tweet.get("posted_at") or "").strip()
    if len(posted) >= 10 and posted[4] == "-" and posted[7] == "-":
        return f"NOTICIA FORTNITE · {posted[8:10]}/{posted[5:7]}"
    return "NOTICIA FORTNITE"


# ----------------------------------------------------------------------
# Registro
# ----------------------------------------------------------------------
def build_provider(name: str):
    chosen = (name or "openai").strip().lower()
    if chosen in {"openai", "api", "local"}:
        return OpenAICompatibleAnalysis()
    if chosen in {"chatgpt", "browser"}:
        return BrowserChatGPTAnalysis()
    if chosen == "manual":
        return ManualAnalysis()
    raise ProviderError(f"proveedor de análisis desconocido: {chosen}")


def analyse_tweet(tweet: dict, preferred: str | None = None) -> dict:
    """Analiza una publicación y devuelve el resultado saneado."""
    settings = config.Settings()
    chosen = (preferred or settings.analysis_provider or "openai").strip().lower()
    provider = build_provider(chosen)
    status = provider.status()
    if not status.available:
        if chosen == "manual":
            raise ProviderError(status.detail)
        # Un proveedor no disponible no debe bloquear: se cae a redacción manual.
        manual = ManualAnalysis()
        result = manual.analyse(tweet)
        result.reasoning = f"{chosen} no disponible ({status.detail}). {result.reasoning}"
        return sanitize_analysis(result).as_dict()
    return sanitize_analysis(provider.analyse(tweet)).as_dict()


def available_providers() -> list[ProviderStatus]:
    return [
        OpenAICompatibleAnalysis().status(),
        BrowserChatGPTAnalysis().status(),
        ManualAnalysis().status(),
    ]


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------
def _nearest_palette(color: str, palette: set[str]) -> str:
    """Ajusta un color al más parecido de la paleta del preset."""
    if not palette:
        return color
    if color.upper() in palette:
        return color.upper()
    try:
        target = _rgb(color)
    except ValueError:
        return sorted(palette)[0]
    best, best_distance = None, None
    for candidate in sorted(palette):
        try:
            distance = sum((a - b) ** 2 for a, b in zip(target, _rgb(candidate)))
        except ValueError:
            continue
        if best_distance is None or distance < best_distance:
            best, best_distance = candidate, distance
    return best or color.upper()


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError("color no válido")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _extract_content(payload: dict) -> str:
    """Lee el texto de una respuesta compatible con OpenAI."""
    try:
        choices = payload.get("choices") or []
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # algunos proveedores devuelven bloques
            return "\n".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if isinstance(content, str):
            return content
    except (AttributeError, IndexError, TypeError):
        pass
    return ""


def _first_json_object(text: str) -> dict | None:
    """Extrae el primer objeto JSON equilibrado de un texto."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None


def _read_error(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
        parsed = json.loads(body)
        error = parsed.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)[:300]
        return str(error or body)[:300]
    except Exception:  # noqa: BLE001
        return "sin detalle"


def _is_fillable(locator) -> bool:
    try:
        return locator.evaluate("el => el.tagName.toLowerCase() === 'textarea'")
    except Exception:  # noqa: BLE001
        return False


def _wait_for_reply(page, selector: str, timeout_seconds: int) -> str:
    """Espera a que ChatGPT termine de escribir y devuelve su respuesta."""
    import time

    deadline = time.time() + max(30, int(timeout_seconds))
    last_text = ""
    stable_rounds = 0
    while time.time() < deadline:
        try:
            nodes = page.locator(selector)
            count = nodes.count()
            if count:
                current = nodes.nth(count - 1).inner_text()
                if current and current == last_text:
                    stable_rounds += 1
                    if stable_rounds >= 2:
                        return current
                else:
                    stable_rounds = 0
                    last_text = current
        except Exception:  # noqa: BLE001 - la página puede estar repintándose
            pass
        page.wait_for_timeout(2000)
    return last_text
