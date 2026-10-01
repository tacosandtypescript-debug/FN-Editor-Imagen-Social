"""Proveedor de análisis usando el CLI de Codex ya instalado en el equipo.

Ventaja frente a conducir `chatgpt.com` en un navegador: el CLI es una
herramienta oficial, no depende del marcado de una web y admite
`--output-schema`, así que el modelo devuelve **JSON válido garantizado** en
lugar de texto que hay que interpretar.

La autenticación es la que ya tenga el CLI. Si está en modo `chatgpt`, consume
la suscripción del usuario y no una clave de API de pago.

Se invoca en modo no interactivo y con el sandbox en solo lectura, de forma que
el modelo no pueda tocar archivos del equipo:

    codex exec --sandbox read-only --ephemeral --skip-git-repo-check \
      -C <carpeta temporal> -o <salida.json> --output-schema <esquema.json> <prompt>
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .. import config
from .base import Analysis, ProviderError, ProviderStatus

#: Forma exacta que se le exige a la respuesta del modelo.
#:
#: Con `additionalProperties: false`, el modo estricto de OpenAI exige que
#: **todas** las propiedades figuren en `required`; si falta alguna, la petición
#: se rechaza con `invalid_json_schema`. Por eso van las seis, aunque el modelo
#: pueda devolverlas vacías.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "top": {"type": "string"},
        "bottom": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "suggested_format": {"type": "string"},
        "reasoning": {"type": "string"},
    },
    "required": [
        "top",
        "bottom",
        "caption",
        "hashtags",
        "suggested_format",
        "reasoning",
    ],
    "additionalProperties": False,
}

#: Contrato estricto para el paso previo a la composición.
PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "options": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "top": {"type": "string"},
                    "bottom": {"type": "string"},
                },
                "required": ["top", "bottom"],
                "additionalProperties": False,
            },
        },
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "suggested_format": {"type": "string"},
        "reasoning": {"type": "string"},
    },
    "required": [
        "options",
        "caption",
        "hashtags",
        "suggested_format",
        "reasoning",
    ],
    "additionalProperties": False,
}

#: Rutas donde el instalador de Codex deja el ejecutable.
_SEARCH_GLOBS = (
    r"%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe",
    r"%LOCALAPPDATA%\Programs\codex\codex.exe",
    r"%APPDATA%\npm\codex.cmd",
)


def candidate_paths() -> list[str]:
    """Rutas candidatas al ejecutable de Codex, la más nueva primero.

    Si se ha indicado una ruta a mano, manda esa y no se busca nada más: es lo
    predecible, y evita que un ajuste equivocado use otra instalación por
    sorpresa.
    """
    override = config.Settings().codex_path.strip()
    if override:
        return [override] if Path(override).is_file() else []

    candidates: list[Path] = []

    found = shutil.which("codex")
    if found:
        candidates.append(Path(found))
    found_cmd = shutil.which("codex.cmd")
    if found_cmd:
        candidates.append(Path(found_cmd))

    for pattern in _SEARCH_GLOBS:
        expanded = os.path.expandvars(pattern)
        for match in glob.glob(expanded):
            candidates.append(Path(match))

    # Un mismo ejecutable puede aparecer varias veces; se ordena por fecha de
    # modificación para preferir la instalación más reciente.
    unique: dict[str, Path] = {}
    for path in candidates:
        try:
            if path.is_file():
                unique[str(path)] = path
        except OSError:
            continue
    return [
        str(path)
        for path in sorted(
            unique.values(),
            key=lambda item: item.stat().st_mtime if item.exists() else 0,
            reverse=True,
        )
    ]


def find_codex_executable() -> str | None:
    paths = candidate_paths()
    return paths[0] if paths else None


def auth_mode() -> str | None:
    """Modo de autenticación del CLI: `chatgpt` (suscripción) o `apikey`."""
    auth_file = Path(os.path.expanduser("~")) / ".codex" / "auth.json"
    if not auth_file.is_file():
        return None
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    mode = str(data.get("auth_mode") or "").strip().lower()
    if not mode:
        return "apikey" if data.get("OPENAI_API_KEY") else None
    return mode


class CodexCliAnalysis:
    """Analiza la publicación con `codex exec` en modo no interactivo."""

    name = "codex"

    def __init__(self) -> None:
        self.settings = config.Settings()

    # ------------------------------------------------------------------
    def status(self) -> ProviderStatus:
        executable = find_codex_executable()
        if not executable:
            override = self.settings.codex_path.strip()
            if override:
                detail = f"la ruta de DASHBOARD_CODEX_PATH no existe: {override}"
            else:
                detail = (
                    "no se encontró el CLI de Codex; indícalo en DASHBOARD_CODEX_PATH"
                )
            return ProviderStatus(self.name, False, detail)
        mode = auth_mode()
        if mode is None:
            return ProviderStatus(
                self.name, False, "el CLI de Codex no tiene sesión: ejecuta `codex login`"
            )
        subscription = "suscripción de ChatGPT" if mode == "chatgpt" else f"modo {mode}"
        return ProviderStatus(
            self.name, True, f"CLI de Codex ({subscription}) · {Path(executable).parent.name}"
        )

    # ------------------------------------------------------------------
    def analyse(self, tweet: dict) -> Analysis:
        """Obtiene una sola propuesta para los caminos antiguos del editor."""
        from . import analysis as analysis_module

        prompt = (
            analysis_module.build_system_prompt()
            + "\n\n--- PUBLICACIÓN ---\n"
            + analysis_module.build_user_prompt(tweet, analysis_module.palette_colors())
        )
        result, elapsed = self._run_json(
            prompt, OUTPUT_SCHEMA, self._parse, "análisis"
        )
        if result.reasoning:
            result.reasoning = f"{result.reasoning} · {elapsed:.0f} s"
        else:
            result.reasoning = f"CLI de Codex en {elapsed:.0f} s"
        return result

    def propose(self, tweet: dict) -> dict:
        """Obtiene tres pares para que el usuario elija antes de componer."""
        from . import analysis as analysis_module

        prompt = (
            analysis_module.build_proposal_system_prompt()
            + "\n\n--- PUBLICACIÓN ---\n"
            + analysis_module.build_user_prompt(tweet, analysis_module.palette_colors())
            + "\n\nDevuelve exactamente tres opciones diferentes."
        )
        result, elapsed = self._run_json(
            prompt,
            PROPOSAL_SCHEMA,
            lambda raw: analysis_module.proposals_from_payload(raw, self.name),
            "propuestas",
        )
        result["reasoning"] = (
            f"{result.get('reasoning') or 'Tres opciones listas'} · {elapsed:.0f} s"
        )
        return result

    def _run_json(self, prompt, schema, parser, label: str):
        """Ejecuta Codex y reintenta una vez si la salida no es JSON válido."""
        status = self.status()
        if not status.available:
            raise ProviderError(status.detail)
        executable = find_codex_executable()
        assert executable is not None  # lo garantiza status()

        # `ignore_cleanup_errors` porque en Windows el proceso de Codex puede
        # dejar un manejador abierto sobre su directorio de trabajo y el borrado
        # falla: no tiene sentido que eso tumbe un análisis ya completado.
        with tempfile.TemporaryDirectory(
            prefix="editimg-codex-", ignore_cleanup_errors=True
        ) as temporary:
            work = Path(temporary)
            schema_file = work / "schema.json"
            output_file = work / "salida.json"
            schema_file.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            started = time.time()
            ultimo_error: ProviderError | None = None

            for attempt in range(2):
                output_file.unlink(missing_ok=True)
                command = [executable, "exec"]
                command += ["--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check"]
                command += ["-C", str(work)]
                command += ["-o", str(output_file), "--output-schema", str(schema_file)]
                if self.settings.codex_model:
                    command += ["--model", self.settings.codex_model]
                actual_prompt = prompt if attempt == 0 else prompt + (
                    "\n\nREINTENTO: la respuesta anterior no era un JSON válido. "
                    "Responde ahora únicamente con el objeto JSON exacto del esquema, "
                    "sin markdown ni texto adicional."
                )
                command.append(actual_prompt)

                try:
                    completed = subprocess.run(  # noqa: S603 - ejecutable del usuario
                        command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=self.settings.codex_timeout_seconds,
                        cwd=str(work),
                        creationflags=_no_window_flags(),
                    )
                except subprocess.TimeoutExpired as exc:
                    raise ProviderError(
                        f"el CLI de Codex tardó más de {self.settings.codex_timeout_seconds} s; "
                        "aumenta DASHBOARD_CODEX_TIMEOUT"
                    ) from exc
                except OSError as exc:
                    raise ProviderError(f"no se pudo ejecutar el CLI de Codex: {exc}") from exc

                raw = ""
                if output_file.is_file():
                    raw = output_file.read_text(encoding="utf-8", errors="replace").strip()
                if completed.returncode != 0 and not raw:
                    raise ProviderError(
                        "el CLI de Codex falló: "
                        + _describe_failure(completed.stderr, completed.stdout, completed.returncode)
                    )
                if not raw:
                    raise ProviderError("el CLI de Codex no devolvió ningún mensaje final")

                try:
                    return parser(raw), time.time() - started
                except ProviderError as exc:
                    # El fallo se guarda y se reintenta. Se informa al final, ya
                    # con el resultado del reintento: si el modelo acertó a la
                    # segunda, el usuario no debe ver ningún error.
                    ultimo_error = exc

            raise ProviderError(
                f"el CLI de Codex devolvió JSON inválido para {label}; "
                f"se reintentó una vez: {ultimo_error}"
            ) from ultimo_error

    # ------------------------------------------------------------------
    def _parse(self, raw: str) -> Analysis:
        from . import analysis as analysis_module

        return analysis_module.analysis_from_payload(raw, provider=self.name)


def _no_window_flags() -> int:
    """Evita que aparezca una consola parpadeando en Windows."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _describe_failure(stderr: str, stdout: str, returncode: int) -> str:
    """Extrae el motivo real de un fallo del CLI.

    El CLI escribe un bloque `ERROR: {json}` en la salida de error, pero sus
    últimas líneas suelen ser solo la llave de cierre. Quedarse con la última
    línea daría mensajes inútiles como «}», así que se busca el mensaje del
    error estructurado y, si no aparece, se resume el bloque.
    """
    text = (stderr or "").strip() or (stdout or "").strip()
    if not text:
        return f"código de salida {returncode} sin más información"

    # Mensaje dentro del JSON de error del CLI.
    for match in re.finditer(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', text):
        try:
            message = json.loads('"' + match.group(1) + '"')
        except json.JSONDecodeError:
            message = match.group(1)
        if message.strip():
            return message.strip()[:400]

    marker = text.find("ERROR:")
    if marker != -1:
        block = text[marker:]
        flattened = " ".join(block.split())
        return flattened[:400]

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return f"código de salida {returncode} sin más información"

    last = lines[-1]
    # Una línea hecha solo de llaves o signos no explica nada: es el cierre del
    # bloque de error del CLI y quedarse con ella daría mensajes como «}».
    if len(last) < 3 or not any(char.isalnum() for char in last):
        return (
            f"código de salida {returncode}; el CLI no dio un mensaje claro "
            f"(última línea: {last[:60]!r})"
        )
    return last[:400]
