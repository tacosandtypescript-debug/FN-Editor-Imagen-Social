"""Tests de integración del dashboard.

Ejercitan el compositor real y la API HTTP completa sin salir a la red: los
medios se colocan ya descargados en el directorio de trabajo y el análisis y
la entrega usan los proveedores que no necesitan credenciales.
"""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from dashboard import config  # noqa: E402
from dashboard.poller import Poller  # noqa: E402
from dashboard.server import create_server  # noqa: E402
from dashboard.service import DashboardError, DashboardService  # noqa: E402
from dashboard.store import Store  # noqa: E402


class DashboardIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        work = Path(self._temporary.name)
        self._originals = {
            name: getattr(config, name)
            for name in (
                "VAR_DIR",
                "DB_PATH",
                "MEDIA_DIR",
                "CARDS_DIR",
                "PROFILES_DIR",
                "LOGS_DIR",
            )
        }
        config.VAR_DIR = work
        config.DB_PATH = work / "dashboard.sqlite3"
        config.MEDIA_DIR = work / "media"
        config.CARDS_DIR = work / "cards"
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
        # Medios ya presentes: así no se sale a la red.
        media_dir = self.service.media_directory(self.tweet_id)
        media_dir.mkdir(parents=True, exist_ok=True)
        self.source = media_dir / "01.png"
        Image.new("RGB", (640, 360), (35, 90, 170)).save(self.source)

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(config, name, value)
        self._temporary.cleanup()

    # ------------------------------------------------------------------
    def test_prepare_card_renders_and_validates_output(self):
        card = self.service.prepare_card(
            self.tweet_id,
            {"top": "NUEVO {MAPA|8B3DFF}", "bottom": "FORTNITEMARES · 01/10",
             "resolution": "native", "backend": "cpu"},
        )
        self.assertEqual(card["version"], 1)
        self.assertTrue(card["meta"]["verification"]["ok"])
        path = Path(card["output_path"])
        self.assertTrue(path.is_file())
        with Image.open(path) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGBA")
            # Fuente 640x360 (horizontal) -> preset 16:9 del repositorio.
            self.assertEqual((image.width, image.height), (1920, 1080))
        self.assertEqual(self.service.get_tweet(self.tweet_id)["status"], "tarjeta_lista")

    def test_prepare_card_reuses_the_same_card(self):
        first = self.service.prepare_card(
            self.tweet_id, {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}
        )
        second = self.service.prepare_card(
            self.tweet_id, {"top": "C", "bottom": "D", "resolution": "native", "backend": "cpu"}
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["version"], 1)
        self.assertEqual(second["params"]["top"], "C")
        self.assertEqual(len(self.service.store.list_cards(self.tweet_id)), 1)

    def test_render_existing_card_keeps_one_file_per_version(self):
        card = self.service.prepare_card(
            self.tweet_id, {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}
        )
        updated = self.service.render_card(
            card["id"],
            {"top": "OTRO TITULAR", "bottom": "OTRO CONTEXTO", "format": "1:1"},
        )
        self.assertEqual(updated["id"], card["id"])
        self.assertEqual(updated["meta"]["width"], updated["meta"]["height"])
        self.assertTrue(Path(updated["output_path"]).is_file())

    def test_card_without_media_is_refused_with_a_clear_message(self):
        self.service.store.upsert_tweets(
            [{"tweet_id": "999", "source_handle": "c", "text": "solo texto"}]
        )
        with self.assertRaises(DashboardError) as context:
            self.service.prepare_card("999", {"top": "A", "bottom": "B"})
        self.assertIn("no tiene imágenes", str(context.exception))

    def test_analysis_with_manual_provider_needs_no_network(self):
        tweet = self.service.analyse(self.tweet_id, provider="manual")
        self.assertEqual(tweet["status"], "analizado")
        self.assertIsNotNone(tweet["analysis"])
        self.assertEqual(len(tweet["analysis"]["hashtags"]), 5)
        self.assertIn("#khetzalgg", tweet["analysis"]["hashtags"])

    def test_send_card_with_local_delivery(self):
        card = self.service.prepare_card(
            self.tweet_id, {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}
        )
        outcome = self.service.send_card(card["id"], caption="prueba", provider="local")
        self.assertTrue(outcome["delivery"]["ok"])
        self.assertEqual(outcome["delivery"]["method"], "archivo-local")
        self.assertEqual(self.service.get_tweet(self.tweet_id)["status"], "enviado")
        self.assertEqual(len(self.service.deliveries(card["id"])), 1)

    def test_failed_card_marks_the_tweet_as_fallido(self):
        with self.assertRaises(DashboardError):
            self.service.prepare_card(self.tweet_id, {"top": "", "bottom": "B"})
        tweet = self.service.get_tweet(self.tweet_id)
        self.assertEqual(tweet["status"], "fallido")
        self.assertIn("titular", tweet["status_detail"])


class DashboardHttpTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        work = Path(self._temporary.name)
        self._originals = {
            name: getattr(config, name)
            for name in ("VAR_DIR", "DB_PATH", "MEDIA_DIR", "CARDS_DIR", "PROFILES_DIR", "LOGS_DIR")
        }
        config.VAR_DIR = work
        config.DB_PATH = work / "dashboard.sqlite3"
        config.MEDIA_DIR = work / "media"
        config.CARDS_DIR = work / "cards"
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
        media_dir = self.service.media_directory(self.tweet_id)
        media_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 400), (120, 40, 160)).save(media_dir / "01.png")

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
        for name, value in self._originals.items():
            setattr(config, name, value)
        self._temporary.cleanup()

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
        self.assertIn(b"EditImg Dashboard", index)

        for path, marker in (
            ("/static/app.js", b"EditImg Dashboard"),
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
        # No se filtran credenciales, solo indicadores booleanos.
        self.assertNotIn("openai_api_key", state["settings"])
        self.assertIn("openai_api_key_set", state["settings"])

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

    def test_full_offline_flow_over_http(self):
        status, payload = self.call("GET", f"/api/tweets?status=todos&limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["tweets"]), 1)

        status, analyzed = self.call(
            "POST", f"/api/tweets/{self.tweet_id}/analyze", {"provider": "manual"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(analyzed["tweet"]["status"], "analizado")

        params = {**analyzed["defaults"], "resolution": "native", "backend": "cpu"}
        params["top"] = "NOVEDAD {FORTNITE|8B3DFF}"
        status, created = self.call(
            "POST", f"/api/tweets/{self.tweet_id}/card", {"params": params}
        )
        self.assertEqual(status, 201)
        card_id = created["card"]["id"]
        self.assertTrue(created["card"]["meta"]["verification"]["ok"])

        status, image = self.call("GET", f"/api/cards/{card_id}/image", raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(image.startswith(b"\x89PNG"))

        status, sent = self.call(
            "POST", f"/api/cards/{card_id}/send", {"provider": "local", "caption": "hola"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(sent["delivery"]["ok"])

        status, deliveries = self.call("GET", f"/api/cards/{card_id}/deliveries")
        self.assertEqual(status, 200)
        self.assertEqual(len(deliveries["deliveries"]), 1)

    def test_invalid_requests_are_rejected_cleanly(self):
        status, payload = self.call("GET", "/api/no-existe")
        self.assertEqual(status, 404)
        self.assertIn("error", payload)

        status, payload = self.call("POST", "/api/tweets/999999/analyze", {"provider": "manual"})
        self.assertEqual(status, 400)
        self.assertIn("no existe", payload["error"])

        status, payload = self.call(
            "POST", f"/api/cards/424242/render", {"params": {"top": "A", "bottom": "B"}}
        )
        self.assertEqual(status, 400)

    def test_path_traversal_is_blocked(self):
        status, payload = self.call("GET", "/static/../config.py")
        self.assertIn(status, (403, 404))
        self.assertIn("error", payload)

    def test_cards_have_one_file_per_tweet(self):
        status, created = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/card",
            {"params": {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}},
        )
        self.assertEqual(status, 201)
        first_path = created["card"]["output_path"]
        status, again = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/card",
            {"params": {"top": "C", "bottom": "D", "resolution": "native", "backend": "cpu"}},
        )
        self.assertEqual(status, 201)
        self.assertEqual(again["card"]["id"], created["card"]["id"])
        self.assertEqual(again["card"]["output_path"], first_path)


if __name__ == "__main__":
    unittest.main()
