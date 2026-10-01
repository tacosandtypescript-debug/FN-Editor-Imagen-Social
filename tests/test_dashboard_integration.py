"""Tests de integración del dashboard.

Ejercitan el compositor real y la API HTTP completa sin salir a la red: los
medios se colocan ya descargados en el directorio de trabajo y el análisis y
la entrega usan los proveedores que no necesitan credenciales.
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

from PIL import Image  # noqa: E402

from dashboard import config  # noqa: E402
from dashboard.poller import Poller  # noqa: E402
from dashboard.providers import analysis as analysis_providers  # noqa: E402
from dashboard.server import create_server  # noqa: E402
from dashboard.service import DashboardError, DashboardService  # noqa: E402
from dashboard.store import Store  # noqa: E402

#: Se guarda para restaurarlo tras los stubs de análisis.
original_analyse = analysis_providers.analyse_tweet


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

        # Los tests nunca deben invocar al CLI de Codex real: sería lento y
        # consumiría la suscripción del usuario en cada ejecución.
        key = "DASHBOARD_ANALYSIS_PROVIDER"
        self._provider_before = os.environ.get(key)
        os.environ[key] = "manual"
        self.addCleanup(self._restore_provider, key)

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

    # --- botón «Procesar» ---------------------------------------------
    def test_process_tweet_does_analysis_media_and_card_in_one_step(self):
        result = self.service.process_tweet(self.tweet_id, {"resolution": "native", "backend": "cpu"})
        self.assertIn("análisis de texto", result["steps"])
        self.assertIn("descarga de medios y composición", result["steps"])
        self.assertEqual(result["card"]["version"], 1)
        self.assertTrue(result["card"]["meta"]["verification"]["ok"])
        self.assertEqual(result["editor_url"], f"/editor.html?card={result['card']['id']}")
        self.assertEqual(result["tweet"]["status"], "tarjeta_lista")

    def test_process_tweet_refuses_publications_without_images(self):
        self.service.store.upsert_tweets(
            [{"tweet_id": "888", "source_handle": "c", "text": "solo texto"}]
        )
        with self.assertRaises(DashboardError) as context:
            self.service.process_tweet("888")
        self.assertIn("no tiene imágenes", str(context.exception))

    # --- análisis con IA al procesar -----------------------------------
    def _use_provider(self, name):
        """Fija el proveedor configurado durante el test."""
        key = "DASHBOARD_ANALYSIS_PROVIDER"
        original = os.environ.get(key)

        def restore():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original

        os.environ[key] = name
        self.addCleanup(restore)

    def test_process_reanalyses_when_the_stored_analysis_was_a_fallback(self):
        """Lo pedido: al procesar debe analizar ChatGPT, no reusar el relleno."""
        self.service.store.update_tweet(
            self.tweet_id,
            analysis_json={
                "top": "RELLENO",
                "bottom": "RELLENO",
                "hashtags": ["#khetzalgg"],
                "provider": "manual",
            },
            status="analizado",
        )
        self._use_provider("chatgpt")
        captured = self._stub_analysis("NUEVO {TITULAR|FF7A00}", "NUEVO CONTEXTO")

        result = self.service.process_tweet(
            self.tweet_id, {"resolution": "native", "backend": "cpu"}
        )

        self.assertIn("análisis de texto", result["steps"])
        self.assertIn("descarga de medios y composición", result["steps"])
        self.assertEqual(captured["tweet"]["tweet_id"], self.tweet_id)
        self.assertEqual(result["card"]["params"]["top"], "NUEVO {TITULAR|FF7A00}")

    def test_process_does_not_repeat_an_analysis_from_the_active_provider(self):
        """Evita repetir un análisis lento (el de ChatGPT tarda ~1 minuto)."""
        self.service.store.update_tweet(
            self.tweet_id,
            analysis_json={
                "top": "YA HECHO",
                "bottom": "YA HECHO",
                "hashtags": ["#khetzalgg"],
                "provider": "chatgpt",
            },
            status="analizado",
        )
        self._use_provider("chatgpt")

        def explode(*args, **kwargs):
            raise AssertionError("no debería volver a analizar")

        analysis_providers.analyse_tweet = explode
        self.addCleanup(setattr, analysis_providers, "analyse_tweet", original_analyse)

        result = self.service.process_tweet(
            self.tweet_id, {"resolution": "native", "backend": "cpu"}
        )
        self.assertIn("análisis ya existente", result["steps"])
        self.assertEqual(result["card"]["params"]["top"], "YA HECHO")

    def test_force_analysis_overrides_the_cached_one(self):
        self.service.store.update_tweet(
            self.tweet_id,
            analysis_json={"top": "VIEJO", "bottom": "VIEJO", "provider": "chatgpt"},
            status="analizado",
        )
        self._use_provider("chatgpt")
        self._stub_analysis("OTRA VEZ", "OTRA VEZ B")
        result = self.service.process_tweet(
            self.tweet_id, {"resolution": "native", "backend": "cpu"}, force_analysis=True
        )
        self.assertIn("análisis de texto", result["steps"])
        self.assertEqual(result["card"]["params"]["top"], "OTRA VEZ")

    def test_test_analysis_does_not_save_or_touch_the_card(self):
        result = self.service.test_analysis(self.tweet_id, provider="manual")
        self.assertFalse(result["saved"])
        self.assertTrue(result["analysis"]["top"])
        # No se guardó análisis ni se creó tarjeta.
        self.assertIsNone(self.service.get_tweet(self.tweet_id)["analysis"])
        self.assertIsNone(self.service.store.latest_card(self.tweet_id))

    def test_test_analysis_reports_provider_failures(self):
        def explode(*args, **kwargs):
            raise analysis_providers.ProviderError("falta iniciar sesión en ChatGPT")

        analysis_providers.analyse_tweet = explode
        self.addCleanup(setattr, analysis_providers, "analyse_tweet", original_analyse)

        with self.assertRaises(DashboardError) as context:
            self.service.test_analysis(self.tweet_id, provider="chatgpt")
        self.assertIn("iniciar sesión", str(context.exception))

    def test_analysis_status_exposes_the_chatgpt_profile(self):
        status = self.service.analysis_status()
        self.assertIn("chatgpt", status["chatgpt_profile"])
        self.assertIn(status["chatgpt_logged_in"], (True, False, None))
        self.assertIn("codex_auth_mode", status)
        names = {provider["name"] for provider in status["providers"]}
        self.assertEqual(names, {"codex", "openai", "chatgpt", "manual"})

    # --- regenerar solo el texto --------------------------------------
    def _stub_analysis(self, top, bottom, caption="", hashtags=None):
        """Sustituye el proveedor de análisis y captura lo que recibe."""
        captured = {}

        def fake_analyse(tweet, preferred=None):
            captured["tweet"] = dict(tweet)
            return {
                "top": top,
                "bottom": bottom,
                "caption": caption,
                "hashtags": hashtags or ["#khetzalgg"],
                "suggested_format": "9:16",
                "suggested_style": None,
                "reasoning": "stub",
                "provider": "stub",
            }

        analysis_providers.analyse_tweet = fake_analyse
        self.addCleanup(setattr, analysis_providers, "analyse_tweet", original_analyse)
        return captured

    def test_regenerate_text_changes_only_the_text_and_keeps_everything_else(self):
        card = self.service.prepare_card(
            self.tweet_id,
            {"top": "VIEJO TITULAR", "bottom": "VIEJO CONTEXTO", "caption": "viejo",
             "format": "1:1", "fit": "contain", "resolution": "native", "backend": "cpu"},
        )
        media_before = sorted(p.name for p in self.service.media_directory(self.tweet_id).glob("*"))

        captured = self._stub_analysis("NUEVO {TITULAR|8B3DFF}", "NUEVO CONTEXTO · 01/10", "nuevo caption")
        result = self.service.regenerate_text(card["id"], render=False)

        params = result["card"]["params"]
        self.assertEqual(params["top"], "NUEVO {TITULAR|8B3DFF}")
        self.assertEqual(params["bottom"], "NUEVO CONTEXTO · 01/10")
        self.assertIn("nuevo caption", params["caption"])
        # Nada más cambia: ajustes de composición y medios intactos.
        self.assertEqual(params["format"], "1:1")
        self.assertEqual(params["fit"], "contain")
        self.assertEqual(params["resolution"], "native")
        self.assertEqual(params["backend"], "cpu")
        self.assertEqual(
            sorted(p.name for p in self.service.media_directory(self.tweet_id).glob("*")),
            media_before,
        )
        self.assertFalse(result["rendered"])
        self.assertTrue(result["text_only"])
        _ = captured

    def test_regenerate_text_sends_the_previous_version_and_the_instruction(self):
        card = self.service.prepare_card(
            self.tweet_id,
            {"top": "VIEJO TITULAR", "bottom": "VIEJO CONTEXTO", "caption": "viejo caption",
             "resolution": "native", "backend": "cpu"},
        )
        captured = self._stub_analysis("OTRO TITULAR", "OTRO CONTEXTO", "otro caption")
        self.service.regenerate_text(card["id"], instructions="más corto", render=False)

        sent = captured["tweet"]
        self.assertEqual(sent["previous"]["top"], "VIEJO TITULAR")
        self.assertEqual(sent["previous"]["bottom"], "VIEJO CONTEXTO")
        self.assertEqual(sent["instructions"], "más corto")

    def test_regenerate_text_recomposes_when_asked(self):
        card = self.service.prepare_card(
            self.tweet_id, {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}
        )
        self._stub_analysis("TITULAR NUEVO", "CONTEXTO NUEVO", "caption nuevo")
        result = self.service.regenerate_text(card["id"], render=True)
        self.assertTrue(result["rendered"])
        self.assertTrue(Path(result["card"]["output_path"]).is_file())
        self.assertTrue(result["card"]["meta"]["verification"]["ok"])

    def test_regenerate_text_never_touches_the_tweet_text(self):
        """El texto original de la publicación es la fuente y no se reescribe."""
        before = self.service.get_tweet(self.tweet_id)["text"]
        card = self.service.prepare_card(
            self.tweet_id, {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}
        )
        self._stub_analysis("TITULAR NUEVO", "CONTEXTO NUEVO", "caption nuevo")
        self.service.regenerate_text(card["id"], render=False)
        self.assertEqual(self.service.get_tweet(self.tweet_id)["text"], before)

    def test_regenerate_missing_card_is_reported(self):
        with self.assertRaises(DashboardError):
            self.service.regenerate_text(999999)

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

    def test_listed_tweets_expose_the_editor_link_once_processed(self):
        without = self.service.list_tweets(limit=5)[0]
        self.assertFalse(without["has_card"])
        self.assertIsNone(without["card_id"])

        card = self.service.prepare_card(
            self.tweet_id, {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}
        )
        with_card = self.service.list_tweets(limit=5)[0]
        self.assertTrue(with_card["has_card"])
        self.assertEqual(with_card["card_id"], card["id"])
        self.assertEqual(with_card["editor_url"], f"/editor.html?card={card['id']}")

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

        # Nunca se invoca al CLI de Codex real desde los tests.
        key = "DASHBOARD_ANALYSIS_PROVIDER"
        self._provider_before = os.environ.get(key)
        os.environ[key] = "manual"
        self.addCleanup(self._restore_provider, key)

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
        # Los hilos de la cola de trabajos deben parar antes de borrar el
        # directorio temporal: si no, siguen usando la base de datos.
        self.poller.stop()
        self.service.shutdown()
        for name, value in self._originals.items():
            setattr(config, name, value)
        self._temporary.cleanup()

    def _restore_provider(self, key):
        if self._provider_before is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = self._provider_before

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

        # Sin pedir nada se sirve la reducida (ligera, para listas); el PNG
        # original se pide explícitamente con size=full.
        status, image = self.call("GET", f"/api/cards/{card_id}/image?size=full", raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(image.startswith(b"\x89PNG"))

        status, reducida = self.call("GET", f"/api/cards/{card_id}/image", raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(reducida.startswith(b"\xff\xd8"), "la vista previa es JPEG")
        self.assertLess(len(reducida), len(image))

        status, sent = self.call(
            "POST", f"/api/cards/{card_id}/send", {"provider": "local", "caption": "hola", "sync": True}
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

    def test_editor_page_is_served_on_its_own_route(self):
        for path in ("/editor", "/editor.html"):
            with self.subTest(path=path):
                status, body = self.call("GET", path, raw=True)
                self.assertEqual(status, 200)
                self.assertIn(b"Editor de tarjeta", body)
                self.assertIn(b"Regenerar texto y caption", body)

        status, body = self.call("GET", "/static/editor.js", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"regenerate-text", body)

    def test_process_endpoint_runs_the_whole_flow(self):
        # `sync` fuerza el camino directo; el normal es en segundo plano.
        status, result = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/process",
            {"params": {"resolution": "native", "backend": "cpu"}, "sync": True},
        )
        self.assertEqual(status, 201)
        self.assertTrue(result["card"]["meta"]["verification"]["ok"])
        self.assertEqual(result["editor_url"], f"/editor.html?card={result['card']['id']}")
        self.assertIn("descarga de medios y composición", result["steps"])

        # Y la bandeja ya ofrece el botón de abrir el editor. Se pide
        # `pending=0` porque por defecto lo ya procesado queda oculto.
        status, listing = self.call("GET", f"/api/tweets?status=todos&limit=5&pending=0")
        tweet = listing["tweets"][0]
        self.assertTrue(tweet["has_card"])
        self.assertEqual(tweet["card_id"], result["card"]["id"])
        self.assertTrue(tweet["is_processed"])
        self.assertTrue(tweet["posted_relative"])
        self.assertTrue(tweet["posted_absolute"])

    def test_processed_publications_leave_the_pending_view(self):
        """Lo procesado deja de estorbar en la vista por defecto."""
        status, _ = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/process",
            {"params": {"resolution": "native", "backend": "cpu"}, "sync": True},
        )
        self.assertEqual(status, 201)

        status, pending = self.call("GET", "/api/tweets?status=todos&limit=10")
        self.assertEqual(pending["tweets"], [], "ya no debería aparecer como pendiente")

        status, everything = self.call("GET", "/api/tweets?status=todos&limit=10&pending=0")
        self.assertEqual(len(everything["tweets"]), 1)
        self.assertTrue(everything["tweets"][0]["is_processed"])

    def test_regenerate_text_endpoint_keeps_composition_settings(self):
        status, created = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/card",
            {"params": {"top": "A", "bottom": "B", "format": "1:1", "resolution": "native",
                        "backend": "cpu"}},
        )
        self.assertEqual(status, 201)
        card_id = created["card"]["id"]

        status, result = self.call(
            "POST",
            f"/api/cards/{card_id}/regenerate-text",
            {"provider": "manual", "instructions": "más corto", "render": False, "sync": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["text_only"])
        self.assertFalse(result["rendered"])
        params = result["card"]["params"]
        self.assertEqual(params["format"], "1:1")
        self.assertEqual(params["resolution"], "native")
        self.assertTrue(params["top"])
        self.assertTrue(params["bottom"])

    def test_regenerate_text_on_missing_card_is_rejected(self):
        status, payload = self.call(
            "POST", "/api/cards/987654/regenerate-text", {"provider": "manual", "sync": True}
        )
        self.assertEqual(status, 400)
        self.assertIn("no existe", payload["error"])

    # --- vista de procesadas ------------------------------------------
    def test_the_cards_view_lists_processed_cards(self):
        """La vista que faltaba: ver lo ya procesado para enviarlo o editarlo."""
        status, created = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/card",
            {"params": {"top": "UN TITULAR", "bottom": "UN CONTEXTO",
                        "resolution": "native", "backend": "cpu"}},
        )
        self.assertEqual(status, 201)
        card_id = created["card"]["id"]

        status, payload = self.call("GET", "/api/cards?limit=50")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["cards"]), 1)
        item = payload["cards"][0]
        self.assertEqual(item["card"]["id"], card_id)
        self.assertEqual(item["editor_url"], f"/editor.html?card={card_id}")
        self.assertFalse(item["sent"])
        self.assertIsNone(item["last_delivery"])
        # Trae lo necesario para mostrarla sin más consultas.
        self.assertEqual(item["tweet"]["author_handle"], "Cuenta")
        self.assertIn("posted_relative", item["tweet"])
        self.assertIn("thumbs", item["tweet"])

    def test_the_cards_view_marks_what_was_already_sent(self):
        status, created = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/card",
            {"params": {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}},
        )
        self.assertEqual(status, 201)
        card_id = created["card"]["id"]
        status, _ = self.call(
            "POST", f"/api/cards/{card_id}/send",
            {"provider": "local", "caption": "hola", "sync": True},
        )
        self.assertEqual(status, 200)

        status, payload = self.call("GET", "/api/cards?limit=50")
        item = payload["cards"][0]
        self.assertTrue(item["sent"])
        self.assertEqual(item["last_delivery"]["status"], "ok")

    def test_process_runs_in_the_background_by_default(self):
        """La petición responde al instante y el trabajo sigue en el servidor."""
        status, payload = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/process",
            {"params": {"resolution": "native", "backend": "cpu"}},
        )
        self.assertEqual(status, 202)
        self.assertTrue(payload["queued"])
        job_id = payload["job"]["id"]
        self.assertIn(payload["job"]["state"], ("en_espera", "en_curso"))

        deadline = time.time() + 150
        state = None
        while time.time() < deadline:
            status, job = self.call("GET", f"/api/jobs/{job_id}")
            self.assertEqual(status, 200)
            state = job["job"]["state"]
            if state in ("hecho", "fallido"):
                break
            time.sleep(1)
        self.assertEqual(state, "hecho")
        status, listing = self.call("GET", "/api/tweets?status=todos&limit=5&pending=0")
        self.assertTrue(listing["tweets"][0]["has_card"])

    def test_regenerate_and_send_are_queued_by_default(self):
        status, created = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/card",
            {"params": {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}},
        )
        card_id = created["card"]["id"]

        status, payload = self.call(
            "POST", f"/api/cards/{card_id}/regenerate-text", {"provider": "manual", "render": False}
        )
        self.assertEqual(status, 202)
        self.assertTrue(payload["queued"])
        finished = self.service.jobs.wait(payload["job"]["id"], timeout=60)
        self.assertEqual(finished.state, "hecho")

        status, payload = self.call(
            "POST", f"/api/cards/{card_id}/send", {"provider": "local"}
        )
        self.assertEqual(status, 202)
        finished = self.service.jobs.wait(payload["job"]["id"], timeout=60)
        self.assertEqual(finished.state, "hecho")

    def test_the_jobs_endpoint_reports_the_queue(self):
        status, payload = self.call("GET", "/api/jobs")
        self.assertEqual(status, 200)
        for key in ("workers", "recent", "busy", "pending", "running"):
            self.assertIn(key, payload)

    def test_an_unknown_job_is_reported(self):
        status, payload = self.call("GET", "/api/jobs/987654")
        self.assertEqual(status, 400)
        self.assertIn("no existe", payload["error"])

    def test_the_cards_tab_exists_in_the_interface(self):
        status, body = self.call("GET", "/", raw=True)
        self.assertIn(b'data-tab="cards"', body)
        self.assertIn("Procesadas".encode(), body)

    def test_the_list_serves_a_small_preview_not_the_full_png(self):
        """Servir el PNG de 30 MB en una miniatura hundía el móvil."""
        status, created = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/card",
            {"params": {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}},
        )
        card_id = created["card"]["id"]

        status, reducida, cabeceras = self.call_raw(
            "GET", f"/api/cards/{card_id}/image?size=preview&w=720"
        )
        self.assertEqual(status, 200)
        self.assertEqual(cabeceras.get("Content-Type"), "image/jpeg")
        self.assertLess(len(reducida), 400_000, "la vista previa debería ser ligera")

        status, completa, cabeceras = self.call_raw(
            "GET", f"/api/cards/{card_id}/image?size=full"
        )
        self.assertEqual(status, 200)
        self.assertEqual(cabeceras.get("Content-Type"), "image/png")
        self.assertGreater(
            len(completa), len(reducida), "la completa debe pesar más que la reducida"
        )

    def test_the_preview_is_the_default(self):
        """Sin pedir nada se sirve la reducida, que es lo que usa la lista."""
        status, created = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/card",
            {"params": {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}},
        )
        card_id = created["card"]["id"]
        status, _cuerpo, cabeceras = self.call_raw("GET", f"/api/cards/{card_id}/image")
        self.assertEqual(status, 200)
        self.assertEqual(cabeceras.get("Content-Type"), "image/jpeg")
        self.assertIn("max-age", cabeceras.get("Cache-Control", ""))

    def test_the_reduced_image_keeps_the_card_proportions(self):
        status, created = self.call(
            "POST",
            f"/api/tweets/{self.tweet_id}/card",
            {"params": {"top": "A", "bottom": "B", "resolution": "native", "backend": "cpu"}},
        )
        meta = created["card"]["meta"]

        import io

        from PIL import Image

        status, cuerpo, _ = self.call_raw(
            "GET", f"/api/cards/{created['card']['id']}/image?size=preview&w=480"
        )
        self.assertEqual(status, 200)
        with Image.open(io.BytesIO(cuerpo)) as imagen:
            ancho, alto = imagen.size
        self.assertEqual(max(ancho, alto), 480)
        self.assertAlmostEqual(ancho / alto, meta["width"] / meta["height"], places=2)

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


class AccessControlTests(unittest.TestCase):
    """El dashboard puede quedar expuesto a la red local: debe pedir clave."""

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
