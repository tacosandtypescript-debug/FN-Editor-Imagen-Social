"""Servidor HTTP del dashboard (biblioteca estándar, sin dependencias).

Sirve la interfaz y una API JSON. No sustituye a nada del repositorio: solo
llama a la capa de servicio, que a su vez reutiliza el compositor y el
descargador existentes.
"""

from __future__ import annotations

import json
import mimetypes
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import config
from .poller import Poller
from .service import DashboardError, DashboardService

#: Rutas de la API. El orden importa: la primera coincidencia gana.
ROUTES = (
    ("GET", re.compile(r"^/api/state$"), "get_state"),
    ("GET", re.compile(r"^/api/events$"), "get_events"),
    ("GET", re.compile(r"^/api/accounts$"), "get_accounts"),
    ("POST", re.compile(r"^/api/accounts$"), "post_account"),
    ("POST", re.compile(r"^/api/accounts/([^/]+)/active$"), "post_account_active"),
    ("DELETE", re.compile(r"^/api/accounts/([^/]+)$"), "delete_account"),
    ("POST", re.compile(r"^/api/poll$"), "post_poll"),
    ("GET", re.compile(r"^/api/tweets$"), "get_tweets"),
    ("GET", re.compile(r"^/api/tweets/(\d+)$"), "get_tweet"),
    ("POST", re.compile(r"^/api/tweets/(\d+)/status$"), "post_tweet_status"),
    ("POST", re.compile(r"^/api/tweets/(\d+)/analyze$"), "post_tweet_analyze"),
    ("POST", re.compile(r"^/api/tweets/(\d+)/card$"), "post_tweet_card"),
    ("GET", re.compile(r"^/api/cards/(\d+)$"), "get_card"),
    ("POST", re.compile(r"^/api/cards/(\d+)/render$"), "post_card_render"),
    ("POST", re.compile(r"^/api/cards/(\d+)/send$"), "post_card_send"),
    ("GET", re.compile(r"^/api/cards/(\d+)/image$"), "get_card_image"),
    ("GET", re.compile(r"^/api/cards/(\d+)/deliveries$"), "get_card_deliveries"),
    ("GET", re.compile(r"^/api/deliveries$"), "get_deliveries"),
)

MAX_BODY_BYTES = 2 * 1024 * 1024


