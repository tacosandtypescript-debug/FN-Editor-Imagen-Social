"""Tests de integración del dashboard.

El dashboard es un visor: descubre publicaciones, las enseña y las marca. Estos
tests recorren eso de punta a punta, incluida la API HTTP, sin salir a la red.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import config  # noqa: E402
from dashboard.poller import Poller  # noqa: E402
from dashboard.server import create_server  # noqa: E402
from dashboard.service import DashboardError, DashboardService  # noqa: E402
from dashboard.store import STATUS_NEW, STATUS_READY, Store  # noqa: E402


class DashboardIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        work = Path(self._temporary.name)
        self._originals = {
            name: getattr(config, name)
            for name in (
                "VAR_DIR",
                "DB_PATH",
                "MEDIA_DIR",
                "PROFILES_DIR",
                "LOGS_DIR",
            )
        }
        config.VAR_DIR = work
        config.DB_PATH = work / "dashboard.sqlite3"
        config.MEDIA_DIR = work / "media"
        config.PROFILES_DIR = work / "profiles"
        config.LOGS_DIR = work / "logs"
        config.ensure_directories()

        self.service = DashboardService(store=Store(config.DB_PATH))
        self.tweet_id = "2105562614461776336"
        self.service.store.upsert_tweets(
            [
                {
                    "tweet_id": self.tweet_id,
                    "source_handle": "ShiinaBR",
                    "author_handle": "ShiinaBR",
                    "text": "NEW LOOK AT THE FORTNITEMARES MAP",
                    "url": f"https://x.com/ShiinaBR/status/{self.tweet_id}",
                    "posted_at": "2026-10-01T07:37:01+00:00",
                    "media": ["https://pbs.twimg.com/media/AAA111.jpg"],
                }
            ]
        )
        self.service.store.add_account("ShiinaBR")

    def tearDown(self):
        # Esta clase no levanta servidor ni sondeador: solo el servicio.
        self.service.shutdown()
        for name, value in self._originals.items():
            setattr(config, name, value)
        self._temporary.cleanup()

    def _restore_provider(self, key):
        if self._provider_before is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = self._provider_before

    # --- marcar como listo ---------------------------------------------
    def test_marking_as_ready_changes_the_status(self):
        tweet = self.service.mark_ready(self.tweet_id)
        self.assertEqual(tweet["status"], STATUS_READY)

    def test_unmarking_returns_it_to_the_pile(self):
        self.service.mark_ready(self.tweet_id)
        tweet = self.service.unmark_ready(self.tweet_id)
        self.assertEqual(tweet["status"], STATUS_NEW)

    def test_marking_an_unknown_publication_is_reported(self):
        with self.assertRaises(DashboardError):
            self.service.mark_ready("999999")

    def test_ready_publications_leave_the_pending_view(self):
        """Marcar listo la aparta: es la marca que la salva de la limpieza."""
        self.service.mark_ready(self.tweet_id)
        self.assertEqual(self.service.list_tweets(pending_only=True), [])
        self.assertEqual(len(self.service.list_tweets()), 1)

    # --- vídeos --------------------------------------------------------
    def _una(self, tweet_id):
        return [
            tweet for tweet in self.service.list_tweets(limit=20)
            if tweet["tweet_id"] == tweet_id
        ][0]

    def test_a_video_is_flagged_as_such(self):
        """Los vídeos de X llegan como miniatura; se señalan para distinguirlos."""
        self.service.store.upsert_tweets(
            [
                {
                    "tweet_id": "777",
                    "source_handle": "cuenta",
                    "text": "con video",
                    "media": ["https://pbs.twimg.com/amplify_video_thumb/AAA/img/x.jpg"],
                }
            ]
        )
        self.assertTrue(self._una("777")["has_video"])

    def test_a_photo_is_not_flagged_as_video(self):
        self.assertFalse(self._una(self.tweet_id)["has_video"])

    def test_thumbnails_ask_the_cdn_for_a_small_version(self):
        """Una miniatura de 108 px no debe pedir el archivo original."""
        thumb = self._una(self.tweet_id)["thumbs"][0]
        self.assertIn("name=360x360", thumb)



    # --- fechas en las publicaciones -----------------------------------
    def test_listed_tweets_carry_relative_and_absolute_dates(self):
        tweets = self.service.list_tweets(limit=5)
        self.assertEqual(len(tweets), 1)
        tweet = tweets[0]
        self.assertIn("posted_relative", tweet)
        self.assertIn("posted_absolute", tweet)
        self.assertTrue(tweet["posted_relative"])
        self.assertTrue(tweet["posted_absolute"])

    def test_the_api_exposes_the_raw_timestamp_the_client_needs(self):
        """El navegador recalcula «hace X» con `posted_at`, sin volver a pedir nada.

        Si este campo desapareciera, las horas relativas se quedarían
        congeladas otra vez: es el dato del que depende el refresco local.
        """
        tweet = self.service.list_tweets(limit=5)[0]
        self.assertIn("posted_at", tweet)
        self.assertTrue(tweet["posted_at"], "hace falta la marca original")
        # Y la fecha de respaldo, para las publicaciones sin hora de origen.
        self.assertIn("fetched_at", tweet)


    def test_tweets_come_back_strictly_from_newest_to_oldest(self):
        """El orden es el contrato principal de la bandeja."""
        # Se insertan a propósito en orden desordenado. Todas son anteriores a
        # la publicación que ya trae el fixture (2026-10-01T07:37:01).
        moments = [
            ("300", "2026-09-20T10:00:00+00:00"),
            ("100", "2026-10-01T07:00:00+00:00"),
            ("500", "2026-09-01T23:59:00+00:00"),
            ("200", "2026-09-30T18:30:00+00:00"),
            ("400", "2026-09-15T00:00:00+00:00"),
        ]
        for tweet_id, posted in moments:
            self.service.store.upsert_tweets(
                [
                    {
                        "tweet_id": tweet_id,
                        "source_handle": "cuenta",
                        "text": f"publicacion {tweet_id}",
                        "posted_at": posted,
                    }
                ]
            )

        listed = self.service.list_tweets(limit=50)
        keys = [tweet["posted_at"] for tweet in listed]
        self.assertEqual(keys, sorted(keys, reverse=True), keys)
        self.assertEqual(
            [tweet["tweet_id"] for tweet in listed],
            [self.tweet_id, "100", "200", "300", "400", "500"],
        )

    def test_tweets_without_a_date_sort_by_download_time(self):
        self.service.store.upsert_tweets(
            [{"tweet_id": "900", "source_handle": "cuenta", "text": "sin fecha"}]
        )
        listed = self.service.list_tweets(limit=50)
        # Sin fecha de publicación cae a la de descarga, que es la más reciente.
        self.assertEqual(listed[0]["tweet_id"], "900")
        self.assertTrue(listed[0]["date_is_estimated"])
        # Y la de descarga usa la hora verificada, no el reloj del sistema.
        fetched = listed[0]["fetched_at"]
        self.assertGreater(fetched, "2026-10-01T07:37:01+00:00")


class DashboardHttpTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        work = Path(self._temporary.name)
        self._originals = {
            name: getattr(config, name)
            for name in ("VAR_DIR", "DB_PATH", "MEDIA_DIR", "PROFILES_DIR", "LOGS_DIR")
        }
        config.VAR_DIR = work
        config.DB_PATH = work / "dashboard.sqlite3"
        config.MEDIA_DIR = work / "media"
        config.PROFILES_DIR = work / "profiles"
        config.LOGS_DIR = work / "logs"
        config.ensure_directories()

        self.service = DashboardService(store=Store(config.DB_PATH))
        self.tweet_id = "1234567890"
        self.service.store.upsert_tweets(
            [
                {
                    "tweet_id": self.tweet_id,
                    "source_handle": "Cuenta",
                    "text": "NOVEDAD DE FORTNITE",
                    "url": f"https://x.com/Cuenta/status/{self.tweet_id}",
                    "media": ["https://pbs.twimg.com/media/AAA111.jpg"],
                }
            ]
        )
        self.service.store.add_account("Cuenta")

        self.server, _, self.poller = create_server(
            host="127.0.0.1", port=0, service=self.service, poller=Poller(self.service)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        # El sondeador tiene su propio hilo: debe parar antes de borrar el
        # directorio temporal, o seguiría usando la base de datos.
        self.poller.stop()
        self.service.shutdown()
        for name, value in self._originals.items():
            setattr(config, name, value)
        self._temporary.cleanup()

    def call_raw(self, method, path, body=None):
        """Como `call`, pero devuelve también las cabeceras de la respuesta."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def call(self, method, path, body=None, raw=False):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
                return response.status, payload if raw else json.loads(payload)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


    # ------------------------------------------------------------------
    def test_index_and_static_assets_are_served(self):
        status, index = self.call("GET", "/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"<html", index.lower())
        # Las tres pestañas del visor, y ni rastro del editor retirado.
        for marca in ("Bandeja", "Cuentas", "Ajustes"):
            with self.subTest(marca=marca):
                self.assertIn(marca.encode("utf-8"), index)
        self.assertNotIn(b"editor", index.lower())
        self.assertNotIn("proposal".encode("utf-8"), index.lower())

        for path, marker in (
            ("/static/app.js", b"Copiar enlace"),
            ("/static/styles.css", b"--purple"),
        ):
            with self.subTest(path=path):
                status, body = self.call("GET", path, raw=True)
                self.assertEqual(status, 200)
                self.assertIn(marker, body)

    def test_state_exposes_providers_without_secrets(self):
        status, state = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("settings", state)
        self.assertIn("poller", state)
        names = {provider["name"] for provider in state["timeline_providers"]}
        self.assertEqual(names, {"browser", "nitter", "xapi"})
        # Ya no hay proveedores de análisis ni de entrega: se fueron con el editor.
        self.assertNotIn("analysis_providers", state)
        self.assertNotIn("delivery_providers", state)
        # No se filtran credenciales, solo indicadores booleanos.
        self.assertIn("access_token_set", state["settings"])

    def test_account_lifecycle_over_http(self):
        status, _ = self.call("POST", "/api/accounts", {"handle": "@Nueva"})
        self.assertEqual(status, 201)
        status, payload = self.call("GET", "/api/accounts")
        self.assertIn("Nueva", [account["handle"] for account in payload["accounts"]])

        status, _ = self.call("POST", "/api/accounts/Nueva/active", {"active": False})
        self.assertEqual(status, 200)
        status, _ = self.call("DELETE", "/api/accounts/Nueva")
        self.assertEqual(status, 200)
        status, payload = self.call("DELETE", "/api/accounts/Nueva")
        self.assertEqual(status, 400)
        self.assertIn("no existe", payload["error"])


    def test_invalid_requests_are_rejected_cleanly(self):
        status, payload = self.call("GET", "/api/no-existe")
        self.assertEqual(status, 404)
        self.assertIn("error", payload)

        status, payload = self.call("POST", "/api/tweets/999999/ready", {"ready": True})
        self.assertEqual(status, 400)
        self.assertIn("no existe", payload["error"])

        status, payload = self.call("POST", "/api/accounts", {"handle": "   "})
        self.assertEqual(status, 400)

    def test_retired_routes_are_gone(self):
        """Lo del editor se retiró de verdad, no solo de la interfaz."""
        for ruta in (
            "/api/cards",
            "/api/cards/1/image",
            "/api/jobs",
            "/api/analysis/status",
            "/api/deliveries",
            "/editor.html",
        ):
            with self.subTest(ruta=ruta):
                status, _ = self.call("GET", ruta)
                self.assertEqual(status, 404, ruta)

    def test_path_traversal_is_blocked(self):
        status, payload = self.call("GET", "/static/../config.py")
        self.assertIn(status, (403, 404))
        self.assertIn("error", payload)

    # --- marcar como listo --------------------------------------------
    def test_the_ready_endpoint_marks_and_unmarks(self):
        status, payload = self.call(
            "POST", f"/api/tweets/{self.tweet_id}/ready", {"ready": True}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["tweet"]["status"], STATUS_READY)

        status, payload = self.call(
            "POST", f"/api/tweets/{self.tweet_id}/ready", {"ready": False}
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["tweet"]["status"], STATUS_NEW)

    def test_processed_publications_leave_the_pending_view(self):
        """Lo marcado como listo deja de estorbar en la vista por defecto."""
        status, _ = self.call("POST", f"/api/tweets/{self.tweet_id}/ready", {"ready": True})
        self.assertEqual(status, 200)

        status, pending = self.call("GET", "/api/tweets?status=todos&limit=10")
        self.assertEqual(pending["tweets"], [], "ya no debería aparecer como pendiente")

        status, everything = self.call("GET", "/api/tweets?status=todos&limit=10&pending=0")
        self.assertEqual(len(everything["tweets"]), 1)
        self.assertTrue(everything["tweets"][0]["is_processed"])

        # Y con el filtro de listas sí aparece: se puede desmarcar.
        status, listas = self.call("GET", "/api/tweets?status=listo&pending=0&limit=10")
        self.assertEqual(len(listas["tweets"]), 1)
        self.assertEqual(listas["tweets"][0]["status"], STATUS_READY)

    def test_media_tabs_are_backed_by_the_api_filter(self):
        self.service.store.upsert_tweets(
            [
                {
                    "tweet_id": "video-only",
                    "source_handle": "cuenta",
                    "text": "publicación con vídeo sin miniatura",
                    "url": "https://x.com/cuenta/status/video-only",
                    "has_video": True,
                }
            ]
        )
        status, payload = self.call("GET", "/api/tweets?pending=0&media=videos&limit=10")
        self.assertEqual(status, 200)
        self.assertEqual([tweet["tweet_id"] for tweet in payload["tweets"]], ["video-only"])
        self.assertEqual(payload["media_counts"]["videos"], 1)




    # --- vista de procesadas ------------------------------------------















class AccessControlTests(unittest.TestCase):
    """El dashboard puede quedar expuesto a la red local: debe pedir clave."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        work = Path(self._temporary.name)
        self._originals = {
            name: getattr(config, name)
            for name in ("VAR_DIR", "DB_PATH", "MEDIA_DIR", "PROFILES_DIR", "LOGS_DIR")
        }
        config.VAR_DIR = work
        config.DB_PATH = work / "dashboard.sqlite3"
        config.MEDIA_DIR = work / "media"
        config.PROFILES_DIR = work / "profiles"
        config.LOGS_DIR = work / "logs"
        config.ensure_directories()

        self.service = DashboardService(store=Store(config.DB_PATH))
        self.server, _, self.poller = create_server(
            host="127.0.0.1", port=0, service=self.service, poller=Poller(self.service)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

        # Se simula que la petición viene de otro dispositivo de la red.
        self.original_is_loopback = config.is_loopback
        self.addCleanup(setattr, config, "is_loopback", self.original_is_loopback)
        config.is_loopback = lambda address: False

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        # Los hilos de la cola de trabajos deben parar antes de borrar el
        # directorio temporal: si no, siguen usando la base de datos.
        self.poller.stop()
        self.service.shutdown()
        for name, value in self._originals.items():
            setattr(config, name, value)
        self._temporary.cleanup()

    def request(self, path, headers=None, follow_redirects=True):
        request = urllib.request.Request(self.base + path, headers=headers or {})
        opener = urllib.request.build_opener()
        if not follow_redirects:
            # urllib sigue las redirecciones por su cuenta y perdería la cookie.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None

            opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def test_without_a_token_the_server_stays_open_on_loopback(self):
        config.is_loopback = lambda address: True
        status, _, _ = self.request("/api/state")
        self.assertEqual(status, 200)

    def test_a_remote_client_without_the_token_is_rejected(self):
        self.server.access_token = "clave-secreta"
        for path in ("/", "/api/state", "/static/app.js"):
            with self.subTest(path=path):
                status, body, _ = self.request(path)
                self.assertEqual(status, 401)
                self.assertIn(b"clave de acceso", body)

    def test_the_token_in_the_url_authorises_and_becomes_a_cookie(self):
        self.server.access_token = "clave-secreta"
        status, _, headers = self.request("/?token=clave-secreta", follow_redirects=False)
        self.assertEqual(status, 302)
        self.assertIn("editimg_token=clave-secreta", headers.get("Set-Cookie", ""))
        self.assertEqual(headers.get("Location"), "/")

    def test_the_url_token_is_stripped_from_the_redirect(self):
        self.server.access_token = "clave-secreta"
        status, _, headers = self.request(
            "/api/tweets?status=nuevo&token=clave-secreta", follow_redirects=False
        )
        self.assertEqual(status, 302)
        location = headers.get("Location", "")
        self.assertNotIn("token", location)
        self.assertIn("status=nuevo", location)

    def test_the_cookie_authorises_later_requests(self):
        self.server.access_token = "clave-secreta"
        status, _, _ = self.request("/api/state", {"Cookie": "editimg_token=clave-secreta"})
        self.assertEqual(status, 200)

    def test_the_header_also_authorises(self):
        self.server.access_token = "clave-secreta"
        status, _, _ = self.request("/api/state", {"X-Dashboard-Token": "clave-secreta"})
        self.assertEqual(status, 200)

    def test_a_wrong_token_is_rejected(self):
        self.server.access_token = "clave-secreta"
        status, _, _ = self.request("/?token=equivocada")
        self.assertEqual(status, 401)

    def test_loopback_is_never_asked_for_a_token(self):
        self.server.access_token = "clave-secreta"
        config.is_loopback = self.original_is_loopback
        status, _, _ = self.request("/api/state")
        self.assertEqual(status, 200)

    def test_the_tailnet_enters_without_a_token(self):
        """Por Tailscale no se pide clave: es una red privada ya autenticada.

        Motivo real: el navegador del móvil perdía la cookie y el dashboard
        quedaba inaccesible desde el tailnet, que era justo la vía cómoda.
        """
        self.server.access_token = "clave-secreta"
        original_tailnet = config.is_tailnet
        self.addCleanup(setattr, config, "is_tailnet", original_tailnet)
        config.is_tailnet = lambda address: True
        status, _, _ = self.request("/api/state")
        self.assertEqual(status, 200)

    def test_the_tailnet_exemption_can_be_turned_off(self):
        self.server.access_token = "clave-secreta"
        original_tailnet = config.is_tailnet
        self.addCleanup(setattr, config, "is_tailnet", original_tailnet)
        config.is_tailnet = lambda address: True
        key = "DASHBOARD_TRUST_TAILNET"
        before = os.environ.get(key)
        os.environ[key] = "0"
        self.addCleanup(
            lambda: os.environ.pop(key, None) if before is None else os.environ.__setitem__(key, before)
        )
        status, _, _ = self.request("/api/state")
        self.assertEqual(status, 401)

    def test_other_lan_addresses_still_need_the_token(self):
        """La clave se sigue exigiendo fuera del tailnet."""
        self.server.access_token = "clave-secreta"
        original_tailnet = config.is_tailnet
        self.addCleanup(setattr, config, "is_tailnet", original_tailnet)
        config.is_tailnet = lambda address: False
        status, _, _ = self.request("/api/state")
        self.assertEqual(status, 401)
        status, _, _ = self.request(
            "/api/state", {"Cookie": "editimg_token=clave-secreta"}
        )
        self.assertEqual(status, 200)


class TailnetAddressTests(unittest.TestCase):
    def test_tailscale_ranges_are_recognised(self):
        for address in ("100.95.55.79", "100.98.201.10", "100.64.0.1", "100.127.255.254"):
            with self.subTest(address=address):
                self.assertTrue(config.is_tailnet(address))

    def test_ipv6_tailnet_is_recognised(self):
        self.assertTrue(config.is_tailnet("fd7a:115c:a1e0::1"))

    def test_ordinary_addresses_are_not_the_tailnet(self):
        for address in ("127.0.0.1", "10.0.0.44", "192.168.1.20", "8.8.8.8", "100.63.255.255", "100.128.0.1"):
            with self.subTest(address=address):
                self.assertFalse(config.is_tailnet(address))

    def test_junk_is_handled(self):
        for address in ("", "no-es-una-ip", None):
            with self.subTest(address=address):
                self.assertFalse(config.is_tailnet(address))

    def test_loopback_is_not_confused_with_the_tailnet(self):
        self.assertTrue(config.is_loopback("127.0.0.1"))
        self.assertFalse(config.is_tailnet("127.0.0.1"))


class ListenAddressTests(unittest.TestCase):
    def test_loopback_reports_only_localhost(self):
        self.assertEqual(
            config.listen_addresses("127.0.0.1", 8765), ["http://127.0.0.1:8765/"]
        )

    def test_lan_binding_reports_network_addresses(self):
        urls = config.listen_addresses("0.0.0.0", 8765)
        self.assertIn("http://127.0.0.1:8765/", urls)
        self.assertTrue(all(url.startswith("http://") for url in urls))

    def test_is_loopback_recognises_the_local_forms(self):
        for address in ("127.0.0.1", "::1", "127.0.0.5", "::ffff:127.0.0.1", "localhost"):
            with self.subTest(address=address):
                self.assertTrue(config.is_loopback(address))
        for address in ("10.0.0.44", "192.168.1.20", "100.95.55.79", "8.8.8.8"):
            with self.subTest(address=address):
                self.assertFalse(config.is_loopback(address))


if __name__ == "__main__":
    unittest.main()
