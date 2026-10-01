"""Configuración del dashboard.

Todo se resuelve con la biblioteca estándar para no alterar
`requirements.txt`. Los valores se leen en este orden de prioridad:

1. variables de entorno reales,
2. archivo `dashboard/.env` (ignorado por Git),
3. valores por defecto de este módulo,
4. ajustes guardados en la base de datos (cambiables desde la interfaz).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = ROOT / "bin"
DASHBOARD_DIR = Path(__file__).resolve().parent
WEB_DIR = DASHBOARD_DIR / "web"
VAR_DIR = DASHBOARD_DIR / "var"
ENV_FILE = DASHBOARD_DIR / ".env"

DB_PATH = VAR_DIR / "dashboard.sqlite3"
MEDIA_DIR = VAR_DIR / "media"
PROFILES_DIR = VAR_DIR / "profiles"
LOGS_DIR = VAR_DIR / "logs"

#: Instancias Nitter conocidas. Son inestables por naturaleza: el proveedor
#: rota entre ellas y descarta las que fallan en tiempo de ejecución.
DEFAULT_NITTER_INSTANCES = (
    "https://nitter.kareem.one",
    "https://nitter.privacyredirect.com",
    "https://nitter.tiekoetter.com",
)


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Lee un `.env` sencillo (KEY=VALOR) sin dependencias externas."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _env(name: str, default: str = "") -> str:
    """Entorno real primero, luego `.env`, luego el valor por defecto."""
    value = os.environ.get(name)
    if value is not None and value != "":
        return value
    return _FILE_ENV.get(name, default)


_FILE_ENV = load_env_file()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "si", "sí", "on"}


def _env_optional_float(name: str) -> float | None:
    raw = _env(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _env(name, "").strip()
    if not raw:
        return default
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(parts) or default


@dataclass
class Settings:
    """Ajustes efectivos del dashboard."""

    host: str = field(default_factory=lambda: _env("DASHBOARD_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("DASHBOARD_PORT", 8765))
    #: Clave de acceso para cuando el dashboard se expone a la red local. Si
    #: se escucha fuera de localhost y no hay clave, se genera una sola vez y
    #: se guarda, para no dejar la herramienta abierta a cualquiera.
    access_token: str = field(default_factory=lambda: _env("DASHBOARD_ACCESS_TOKEN", ""))
    #: Las peticiones que llegan por el tailnet de Tailscale se aceptan sin
    #: clave: es una red privada y cifrada a la que solo se unen dispositivos
    #: ya autenticados, así que pedir un segundo secreto allí solo añade
    #: fricción (y el navegador del móvil acaba perdiendo la cookie). La clave
    #: se sigue exigiendo para el resto de la red local.
    trust_tailnet: bool = field(
        default_factory=lambda: _env_bool("DASHBOARD_TRUST_TAILNET", True)
    )

    # --- Hora y zona horaria -------------------------------------------
    #: Desfase del usuario respecto a UTC, en horas. Si se deja vacío se
    #: resuelve con la zona de abajo. Ejemplo para Quebec: -4 (verano).
    utc_offset_hours: float | None = field(
        default_factory=lambda: _env_optional_float("DASHBOARD_UTC_OFFSET")
    )
    #: Zona horaria IANA del usuario. Quebec usa America/Toronto.
    timezone_name: str = field(
        default_factory=lambda: _env("DASHBOARD_TIMEZONE", "America/Toronto")
    )

    # --- Descubrimiento de publicaciones -------------------------------
    #: `nitter` por defecto: medido en este equipo da 20 publicaciones por
    #: cuenta frente a las 5 del navegador sin sesión, y algo más rápido. El
    #: navegador queda como respaldo automático para las cuentas que Nitter no
    #: indexa.
    timeline_provider: str = field(
        default_factory=lambda: _env("DASHBOARD_TIMELINE_PROVIDER", "nitter")
    )
    poll_interval_seconds: int = field(
        default_factory=lambda: _env_int("DASHBOARD_POLL_INTERVAL", 900)
    )
    poll_on_start: bool = field(
        default_factory=lambda: _env_bool("DASHBOARD_POLL_ON_START", True)
    )
    #: Horas que una publicación permanece visible en la bandeja. Al pasar ese
    #: tiempo se borra junto con sus archivos, pero **el registro de «ya vista»
    #: se conserva**, así que no vuelve a aparecer como nueva.
    retention_hours: int = field(
        default_factory=lambda: _env_int("DASHBOARD_RETENTION_HOURS", 48)
    )
    #: Días durante los que se recuerda la huella del contenido para detectar
    #: la misma noticia republicada con otro identificador.
    duplicate_window_days: int = field(
        default_factory=lambda: _env_int("DASHBOARD_DUPLICATE_WINDOW_DAYS", 7)
    )
    #: Consultas de enriquecido por sondeo, sumando todas las cuentas. Cada
    #: una cuesta ~0,34 s y los mirrors limitan las peticiones.
    enrich_budget: int = field(
        default_factory=lambda: _env_int("DASHBOARD_ENRICH_BUDGET", 60)
    )
    nitter_instances: tuple[str, ...] = field(
        default_factory=lambda: _env_list("DASHBOARD_NITTER_INSTANCES", DEFAULT_NITTER_INSTANCES)
    )
    #: Registro público de instancias Nitter. Se consulta solo cuando fallan
    #: las configuradas, para no depender de un único dominio.
    nitter_registry_url: str = field(
        default_factory=lambda: _env(
            "DASHBOARD_NITTER_REGISTRY", "https://status.d420.de/api/v1/instances"
        )
    )
    nitter_discovery: bool = field(
        default_factory=lambda: _env_bool("DASHBOARD_NITTER_DISCOVERY", True)
    )
    #: Puntos mínimos de salud para aceptar una instancia descubierta.
    nitter_min_points: int = field(
        default_factory=lambda: _env_int("DASHBOARD_NITTER_MIN_POINTS", 40)
    )
    browser_headless: bool = field(
        default_factory=lambda: _env_bool("DASHBOARD_BROWSER_HEADLESS", False)
    )
    browser_channel: str = field(
        default_factory=lambda: _env("DASHBOARD_BROWSER_CHANNEL", "chrome")
    )
    browser_timeout_ms: int = field(
        default_factory=lambda: _env_int("DASHBOARD_BROWSER_TIMEOUT_MS", 45000)
    )
    browser_settle_ms: int = field(
        default_factory=lambda: _env_int("DASHBOARD_BROWSER_SETTLE_MS", 9000)
    )
    #: Cuánto esperar a que aparezcan los primeros artículos de una cuenta.
    browser_article_timeout_ms: int = field(
        default_factory=lambda: _env_int("DASHBOARD_BROWSER_ARTICLE_TIMEOUT_MS", 15000)
    )
    #: Cuánto esperar a que la lista crezca tras desplazar. Si no crece, se
    #: sigue adelante en vez de agotar el tiempo.
    browser_scroll_wait_ms: int = field(
        default_factory=lambda: _env_int("DASHBOARD_BROWSER_SCROLL_WAIT_MS", 3000)
    )
    browser_max_scrolls: int = field(
        default_factory=lambda: _env_int("DASHBOARD_BROWSER_MAX_SCROLLS", 2)
    )
    x_api_bearer: str = field(default_factory=lambda: _env("X_API_BEARER_TOKEN", ""))

    # --- Handoff a Telegram -------------------------------------------
    # La Fase 1 solo expone si existe una configuración válida. El adaptador
    # que entrega el enlace al flujo de Telegram se conecta en la Fase 2.
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID", ""))


def ensure_directories() -> None:
    """Crea el árbol de trabajo local (ignorado por Git)."""
    for path in (VAR_DIR, MEDIA_DIR, PROFILES_DIR, LOGS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def is_loopback(address: str) -> bool:
    """True si la dirección de origen es el propio equipo."""
    import ipaddress

    raw = str(address or "").split("%")[0].strip()
    if raw.lower() == "localhost":
        return True
    try:
        parsed = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if parsed.is_loopback:
        return True
    # Direcciones IPv4 mapeadas en IPv6 (::ffff:127.0.0.1).
    mapped = getattr(parsed, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


#: Rangos que usa Tailscale. Solo entran dispositivos del propio tailnet, que
#: ya han tenido que autenticarse para unirse.
TAILNET_V4 = "100.64.0.0/10"
TAILNET_V6 = "fd7a:115c:a1e0::/48"


def is_tailnet(address: str) -> bool:
    """True si la petición llega por la red privada de Tailscale."""
    import ipaddress

    raw = str(address or "").split("%")[0].strip()
    if not raw:
        return False
    try:
        parsed = ipaddress.ip_address(raw)
    except ValueError:
        return False
    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        parsed = mapped
    for network in (TAILNET_V4, TAILNET_V6):
        try:
            if parsed in ipaddress.ip_network(network):
                return True
        except ValueError:
            continue
    return False


def listen_addresses(host: str, port: int) -> list[str]:
    """Direcciones por las que se puede alcanzar el dashboard.

    Sirve para imprimir las URLs útiles al arrancar: en el propio equipo, en la
    red local y, si la hay, por Tailscale.
    """
    import socket

    addresses: list[str] = []
    if host in {"127.0.0.1", "localhost"}:
        return [f"http://127.0.0.1:{port}/"]

    candidates: set[str] = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # No se envía nada: solo sirve para saber qué interfaz saldría.
            probe.connect(("8.8.8.8", 80))
            candidates.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.add(info[4][0])
    except OSError:
        pass

    for address in sorted(addrs for addrs in candidates if not addrs.startswith("127.")):
        addresses.append(f"http://{address}:{port}/")
    addresses.append(f"http://127.0.0.1:{port}/")
    return addresses


def redacted(settings: Settings) -> dict:
    """Vista pública de los ajustes, sin exponer credenciales completas."""
    def mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 6:
            return "•••"
        return f"{value[:3]}•••{value[-2:]}"

    return {
        "host": settings.host,
        "port": settings.port,
        "access_token_set": bool(settings.access_token),
        "utc_offset_hours": settings.utc_offset_hours,
        "timezone_name": settings.timezone_name,
        "timeline_provider": settings.timeline_provider,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "poll_on_start": settings.poll_on_start,
        "retention_hours": settings.retention_hours,
        "duplicate_window_days": settings.duplicate_window_days,
        "enrich_budget": settings.enrich_budget,
        "nitter_instances": list(settings.nitter_instances),
        "nitter_discovery": settings.nitter_discovery,
        "nitter_min_points": settings.nitter_min_points,
        "browser_headless": settings.browser_headless,
        "browser_channel": settings.browser_channel,
        "x_api_bearer_set": bool(settings.x_api_bearer),
        "telegram_bot_token_set": bool(settings.telegram_bot_token),
        "telegram_chat_id_set": bool(settings.telegram_chat_id),
    }
