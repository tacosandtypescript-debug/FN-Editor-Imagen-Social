"""Servidor HTTP del dashboard (biblioteca estándar, sin dependencias).

Sirve la interfaz y una API JSON. No sustituye a nada del repositorio: solo
llama a la capa de servicio, que a su vez reutiliza el compositor y el
descargador existentes.
"""

from __future__ import annotations

import json
import mimetypes
import re
import secrets
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from . import config
from . import previews
from .poller import Poller
from .service import DashboardError, DashboardService

#: Rutas de la API. El orden importa: la primera coincidencia gana.
ROUTES = (
    ("GET", re.compile(r"^/api/state$"), "get_state"),
    ("GET", re.compile(r"^/api/events$"), "get_events"),
    ("GET", re.compile(r"^/api/analysis/status$"), "get_analysis_status"),
    ("GET", re.compile(r"^/api/jobs$"), "get_jobs"),
    ("GET", re.compile(r"^/api/jobs/(\d+)$"), "get_job"),
    ("POST", re.compile(r"^/api/analysis/test$"), "post_analysis_test"),
    ("POST", re.compile(r"^/api/profiles/chatgpt/open$"), "post_chatgpt_login"),
    ("GET", re.compile(r"^/api/accounts$"), "get_accounts"),
    ("POST", re.compile(r"^/api/accounts$"), "post_account"),
    ("POST", re.compile(r"^/api/accounts/([^/]+)/active$"), "post_account_active"),
    ("DELETE", re.compile(r"^/api/accounts/([^/]+)$"), "delete_account"),
    ("POST", re.compile(r"^/api/poll$"), "post_poll"),
    ("POST", re.compile(r"^/api/maintenance/purge$"), "post_maintenance_purge"),
    ("GET", re.compile(r"^/api/maintenance$"), "get_maintenance"),
    ("GET", re.compile(r"^/api/tweets$"), "get_tweets"),
    ("GET", re.compile(r"^/api/tweets/(\d+)$"), "get_tweet"),
    ("POST", re.compile(r"^/api/tweets/(\d+)/status$"), "post_tweet_status"),
    ("POST", re.compile(r"^/api/tweets/(\d+)/analyze$"), "post_tweet_analyze"),
    ("POST", re.compile(r"^/api/tweets/(\d+)/process$"), "post_tweet_process"),
    ("POST", re.compile(r"^/api/tweets/(\d+)/card$"), "post_tweet_card"),
    ("GET", re.compile(r"^/api/cards$"), "get_cards"),
    ("GET", re.compile(r"^/api/cards/(\d+)$"), "get_card"),
    ("POST", re.compile(r"^/api/cards/(\d+)/render$"), "post_card_render"),
    ("POST", re.compile(r"^/api/cards/(\d+)/regenerate-text$"), "post_card_regenerate_text"),
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

    # -- control de acceso ----------------------------------------------
    def _authorised(self, path: str, query: dict) -> bool:
        """Comprueba la clave de acceso cuando el dashboard está en la red.

        Se entra sin clave desde el propio equipo y desde el tailnet de
        Tailscale. Para el resto de la red local hace falta la clave, que viaja
        una vez en la URL y después queda en una cookie.
        """
        token = getattr(self.server, "access_token", None)
        if not token:
            return True

        origin = self.client_address[0]
        if config.is_loopback(origin):
            return True
        settings = config.Settings()
        if settings.trust_tailnet and config.is_tailnet(origin):
            return True

        supplied = _first(query, "token") or (self.headers.get("X-Dashboard-Token") or "").strip()
        if not supplied:
            supplied = _cookie(self.headers.get("Cookie"), "editimg_token")

        if supplied and secrets.compare_digest(supplied, token):
            # Si venía en la URL, se guarda en cookie y se limpia la barra.
            if _first(query, "token"):
                clean = path or "/"
                remaining = {key: values for key, values in query.items() if key != "token"}
                if remaining:
                    clean += "?" + urlencode(remaining, doseq=True)
                self.send_response(302)
                self.send_header("Location", clean)
                self.send_header(
                    "Set-Cookie",
                    f"editimg_token={token}; Path=/; SameSite=Lax; Max-Age=2592000",
                )
                self.send_header("Content-Length", "0")
                self.end_headers()
            return True

        body = (
            "<!DOCTYPE html><html lang='es'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>EditImg Dashboard</title>"
            "<body style=\"font-family:system-ui;background:#0f0b18;color:#f2eefb;"
            "padding:24px;line-height:1.6\">"
            "<h1 style='font-size:20px'>Hace falta la clave de acceso</h1>"
            "<p>Abre la dirección que imprime el dashboard al arrancar, "
            "incluyendo <code>?token=…</code>. La tienes en la ventana donde "
            "ejecutaste <code>python -m dashboard</code> y también en "
            "<code>dashboard/var/dashboard.sqlite3</code> (ajuste "
            "<code>access_token</code>).</p>"
            "<p style='color:#a294c9'>Si entras por Tailscale, actualiza el "
            "dashboard: desde el tailnet ya no se pide clave.</p>"
            "</body></html>"
        ).encode("utf-8")
        try:
            self.send_response(401)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            # El cliente cortó la conexión: no hay nada que responder.
            pass
        return False

    # -- utilidades -----------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - firma heredada
        # Silencia el ruido por petición; los sucesos relevantes van a la bitácora.
        return

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            # El cliente cortó la conexión (recarga, cierre de pestaña): no es
            # un error del servidor y no debe ensuciar la consola.
            pass

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

        if not self._authorised(path, query):
            return

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
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    return
                except DashboardError as exc:
                    self._send_error_json(str(exc), status=400)
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
        if path in {"/editor", "/editor.html"}:
            # Editor independiente: se abre en su propia pestaña.
            self._serve_file(config.WEB_DIR / "editor.html")
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
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    # -- endpoints ------------------------------------------------------
    def get_state(self, query, *groups) -> None:
        payload = self.service.state()
        payload["poller"] = self.poller.status()
        self._send_json(payload)

    def get_events(self, query, *groups) -> None:
        limit = _int_param(query, "limit", 60)
        self._send_json({"events": self.service.events(limit)})

    def get_analysis_status(self, query, *groups) -> None:
        self._send_json(self.service.analysis_status())

    def post_analysis_test(self, query, *groups) -> None:
        """Prueba el proveedor de análisis sin guardar nada."""
        payload = self._read_json()
        tweet_id = str(payload.get("tweet_id") or "").strip()
        if not tweet_id:
            raise DashboardError("falta el identificador de la publicación")
        self._send_json(self.service.test_analysis(tweet_id, payload.get("provider") or None))

    def post_chatgpt_login(self, query, *groups) -> None:
        """Abre la ventana de Chrome con el perfil de ChatGPT para entrar."""
        self._send_json(self.service.open_chatgpt_login())

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
            # El hilo debe existir para poder atender el disparo; si el servidor
            # se creó sin hilo periódico, se levanta aquí.
            if not self.poller.running:
                self.poller.start()
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
        # Por defecto solo se muestra lo pendiente: lo ya procesado y lo
        # duplicado dejan de estorbar.
        pending_only = _first(query, "pending") != "0"
        tweets = self.service.list_tweets(
            status=status,
            source_handle=handle,
            pending_only=pending_only,
            limit=limit,
            offset=offset,
        )
        self._send_json({"tweets": tweets, "counts": self.service.store.count_by_status()})

    def get_maintenance(self, query, *groups) -> None:
        self._send_json(self.service.maintenance_state())

    def post_maintenance_purge(self, query, *groups) -> None:
        """Borra de la bandeja lo anterior a la retención configurada."""
        payload = self._read_json()
        result = self.service.maintenance(force=bool(payload.get("force", True)))
        self._send_json(result)

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

    def post_tweet_process(self, query, tweet_id: str) -> None:
        """Botón «Procesar»: análisis, descarga y composición.

        Se atiende en segundo plano y responde al instante. El trabajo tarda
        entre treinta y sesenta segundos (Codex más composición 4K); mantener
        al navegador esperando hacía que, al cortarse la petición, la interfaz
        mostrara un fallo aunque el servidor hubiera terminado bien. Con
        `sync` se puede forzar el camino directo, útil para pruebas.
        """
        payload = self._read_json()
        params = payload.get("params") or None
        provider = payload.get("provider") or None
        force = bool(payload.get("force_analysis"))
        if payload.get("sync"):
            self._send_json(self.service.process_tweet(tweet_id, params, provider, force), status=201)
            return
        job = self.service.enqueue_process(tweet_id, params, provider, force)
        self._send_json({"job": job, "queued": True}, status=202)

    def get_jobs(self, query, *groups) -> None:
        self._send_json(self.service.jobs_state())

    def get_job(self, query, job_id: str) -> None:
        job = self.service.jobs.get(int(job_id))
        if job is None:
            raise DashboardError(f"no existe el trabajo {job_id}")
        self._send_json({"job": job.as_dict(include_result=True)})

    def post_card_regenerate_text(self, query, card_id: str) -> None:
        """Regenera titular, texto inferior y caption.

        Se encola: el análisis tarda unos treinta segundos y el navegador no
        debe quedarse esperando. Con `sync` se fuerza el camino directo.
        """
        payload = self._read_json()
        if payload.get("sync"):
            self._send_json(
                self.service.regenerate_text(
                    int(card_id),
                    instructions=payload.get("instructions"),
                    render=bool(payload.get("render", True)),
                    provider=payload.get("provider") or None,
                )
            )
            return
        job = self.service.enqueue_regenerate(
            int(card_id),
            instructions=payload.get("instructions"),
            provider=payload.get("provider") or None,
            render=bool(payload.get("render", True)),
        )
        self._send_json({"job": job, "queued": True}, status=202)

    def get_card(self, query, card_id: str) -> None:
        self._send_json({"card": self.service.get_card(int(card_id))})

    def get_cards(self, query, *groups) -> None:
        """Listado de tarjetas creadas: la vista de «ya procesadas»."""
        limit = _int_param(query, "limit", 200)
        self._send_json({"cards": self.service.list_cards_view(limit)})

    def post_card_render(self, query, card_id: str) -> None:
        payload = self._read_json()
        card = self.service.render_card(int(card_id), payload.get("params") or {})
        self._send_json({"card": card})

    def post_card_send(self, query, card_id: str) -> None:
        payload = self._read_json()
        caption = payload.get("caption")
        provider = payload.get("provider") or None
        if payload.get("sync"):
            self._send_json(
                self.service.send_card(int(card_id), caption=caption, provider=provider)
            )
            return
        job = self.service.enqueue_send(int(card_id), caption=caption, provider=provider)
        self._send_json({"job": job, "queued": True}, status=202)

    def get_card_image(self, query, card_id: str) -> None:
        """Sirve la tarjeta.

        Por defecto entrega la **versión reducida**: el PNG original ronda los
        30 MB y en una lista de móvil eso es servir una imagen de 4000 px en un
        hueco de 400 px. Con `size=full` se entrega el PNG original, que es lo
        que se descarga y lo que se envía a Telegram.
        """
        path = self.service.card_image(int(card_id))
        size = (_first(query, "size") or "preview").lower()
        tipo = "image/png"
        if size in ("preview", "thumb", "reduced"):
            ancho = _int_param(query, "w", previews.PREVIEW_MAX_SIDE)
            try:
                path = previews.preview_path(path, max_side=ancho or previews.PREVIEW_MAX_SIDE)
                tipo = "image/jpeg"
            except Exception:  # noqa: BLE001 - ante cualquier fallo, el original
                tipo = "image/png"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(body)))
        # Las miniaturas llevan el sello del original en el nombre, así que se
        # pueden cachear; el original no, porque se recompone a menudo.
        self.send_header(
            "Cache-Control",
            "public, max-age=300" if tipo == "image/jpeg" else "no-store",
        )
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