class DashboardHandler(BaseHTTPRequestHandler):
    """Manejador HTTP con la API del dashboard."""

    server_version = "EditImgDashboard/0.1"
    service: DashboardService
    poller: Poller

    # -- utilidades -----------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - firma heredada
        # Silencia el ruido por petición; los sucesos relevantes van a la bitácora.
        return

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message}, status=status)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise DashboardError("el cuerpo de la petición es demasiado grande")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DashboardError(f"JSON inválido en la petición: {exc}") from exc
        if not isinstance(payload, dict):
            raise DashboardError("el cuerpo de la petición debe ser un objeto JSON")
        return payload

    # -- reparto --------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - firma heredada
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path.startswith("/api/"):
            for route_method, pattern, handler_name in ROUTES:
                match = pattern.match(path)
                if not match:
                    continue
                if route_method != method:
                    continue
                handler = getattr(self, handler_name)
                try:
                    handler(query, *[unquote(group) for group in match.groups()])
                except DashboardError as exc:
                    self._send_error_json(str(exc), status=400)
                except BrokenPipeError:
                    return
                except Exception as exc:  # noqa: BLE001 - última red de seguridad
                    self.service.store.log(
                        f"Error interno en {method} {path}: {type(exc).__name__}: {exc}",
                        level="error",
                    )
                    self._send_error_json(
                        f"error interno: {type(exc).__name__}: {exc}", status=500
                    )
                return
            self._send_error_json(f"ruta no encontrada: {method} {path}", status=404)
            return

        if method != "GET":
            self._send_error_json("método no permitido", status=405)
            return
        if path in {"/", "/index.html"}:
            self._serve_file(config.WEB_DIR / "index.html")
            return
        if path.startswith("/static/"):
            self._serve_file(config.WEB_DIR / path[len("/static/") :])
            return
        self._send_error_json("ruta no encontrada", status=404)

    def _serve_file(self, path: Path) -> None:
        resolved = path.resolve()
        # Impide salir del directorio web con .. o enlaces simbólicos.
        try:
            resolved.relative_to(config.WEB_DIR.resolve())
        except ValueError:
            self._send_error_json("acceso denegado", status=403)
            return
        if not resolved.is_file():
            self._send_error_json("archivo no encontrado", status=404)
            return
        body = resolved.read_bytes()
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # -- endpoints ------------------------------------------------------
    def get_state(self, query, *groups) -> None:
        payload = self.service.state()
        payload["poller"] = self.poller.status()
        self._send_json(payload)

    def get_events(self, query, *groups) -> None:
        limit = _int_param(query, "limit", 60)
        self._send_json({"events": self.service.events(limit)})

    def get_accounts(self, query, *groups) -> None:
        self._send_json({"accounts": self.service.store.list_accounts()})

    def post_account(self, query, *groups) -> None:
        payload = self._read_json()
        account = self.service.add_account(str(payload.get("handle") or ""))
        self._send_json({"account": account}, status=201)

    def post_account_active(self, query, handle: str) -> None:
        payload = self._read_json()
        account = self.service.set_account_active(handle, bool(payload.get("active")))
        if not account:
            raise DashboardError(f"no existe la cuenta @{handle}")
        self._send_json({"account": account})

    def delete_account(self, query, handle: str) -> None:
        removed = self.service.remove_account(handle)
        if not removed:
            raise DashboardError(f"no existe la cuenta @{handle}")
        self._send_json({"removed": True, "handle": handle})

    def post_poll(self, query, *groups) -> None:
        payload = self._read_json()
        handle = (payload.get("handle") or "").strip() or None
        if payload.get("background"):
            if handle:
                raise DashboardError("el sondeo en segundo plano es para todas las cuentas")
            if not self.poller.trigger():
                raise DashboardError("ya hay un sondeo en curso")
            self._send_json({"started": True, "poller": self.poller.status()}, status=202)
            return
        self._send_json(self.service.poll(handle, payload.get("provider") or None))

    def get_tweets(self, query, *groups) -> None:
        status = _first(query, "status") or None
        handle = _first(query, "handle") or None
        limit = _int_param(query, "limit", 200)
        offset = _int_param(query, "offset", 0)
        tweets = self.service.list_tweets(
            status=status, source_handle=handle, limit=limit, offset=offset
        )
        self._send_json({"tweets": tweets, "counts": self.service.store.count_by_status()})

    def get_tweet(self, query, tweet_id: str) -> None:
        tweet = self.service.get_tweet(tweet_id)
        card = self.service.store.latest_card(tweet_id)
        self._send_json(
            {
                "tweet": tweet,
                "card": card,
                "defaults": self.service.default_params(tweet_id),
            }
        )

    def post_tweet_status(self, query, tweet_id: str) -> None:
        payload = self._read_json()
        tweet = self.service.set_tweet_status(tweet_id, str(payload.get("status") or ""))
        self._send_json({"tweet": tweet})

    def post_tweet_analyze(self, query, tweet_id: str) -> None:
        payload = self._read_json()
        tweet = self.service.analyse(tweet_id, payload.get("provider") or None)
        self._send_json({"tweet": tweet, "defaults": self.service.default_params(tweet_id)})

    def post_tweet_card(self, query, tweet_id: str) -> None:
        payload = self._read_json()
        card = self.service.prepare_card(tweet_id, payload.get("params") or {})
        self._send_json({"card": card}, status=201)

    def get_card(self, query, card_id: str) -> None:
        self._send_json({"card": self.service.get_card(int(card_id))})

    def post_card_render(self, query, card_id: str) -> None:
        payload = self._read_json()
        card = self.service.render_card(int(card_id), payload.get("params") or {})
        self._send_json({"card": card})

    def post_card_send(self, query, card_id: str) -> None:
        payload = self._read_json()
        result = self.service.send_card(
            int(card_id),
            caption=payload.get("caption"),
            provider=payload.get("provider") or None,
        )
        self._send_json(result)

    def get_card_image(self, query, card_id: str) -> None:
        path = self.service.card_image(int(card_id))
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def get_card_deliveries(self, query, card_id: str) -> None:
        self._send_json({"deliveries": self.service.deliveries(int(card_id))})

    def get_deliveries(self, query, *groups) -> None:
        self._send_json({"deliveries": self.service.deliveries()})


# ----------------------------------------------------------------------
def _first(query: dict, key: str) -> str:
    values = query.get(key) or []
    return values[0].strip() if values and values[0] else ""


def _int_param(query: dict, key: str, default: int) -> int:
    raw = _first(query, key)
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def create_server(
    host: str | None = None,
    port: int | None = None,
    service: DashboardService | None = None,
    poller: Poller | None = None,
) -> tuple[ThreadingHTTPServer, DashboardService, Poller]:
    """Crea el servidor listo para `serve_forever`."""
    settings = config.Settings()
    service = service or DashboardService()
    poller = poller if poller is not None else Poller(service)

    handler = type(
        "BoundDashboardHandler",
        (DashboardHandler,),
        {"service": service, "poller": poller},
    )
    server = ThreadingHTTPServer((host or settings.host, settings.port if port is None else port), handler)
    server.daemon_threads = True
    return server, service, poller


def serve(host: str | None = None, port: int | None = None, start_poller: bool = True) -> None:
    """Arranca el dashboard y bloquea hasta Ctrl+C."""
    server, service, poller = create_server(host, port)
    if start_poller:
        poller.start()
    url = f"http://{server.server_address[0]}:{server.server_address[1]}/"
    service.store.log(f"Dashboard iniciado en {url}")
    print(f"EditImg Dashboard escuchando en {url}")
    print("Pulsa Ctrl+C para detenerlo.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo el dashboard…")
    finally:
        poller.stop()
        server.shutdown()
        server.server_close()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Dashboard web de EditImg.")
    parser.add_argument("--host", default=None, help="interfaz de escucha (defecto 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="puerto (defecto 8765)")
    parser.add_argument(
        "--no-poller",
        action="store_true",
        help="no lanzar el sondeo automático en segundo plano",
    )
    parser.add_argument(
        "--traceback", action="store_true", help="mostrar la traza completa de los errores"
    )
    args = parser.parse_args()
    try:
        serve(args.host, args.port, start_poller=not args.no_poller)
    except OSError as exc:
        print(f"No se pudo abrir el puerto: {exc}")
        return 1
    except Exception:  # noqa: BLE001
        if args.traceback:
            traceback.print_exc()
        else:
            raise
        return 1
    return 0
