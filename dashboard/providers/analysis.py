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
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .. import config
from ..pipeline import cards as cards_pipeline
from ..pipeline import repo
from .base import Analysis, ProviderError, ProviderStatus

#: Paleta de reserva si el preset no se puede leer.
FALLBACK_PALETTE = ("#8B3DFF", "#FF7A00", "#E83DFF", "#FFD166")
DEFAULT_FORMATS = ("9:16", "1:1", "16:9")
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
  "bottom": "CONTEXTO BREVE",
  "caption": "titulo y hashtags",
  "hashtags": ["#khetzalgg", "#...", "#...", "#...", "#..."],
  "suggested_format": "9:16",
  "reasoning": "una frase"
}

Reglas obligatorias:
- "top" es el titular: 3 a 12 palabras, en mayúsculas, sin punto final, y como
  MÁXIMO 48 caracteres contando espacios.
- "bottom" es un contexto breve basado en la publicación, de como MÁXIMO 52
  caracteres contando espacios (unas 8 palabras). Son límites duros: el
  compositor rechaza el texto que no entra y la composición falla. No rellenes
  la plantilla con una etiqueta fija ni con una fecha inventada.
- COLOREA SIEMPRE LAS PALABRAS: resalta exactamente UNA palabra en "top" y UNA
  palabra en "bottom". No es opcional.
- Para resaltar una palabra, escríbela así: {PALABRA|HEX}
  Ejemplo de "top": FORTNITEMARES VUELVE CON {MAPA|FF7A00} NUEVO