def _cookie(header: str | None, name: str) -> str:
    """Lee una cookie concreta de la cabecera `Cookie`."""
    for chunk in str(header or "").split(";"):
        key, _, value = chunk.strip().partition("=")
        if key == name:
            return value.strip()
    return ""


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
    periodic: bool = True,
) -> tuple[ThreadingHTTPServer, DashboardService, Poller]:
    """Crea el servidor listo para `serve_forever`.

    Si se escucha fuera de localhost, se exige una clave de acceso: el
    dashboard puede enviar a Telegram y no conviene dejarlo abierto a
    cualquiera que esté en la misma red.
    """
    settings = config.Settings()
    service = service or DashboardService()
    poller = poller if poller is not None else Poller(service, periodic=periodic)
    bind_host = host or settings.host

    access_token = ""
    if bind_host not in {"127.0.0.1", "localhost", "::1"}:
        access_token = settings.access_token or service.store.get_setting("access_token") or ""
        if not access_token:
            access_token = secrets.token_urlsafe(12)
            service.store.set_setting("access_token", access_token)
            service.store.log("Clave de acceso generada para el acceso desde la red local")

    handler = type(
        "BoundDashboardHandler",
        (DashboardHandler,),
        {"service": service, "poller": poller},
    )
    server = ThreadingHTTPServer((bind_host, settings.port if port is None else port), handler)
    server.daemon_threads = True
    server.access_token = access_token  # type: ignore[attr-defined]
    return server, service, poller


