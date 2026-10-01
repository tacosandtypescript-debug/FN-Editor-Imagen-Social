"""Tests del descubrimiento y la rotación de instancias Nitter.

Motivo real: de las tres instancias configuradas en este proyecto, en un
momento dado solo respondía una (las otras daban 500 y 403). Depender de un
único dominio de terceros es frágil, así que el proveedor consulta el registro
público y valida las candidatas.
"""

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import config  # noqa: E402
from dashboard.providers import timelines as timeline_providers  # noqa: E402
from dashboard.providers.base import ProviderError  # noqa: E402

RSS_OK = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <item>
      <title>ALGO PASA EN FORTNITE</title>
      <dc:creator>@cuenta</dc:creator>
      <description><![CDATA[<p>ALGO PASA EN FORTNITE</p>]]></description>
      <pubDate>Thu, 01 Oct 2026 07:37:01 GMT</pubDate>
      <guid isPermaLink="false">111</guid>
      <link>https://nitter.example/cuenta/status/111#m</link>
    </item>
  </channel>
</rss>
"""

RSS_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>vacio</title></channel></rss>
"""


class _Handler(BaseHTTPRequestHandler):
    """Servidor que hace de registro y de instancias, según la ruta."""

    registry = {"hosts": []}
    #: dominio -> "OK" | "500" | "403" | "VACIO"
    behavior: dict = {}

    def log_message(self, *args):  # noqa: D102
        return

    def _send(self, status, body: bytes, content_type="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/registry"):
            self._send(200, json.dumps(self.registry).encode(), "application/json")
            return
        if self.path.startswith("/rate-limited"):
            self._send(429, b"too many")
            return
        # Ruta de instancia: /<clave>/<usuario>/rss
        parts = [p for p in self.path.split("/") if p]
        clave = parts[0] if parts else ""
        mode = self.behavior.get(clave, "OK")
        if mode == "500":
            self._send(500, b"boom")
        elif mode == "403":
            self._send(403, b"forbidden")
        elif mode == "VACIO":
            self._send(200, RSS_EMPTY.encode(), "application/rss+xml")
        else:
            self._send(200, RSS_OK.encode(), "application/rss+xml")


class NitterDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._temporary = None
        _Handler.behavior = {}
        _Handler.registry = {"hosts": []}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

        # El registro y las instancias comparten servidor: se distinguen por ruta.
        self.original_registry = config.Settings().nitter_registry_url
        self._env_backup = {}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        import os

        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _set_env(self, **values):
        import os

        for key, value in values.items():
            self._env_backup.setdefault(key, os.environ.get(key))
            os.environ[key] = str(value)

    def _provider(self, instances):
        return timeline_providers.NitterTimeline(tuple(instances))

    # ------------------------------------------------------------------
    def test_uses_the_first_instance_that_works(self):
        self._set_env(DASHBOARD_NITTER_DISCOVERY=0)
        provider = self._provider([f"{self.base}/caida", f"{self.base}/buena"])
        _Handler.behavior = {"caida": "500", "buena": "OK"}
        tweets = provider.fetch("cuenta")
        self.assertEqual(len(tweets), 1)
        self.assertTrue(provider._preferred.endswith("/buena"))  # noqa: SLF001

    def test_remembers_the_instance_that_worked(self):
        self._set_env(DASHBOARD_NITTER_DISCOVERY=0)
        provider = self._provider([f"{self.base}/caida", f"{self.base}/buena"])
        _Handler.behavior = {"caida": "500", "buena": "OK"}
        provider.fetch("cuenta")
        # En la siguiente lectura se prueba primero la que funcionó.
        self.assertTrue(provider.candidates()[0].endswith("/buena"))

    def test_an_empty_feed_is_treated_as_a_soft_failure(self):
        """Una instancia puede responder sin tener la cuenta indexada."""
        self._set_env(DASHBOARD_NITTER_DISCOVERY=0)
        provider = self._provider([f"{self.base}/vacia", f"{self.base}/buena"])
        _Handler.behavior = {"vacia": "VACIO", "buena": "OK"}
        tweets = provider.fetch("cuenta")
        self.assertEqual(len(tweets), 1)
        self.assertTrue(provider._preferred.endswith("/buena"))  # noqa: SLF001

    def test_discovers_instances_from_the_registry_when_all_configured_fail(self):
        self._set_env(
            DASHBOARD_NITTER_DISCOVERY=1,
            DASHBOARD_NITTER_REGISTRY=f"{self.base}/registry",
            DASHBOARD_NITTER_MIN_POINTS=40,
        )
        _Handler.registry = {
            "hosts": [
                {"url": f"{self.base}/nueva-buena", "rss": True, "points": 70},
                {"url": f"{self.base}/nueva-floja", "rss": True, "points": 10},
                {"url": f"{self.base}/sin-rss", "rss": False, "points": 90},
                {"url": f"{self.base}/nueva-403", "rss": True, "points": 60},
            ]
        }
        _Handler.behavior = {
            "caida": "500",
            "nueva-floja": "OK",
            "sin-rss": "OK",
            "nueva-403": "403",
            "nueva-buena": "OK",
        }
        provider = self._provider([f"{self.base}/caida"])
        tweets = provider.fetch("cuenta")

        self.assertEqual(len(tweets), 1)
        discovered = provider._discovered  # noqa: SLF001
        # Se descartan las de pocos puntos y las que no declaran RSS.
        self.assertIn(f"{self.base}/nueva-buena", discovered)
        self.assertNotIn(f"{self.base}/nueva-floja", discovered)
        self.assertNotIn(f"{self.base}/sin-rss", discovered)
        # El orden respeta la salud declarada.
        self.assertEqual(discovered[0], f"{self.base}/nueva-buena")

    def test_discovery_can_be_disabled(self):
        self._set_env(
            DASHBOARD_NITTER_DISCOVERY=0,
            DASHBOARD_NITTER_REGISTRY=f"{self.base}/registry",
        )
        _Handler.registry = {"hosts": [{"url": f"{self.base}/nueva-buena", "rss": True, "points": 90}]}
        _Handler.behavior = {"caida": "500"}
        provider = self._provider([f"{self.base}/caida"])
        with self.assertRaises(ProviderError):
            provider.fetch("cuenta")
        self.assertEqual(provider.discover(), [])

    def test_a_broken_registry_does_not_raise(self):
        self._set_env(
            DASHBOARD_NITTER_DISCOVERY=1,
            DASHBOARD_NITTER_REGISTRY=f"{self.base}/rate-limited",
        )
        provider = self._provider([f"{self.base}/caida"])
        _Handler.behavior = {"caida": "500"}
        self.assertEqual(provider.discover(force=True), [])
        with self.assertRaises(ProviderError):
            provider.fetch("cuenta")

    def test_the_registry_is_consulted_once_and_then_cached(self):
        self._set_env(
            DASHBOARD_NITTER_DISCOVERY=1,
            DASHBOARD_NITTER_REGISTRY=f"{self.base}/registry",
        )
        _Handler.registry = {"hosts": [{"url": f"{self.base}/nueva-buena", "rss": True, "points": 90}]}
        provider = self._provider([f"{self.base}/caida"])
        first = provider.discover()
        # Se cambia el registro: sin `force` debe seguir la copia en caché.
        _Handler.registry = {"hosts": []}
        self.assertEqual(provider.discover(), first)

    def test_all_failing_reports_the_reasons(self):
        self._set_env(DASHBOARD_NITTER_DISCOVERY=0)
        _Handler.behavior = {"a": "500", "b": "403"}
        provider = self._provider([f"{self.base}/a", f"{self.base}/b"])
        with self.assertRaises(ProviderError) as context:
            provider.fetch("cuenta")
        message = str(context.exception)
        self.assertIn("500", message)
        self.assertIn("403", message)

    def test_status_reports_both_sources(self):
        provider = self._provider([f"{self.base}/a"])
        provider._discovered = [f"{self.base}/b"]  # noqa: SLF001
        status = provider.status()
        self.assertTrue(status.available)
        self.assertIn("descubierta", status.detail)


if __name__ == "__main__":
    unittest.main()