- Colores permitidos, solo estos: __PALETTE__.
  Al escribir el marcado van **SIN la almohadilla**: {PALABRA|FF7A00}. Nunca
  {PALABRA|#FF7A00}: la almohadilla impide que el color se aplique y las llaves
  acaban impresas dentro de la imagen.
  Elige el color por contraste con el contenido; no repitas siempre el mismo.
- NUNCA resaltes palabras funcionales: de, del, la, las, lo, los, el, un, una,
  unos, unas, a, al, ante, bajo, con, contra, desde, e, en, entre, hacia,
  hasta, o, u, para, por, que, se, sin, sobre, y.
- No resaltes más de dos palabras en total.
- "hashtags": EXACTAMENTE cinco etiquetas únicas, en minúsculas, y una de ellas
  debe ser #khetzalgg.
- "suggested_format" debe ser uno de: __FORMATS__, según la orientación de las
  imágenes de la publicación.
- No inventes datos, cifras ni nombres que no aparezcan en la publicación.
- No incluyas hashtags dentro de "top" ni de "bottom".
- Escribe en español."""

PROPOSAL_SYSTEM_PROMPT = """Eres el editor de tarjetas sociales de Fortnite del proyecto EditImg.
Recibes el texto de una publicación de X y debes proponer tres alternativas
editoriales para que una persona elija antes de componer la tarjeta. Respondes
SIEMPRE con un único objeto JSON válido, sin texto alrededor y sin bloques de
código.

Formato exacto de la respuesta:
{
  "options": [
    {"top": "TITULAR {PALABRA|HEX}", "bottom": "CONTEXTO BREVE"},
    {"top": "OTRO TITULAR {PALABRA|HEX}", "bottom": "OTRO CONTEXTO"},
    {"top": "TERCER TITULAR {PALABRA|HEX}", "bottom": "TERCER CONTEXTO"}
  ],
  "caption": "titulo y hashtags",
  "hashtags": ["#khetzalgg", "#...", "#...", "#...", "#..."],
  "suggested_format": "9:16",
  "reasoning": "una frase"
}

Reglas obligatorias:
- "options" debe tener EXACTAMENTE tres objetos distintos. Cada objeto es un
  par independiente de texto superior e inferior; no mezcles textos entre
  opciones.
- "top" es un titular de 3 a 12 palabras, en mayúsculas, sin punto final y de
  como MÁXIMO 48 caracteres contando espacios.
- "bottom" es un contexto basado en la publicación, de como MÁXIMO 52 caracteres
  contando espacios (unas 8 palabras). Son límites duros: si te pasas, la
  composición falla y la opción se descarta. No uses etiquetas fijas de
  plantilla ni fechas inventadas.
- Las tres opciones deben cambiar el enfoque o las palabras, sin inventar datos,
  cifras ni nombres que no estén en la publicación.
- COLOREA exactamente UNA palabra informativa en "top" y UNA en "bottom" de
  cada opción, usando {PALABRA|HEX}. No colorees palabras funcionales.
- Colores permitidos, solo estos: __PALETTE__.
  Al escribir el marcado van **SIN la almohadilla**: {PALABRA|FF7A00}. Nunca
  {PALABRA|#FF7A00}: la almohadilla impide que el color se aplique y las llaves
  acaban impresas dentro de la imagen.
- No incluyas hashtags dentro de "top" ni de "bottom".
- "hashtags" debe contener exactamente cinco etiquetas únicas en minúsculas y
  una debe ser #khetzalgg.
- "suggested_format" debe ser uno de: __FORMATS__.
- Escribe en español."""


def _resolve_prompt(template: str) -> str:
    """Resuelve marcadores sin interpretar las llaves JSON del prompt."""
    return (
        template
        .replace("__PALETTE__", ", ".join(palette_colors()))
        .replace("__FORMATS__", ", ".join(DEFAULT_FORMATS))
    )


def build_system_prompt() -> str:
    """Prompt del sistema para una sola propuesta, ya resuelto."""
    return _resolve_prompt(SYSTEM_PROMPT)


def build_proposal_system_prompt() -> str:
    """Prompt del sistema para las tres propuestas previas a la composición."""
    return _resolve_prompt(PROPOSAL_SYSTEM_PROMPT)


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
        # Al regenerar se sube la temperatura: si no, el modelo tiende a
        # devolver exactamente el mismo texto que ya no gustaba.
        regenerating = bool(tweet.get("previous"))
        body = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.95 if regenerating else 0.4,
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
    PROMPT_SELECTOR = "#prompt-textarea, div[contenteditable='true'], textarea[placeholder]"
    SEND_SELECTOR = (
        "[data-testid='send-button'], button[data-testid='composer-send-button'], "
        "button[aria-label*='Enviar'], button[aria-label*='Send'], button[aria-label*='enviar']"
    )
    REPLY_SELECTOR = "[data-message-author-role='assistant']"
    URL = "https://chatgpt.com/"

    def __init__(self) -> None:
        self.settings = config.Settings()

    @property
    def profile(self) -> Path:
        return config.PROFILES_DIR / "chatgpt"

    def status(self) -> ProviderStatus:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return ProviderStatus(
                self.name, False, "requiere Playwright (requirements-dashboard.txt)"
            )
        if self.profile_logged_in() is False:
            return ProviderStatus(
                self.name,
                False,
                "falta iniciar sesión en el perfil de ChatGPT; usa el botón "
                "«Abrir ventana de ChatGPT»",
            )
        return ProviderStatus(
            self.name,
            True,
            "usa tu propia sesión de chatgpt.com (consume tu suscripción, no la API)",
        )

    # -- sesión ---------------------------------------------------------
    def profile_logged_in(self) -> bool | None:
        """Marca si el perfil parece tener sesión iniciada.

        Devuelve `None` cuando todavía no se puede saber (nunca se ha abierto).
        """
        marker = self.profile / "Default" / "Cookies"
        if not self.profile.exists():
            return None
        return marker.exists()

    def open_login_window(self, url: str | None = None) -> dict:
        """Abre Chrome con el perfil para iniciar sesión a mano, sin bloquear.

        El proceso se lanza suelto: la ventana queda abierta para que el
        usuario entre con su cuenta y el dashboard la reutiliza después.
        """
        import subprocess

        ok, detail = self.status_playwright()
        if not ok:
            raise ProviderError(detail)

        from playwright.sync_api import sync_playwright

        profile = self.profile
        profile.mkdir(parents=True, exist_ok=True)
        target = url or self.URL
        # Se abre con el binario de Chrome y `--user-data-dir` para que la
        # ventana sobreviva a esta petición.
        try:
            from playwright.sync_api import sync_playwright as _sync

            with _sync() as playwright:
                executable = playwright.chromium.executable_path
        except Exception:  # noqa: BLE001
            executable = None

        command = [
            self.settings.browser_channel or "chrome",
        ]
        if executable and not Path(str(executable)).name.lower().startswith("chrome"):
            command = [str(executable)]
        command += [f"--user-data-dir={profile}", "--new-window", target]
        try:
            subprocess.Popen(command, close_fds=True)  # noqa: S603 - ruta controlada
        except OSError as exc:
            raise ProviderError(
                f"no se pudo abrir Chrome para iniciar sesión: {exc}. "
                f"Abre manualmente el perfil: {profile}"
            ) from exc
        return {"opened": True, "url": target, "profile": str(profile)}

    @staticmethod
    def status_playwright() -> tuple[bool, str]:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False, "Playwright no está instalado (requirements-dashboard.txt)"
        return True, "ok"

    # -- análisis -------------------------------------------------------
    def analyse(self, tweet: dict) -> Analysis:
        ok, detail = self.status_playwright()
        if not ok:
            raise ProviderError(detail)

        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        palette = palette_colors()
        prompt = (
            build_system_prompt()
            + "\n\n--- PUBLICACIÓN ---\n"
            + build_user_prompt(tweet, palette)
        )
        profile = self.profile
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
                            self.URL, wait_until="domcontentloaded",
                            timeout=self.settings.browser_timeout_ms,
                        )
                        page.wait_for_timeout(4500)

                        if _chatgpt_asks_for_login(page):
                            raise ProviderError(self._login_hint(profile))

                        box = page.locator(self.PROMPT_SELECTOR).first
                        try:
                            box.wait_for(timeout=20000)
                        except PlaywrightTimeout as exc:
                            raise ProviderError(self._login_hint(profile)) from exc

                        # Se cuenta lo que ya había antes de enviar: con un
                        # perfil persistente puede haber conversaciones
                        # anteriores y no queremos leer una respuesta vieja.
                        before = _assistant_count(page, self.REPLY_SELECTOR)

                        _write_prompt(page, box, prompt)
                        if not _send(page, self.SEND_SELECTOR):
                            raise ProviderError(
                                "no se pudo enviar el mensaje en chatgpt.com; "
                                "la interfaz puede haber cambiado"
                            )
                        reply = _wait_for_new_reply(
                            page,
                            self.REPLY_SELECTOR,
                            before,
                            self.settings.analysis_timeout_seconds,
                        )
                    finally:
                        context.close()
            except ProviderError:
                raise
            except PlaywrightTimeout as exc:
                raise ProviderError(f"chatgpt.com no respondió a tiempo: {exc}") from exc
            except PlaywrightError as exc:
                raise ProviderError(
                    "el navegador no pudo completar el análisis en chatgpt.com: "
                    f"{str(exc)[:300]}. {self._login_hint(profile)}"
                ) from exc

        if not reply:
            raise ProviderError(
                "ChatGPT no devolvió respuesta en el tiempo esperado. "
                f"{self._login_hint(profile)}"
            )
        return analysis_from_payload(reply, provider=self.name)

    def _login_hint(self, profile: Path) -> str:
        return (
            "Parece que no hay sesión iniciada en el perfil de ChatGPT. Pulsa "
            "«Abrir ventana de ChatGPT» en la pestaña Estado y ajustes, entra "
            f"con tu cuenta y vuelve a intentarlo (perfil: {profile})."
        )


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

    instructions = str(tweet.get("instructions") or "").strip()
    if instructions:
        lines += [
            "",
            "INDICACIÓN DEL USUARIO (tiene prioridad sobre el estilo por defecto):",
            instructions,
        ]

    previous = tweet.get("previous") or {}
    if isinstance(previous, dict) and any(str(value).strip() for value in previous.values()):
        lines += [
            "",
            "Esta es la versión anterior, que NO ha gustado. Propón otra distinta:",
            f"  titular anterior: {str(previous.get('top') or '').strip()}",
            f"  texto inferior anterior: {str(previous.get('bottom') or '').strip()}",
            f"  caption anterior: {str(previous.get('caption') or '').strip()}",
            "",
            "No repitas el mismo titular ni el mismo enfoque: cambia el ángulo, "
            "las palabras o el orden, manteniendo los datos reales de la publicación.",
        ]

    previous_options = tweet.get("previous_options") or []
    if isinstance(previous_options, list) and previous_options:
        lines += [
            "",
            "Estas opciones ya se mostraron y no convencieron. No repitas ninguno "
            "de sus pares; genera tres pares nuevos:",
        ]
        for index, option in enumerate(previous_options[:6], 1):
            if isinstance(option, dict):
                lines.append(
                    f"  opción {index}: {str(option.get('top') or '').strip()} / "
                    f"{str(option.get('bottom') or '').strip()}"
                )
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
        # Se quita la almohadilla **antes** de buscar el marcado: `SEG` exige
        # seis dígitos hex sin ella, así que `{PALABRA|#RRGGBB}` no coincidía,
        # no se coloreaba y las llaves acababan dibujadas en la tarjeta.
        value = cards_pipeline.normalise_markup(text)

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


def analysis_from_dict(data: dict, provider: str = "") -> Analysis:
    """Construye y sanea una propuesta recibida como diccionario."""
    if not isinstance(data, dict):
        raise ProviderError("la propuesta no es un objeto JSON")
    analysis = Analysis(
        top=str(data.get("top") or "").strip(),
        bottom=str(data.get("bottom") or "").strip(),
        caption=str(data.get("caption") or "").strip(),
        hashtags=list(data.get("hashtags") or []),
        suggested_format=str(data.get("suggested_format") or "").strip() or None,
        suggested_style=str(data.get("suggested_style") or "").strip() or None,
        reasoning=str(data.get("reasoning") or "").strip() or None,
        provider=str(data.get("provider") or provider or "").strip(),
        raw=str(data.get("raw") or "")[:4000] or None,
    )
    if not analysis.top or not analysis.bottom:
        raise ProviderError("el modelo no devolvió titular y contexto")
    return sanitize_analysis(analysis)


def analysis_from_payload(content: str, provider: str) -> Analysis:
    """Extrae el JSON de la respuesta del modelo y lo sanea."""
    data = _first_json_object(content)
    if not isinstance(data, dict):
        raise ProviderError("el modelo no devolvió un objeto JSON reconocible")
    return analysis_from_dict({**data, "raw": content}, provider=provider)


def proposals_from_payload(content: str, provider: str) -> dict:
    """Extrae exactamente tres pares de textos y sanea cada uno."""
    data = _first_json_object(content)
    if not isinstance(data, dict):
        raise ProviderError("el CLI no devolvió un objeto JSON reconocible")
    raw_options = data.get("options")
    if not isinstance(raw_options, list) or len(raw_options) != 3:
        raise ProviderError("el CLI debe devolver exactamente tres opciones")

    common = {
        "caption": str(data.get("caption") or "").strip(),
        "hashtags": list(data.get("hashtags") or []),
        "suggested_format": str(data.get("suggested_format") or "").strip() or None,
        "suggested_style": str(data.get("suggested_style") or "").strip() or None,
        "reasoning": str(data.get("reasoning") or "").strip() or None,
    }
    options: list[dict] = []
    pairs: set[tuple[str, str]] = set()
    for raw_option in raw_options:
        if not isinstance(raw_option, dict):
            raise ProviderError("cada opción del CLI debe ser un objeto JSON")
        option_data = {**common, **raw_option, "provider": provider, "raw": content}
        analysis = analysis_from_dict(option_data, provider=provider)
        pair = (analysis.top, analysis.bottom)
        if pair in pairs:
            raise ProviderError("el CLI devolvió opciones repetidas; deben ser tres distintas")
        pairs.add(pair)
        options.append(analysis.as_dict())

    return {
        "options": options,
        "caption": options[0]["caption"],
        "hashtags": options[0]["hashtags"],
        "suggested_format": options[0]["suggested_format"],
        "suggested_style": options[0].get("suggested_style"),
        "reasoning": common["reasoning"],
        "provider": provider,
        "raw": content[:4000],
    }


def suggest_format(tweet: dict) -> str:
    """Formato sugerido según la orientación declarada de los medios."""
    return "9:16"


def date_context(tweet: dict) -> str:
    """Fallback neutro: la fecha solo aparece si la propone el modelo."""
    return "CONTEXTO PENDIENTE"


# ----------------------------------------------------------------------
# Registro
# ----------------------------------------------------------------------
def build_provider(name: str):
    chosen = (name or "codex").strip().lower()
    if chosen in {"codex", "cli"}:
        from .codex_cli import CodexCliAnalysis

        return CodexCliAnalysis()
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
    chosen = (preferred or settings.analysis_provider or "codex").strip().lower()
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


def propose_tweet(tweet: dict, preferred: str | None = None) -> dict:
    """Genera tres pares para elegir antes de componer.

    Este camino es deliberadamente estricto: si el proveedor elegido no puede
    devolver tres propuestas, se informa del error en vez de componer una
    tarjeta silenciosamente con un texto de relleno.
    """
    settings = config.Settings()
    chosen = (preferred or settings.analysis_provider or "codex").strip().lower()
    provider = build_provider(chosen)
    status = provider.status()
    if not status.available:
        raise ProviderError(status.detail)
    propose = getattr(provider, "propose", None)
    if not callable(propose):
        raise ProviderError(
            f"el proveedor {chosen} no admite tres propuestas; usa el CLI de Codex"
        )
    result = propose(tweet)
    if not isinstance(result, dict) or len(result.get("options") or []) != 3:
        raise ProviderError("el proveedor no devolvió exactamente tres opciones")
    return result


def available_providers() -> list[ProviderStatus]:
    from .codex_cli import CodexCliAnalysis

    return [
        CodexCliAnalysis().status(),
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


def _chatgpt_asks_for_login(page) -> bool:
    """Detecta la pantalla de acceso para dar un mensaje útil en vez de un timeout."""
    try:
        url = (page.url or "").lower()
    except Exception:  # noqa: BLE001
        url = ""
    if "/auth/" in url or "login" in url:
        return True
    try:
        if page.locator(BrowserChatGPTAnalysis.PROMPT_SELECTOR).count() > 0:
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        body = (page.inner_text("body") or "")[:3000].lower()
    except Exception:  # noqa: BLE001
        return False
    markers = ("log in", "iniciar sesión", "sign up", "crear una cuenta", "regístrate")
    return any(marker in body for marker in markers)


def _assistant_count(page, selector: str) -> int:
    """Cuántas respuestas del asistente hay ya en la página."""
    try:
        return page.locator(selector).count()
    except Exception:  # noqa: BLE001
        return 0


def _write_prompt(page, box, prompt: str) -> None:
    """Escribe el prompt en la caja de chatgpt.com.

    `fill` es lo rápido y dispara los eventos que React necesita; si no deja
    texto, se recurre a `insert_text`, que también los dispara, y solo como
    último recurso se teclea carácter a carácter.
    """
    box.click()
    try:
        box.fill(prompt)
    except Exception:  # noqa: BLE001 - algunos editores no admiten fill
        pass

    written = ""
    try:
        written = (box.inner_text() or "").strip()
    except Exception:  # noqa: BLE001
        written = ""
    if len(written) < 10:
        try:
            page.keyboard.insert_text(prompt)
        except Exception:  # noqa: BLE001
            box.type(prompt)


def _send(page, selector: str) -> bool:
    """Pulsa enviar; si el botón no está disponible, usa Intro."""
    try:
        button = page.locator(selector).first
        if button.count():
            for _ in range(20):
                if button.is_enabled():
                    button.click()
                    return True
                page.wait_for_timeout(300)
    except Exception:  # noqa: BLE001 - se intenta con Intro
        pass
    try:
        page.keyboard.press("Enter")
        return True
    except Exception:  # noqa: BLE001
        return False


def _wait_for_new_reply(page, selector: str, known_count: int, timeout_seconds: int) -> str:
    """Espera una respuesta **nueva** y la devuelve cuando deja de cambiar.

    Se compara con las respuestas que ya había: con un perfil persistente la
    conversación anterior sigue en pantalla y no se debe leer esa.
    """
    import time

    deadline = time.time() + max(60, int(timeout_seconds))
    last_text = ""
    stable_rounds = 0
    while time.time() < deadline:
        try:
            nodes = page.locator(selector)
            count = nodes.count()
            if count > known_count:
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