def serve(host: str | None = None, port: int | None = None, periodic: bool = True) -> None:
    """Arranca el dashboard y bloquea hasta Ctrl+C.

    El hilo del sondeador se lanza siempre: con `periodic=False` simplemente no
    sondea por su cuenta, pero sigue atendiendo los sondeos pedidos desde la
    interfaz.
    """
    server, service, poller = create_server(host, port, periodic=periodic)

    # La hora se mide antes de registrar nada: si no, el primer suceso
    # («Dashboard iniciado») quedaría fechado con el reloj del sistema, que
    # puede ir desviado, y parecería de otra hora.
    try:
        service.sync_clock(force=True)
    except Exception as exc:  # noqa: BLE001 - sin red se sigue con el reloj local
        print(f"Aviso: no se pudo verificar la hora por internet ({type(exc).__name__}).")

    poller.start()

    token = getattr(server, "access_token", "")
    actual_host, actual_port = server.server_address[0], server.server_address[1]
    urls = config.listen_addresses(actual_host, actual_port)
    if token:
        urls = [f"{url}?token={token}" for url in urls]

    service.store.log(f"Dashboard iniciado en {urls[0]}")
    print("EditImg Dashboard disponible en:")
    for url in urls:
        print(f"  {url}")
    if token:
        print()
        print("Hay clave de acceso porque se está escuchando en la red local.")
        print("Abre la dirección completa (con ?token=…) una vez: el navegador la recuerda.")
        print("Si Windows bloquea la entrada, permite el puerto 8765 en el firewall.")
    print(
        "Sondeo periódico activado." if periodic
        else "Sondeo periódico desactivado (el botón «Buscar ahora» sigue funcionando)."
    )
    print("Pulsa Ctrl+C para detenerlo.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo el dashboard…")
    finally:
        poller.stop()
        # Los hilos de la cola de trabajos deben terminar antes de salir; si no,
        # pueden seguir usando la base de datos mientras se cierra todo.
        service.shutdown()
        server.shutdown()
        server.server_close()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Dashboard web de EditImg.")
    parser.add_argument("--host", default=None, help="interfaz de escucha (defecto 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="puerto (defecto 8765)")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="escuchar en toda la red local para poder abrirlo desde el móvil",
    )
    parser.add_argument(
        "--no-poller",
        action="store_true",
        help="desactivar solo el sondeo periódico (el botón «Buscar ahora» sigue activo)",
    )
    parser.add_argument(
        "--traceback", action="store_true", help="mostrar la traza completa de los errores"
    )
    args = parser.parse_args()
    host = args.host or ("0.0.0.0" if args.lan else None)
    try:
        serve(host, args.port, periodic=not args.no_poller)
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
