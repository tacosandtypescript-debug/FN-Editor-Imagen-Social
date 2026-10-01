"""Tests del sondeo: disparo manual, sondeo periódico y lectura por lotes.

Cubren una regresión real: al arrancar con `--no-poller` no existía el hilo
del sondeador, así que el botón «Buscar ahora» avisaba de que había empezado
pero no hacía nada y el navegador nunca se abría.
"""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import config  # noqa: E402
from dashboard.poller import Poller  # noqa: E402
from dashboard.providers import timelines as timeline_providers  # noqa: E402
from dashboard.providers.base import ProviderStatus  # noqa: E402
from dashboard.service import DashboardService  # noqa: E402
from dashboard.store import Store  # noqa: E402


def wait_until(predicate, timeout=15.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class FakeProvider:
    """Proveedor de prueba que registra cuántas veces y con qué se le llama."""

    name = "browser"

    def __init__(self, results, delay=0.0):
        self.results = results
        self.delay = delay
        self.batch_calls = []
        self.single_calls = []

    def status(self):
        return ProviderStatus(self.name, True, "de prueba")

    def fetch_many(self, handles, on_progress=None):
        self.batch_calls.append(list(handles))
        for position, handle in enumerate(handles, 1):
            if on_progress is not None:
                on_progress(
                    {"provider": self.name, "handle": handle, "index": position, "total": len(handles)}
                )
            if self.delay:
                time.sleep(self.delay)
        return {handle: self.results.get(handle, []) for handle in handles}

    def fetch(self, handle):
        self.single_calls.append(handle)
        return self.results.get(handle, [])


class BatchFetchTests(unittest.TestCase):
    def setUp(self):
        self.original = timeline_providers.build_provider
        self.addCleanup(setattr, timeline_providers, "build_provider", self.original)

    def test_all_accounts_are_read_in_a_single_provider_call(self):
        """Una sola sesión de navegador para todas las cuentas, no una por cuenta."""
        provider = FakeProvider({"a": [{"tweet_id": "1"}], "b": [{"tweet_id": "2"}]})
        timeline_providers.build_provider = lambda name: provider

        outcome = timeline_providers.fetch_timelines_batch(["a", "b", "c"], "browser")
        self.assertEqual(outcome["provider"], "browser")
        self.assertEqual(len(provider.batch_calls), 1)
        self.assertEqual(provider.batch_calls[0], ["a", "b", "c"])
        self.assertEqual(provider.single_calls, [])

    def test_a_failing_account_does_not_stop_the_others(self):
        provider = FakeProvider({"a": [{"tweet_id": "1"}], "b": []})
        provider.results["b"] = RuntimeError("cuenta privada")
        timeline_providers.build_provider = lambda name: provider

        outcome = timeline_providers.fetch_timelines_batch(["a", "b"], "browser")
        self.assertIsInstance(outcome["results"]["b"], RuntimeError)
        self.assertEqual(len(outcome["results"]["a"]), 1)

    def test_falls_back_to_the_next_provider_when_every_account_fails(self):
        failing = FakeProvider({"a": RuntimeError("bloqueado")})
        working = FakeProvider({"a": [{"tweet_id": "9"}]})
        working.name = "nitter"

        def factory(name):
            return failing if name == "browser" else working

        timeline_providers.build_provider = factory
        outcome = timeline_providers.fetch_timelines_batch(["a"], "browser")
        self.assertEqual(outcome["provider"], "nitter")
        self.assertEqual(outcome["results"]["a"], [{"tweet_id": "9"}])
        self.assertTrue(outcome["attempts"])

    def test_empty_list_is_a_no_op(self):
        outcome = timeline_providers.fetch_timelines_batch([], "browser")
        self.assertEqual(outcome["results"], {})
        self.assertIsNone(outcome["provider"])

    def test_only_the_failed_account_is_retried_with_the_next_provider(self):
        """Respaldo por cuenta sin repetir las que ya salieron bien."""
        primary = FakeProvider(
            {
                "a": [{"tweet_id": "1"}],
                "b": RuntimeError("no indexada"),
                "c": [{"tweet_id": "3"}],
            }
        )
        primary.name = "browser"
        secondary = FakeProvider({"b": [{"tweet_id": "2"}]})
        secondary.name = "nitter"

        timeline_providers.build_provider = (
            lambda name: primary if name == "browser" else secondary
        )
        outcome = timeline_providers.fetch_timelines_batch(["a", "b", "c"], "browser")

        # El secundario solo recibe la cuenta que falló.
        self.assertEqual(secondary.batch_calls, [["b"]])
        self.assertEqual(len(primary.batch_calls[0]), 3)
        self.assertEqual(outcome["used"], {"a": "browser", "b": "nitter", "c": "browser"})
        self.assertEqual(outcome["provider"], "mixto:browser+nitter")
        self.assertEqual(outcome["results"]["b"], [{"tweet_id": "2"}])


class PollerTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
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
        self.service.store.add_account("una")
        self.service.store.add_account("otra")
        # Sin red: el enriquecido consulta los mirrors públicos.
        self.service._enrich = lambda tweets: None  # type: ignore[method-assign]

        self.original_factory = timeline_providers.build_provider
        self.addCleanup(setattr, timeline_providers, "build_provider", self.original_factory)

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(config, name, value)
        self._temporary.cleanup()

    def _provider(self, results=None, delay=0.0):
        provider = FakeProvider(
            results
            if results is not None
            else {
                "una": [{"tweet_id": "100", "source_handle": "una", "text": "hola"}],
                "otra": [{"tweet_id": "200", "source_handle": "otra", "text": "hey"}],
            },
            delay=delay,
        )
        timeline_providers.build_provider = lambda name: provider
        return provider

    def test_manual_trigger_works_when_periodic_polling_is_off(self):
        """La regresión: sin hilo no había forma de que el botón hiciera nada."""
        self._provider()
        poller = Poller(self.service, periodic=False)
        self.addCleanup(poller.stop)
        poller.start()
        self.assertTrue(poller.running)
        self.assertFalse(poller.status()["periodic"])

        self.assertTrue(poller.trigger())
        self.assertTrue(
            wait_until(lambda: poller.status()["last_run"] is not None),
            "el sondeo manual no llegó a ejecutarse",
        )
        self.assertFalse(poller.status()["busy"])
        self.assertEqual(poller.status()["last_run"]["new"], 2)
        self.assertEqual(len(self.service.list_tweets(limit=10)), 2)

    def test_periodic_flag_is_reported(self):
        poller = Poller(self.service, periodic=True)
        self.addCleanup(poller.stop)
        self.assertTrue(poller.status()["periodic"])
        self.assertFalse(poller.running)

    def test_trigger_is_refused_while_a_poll_is_running(self):
        self._provider(delay=1.5)
        poller = Poller(self.service, periodic=False)
        self.addCleanup(poller.stop)
        poller.start()

        self.assertTrue(poller.trigger())
        self.assertTrue(wait_until(lambda: poller.status()["busy"], timeout=5))
        self.assertFalse(poller.trigger())
        self.assertTrue(wait_until(lambda: not poller.status()["busy"], timeout=15))

    def test_progress_is_reported_during_the_poll(self):
        self._provider(delay=0.8)
        poller = Poller(self.service, periodic=False)
        self.addCleanup(poller.stop)
        poller.start()
        poller.trigger()
        self.assertTrue(wait_until(lambda: poller.status()["progress"]["total"] > 0, timeout=5))
        progress = poller.status()["progress"]
        self.assertEqual(progress["total"], 2)
        self.assertTrue(wait_until(lambda: not poller.status()["busy"], timeout=15))
        self.assertFalse(poller.status()["progress"]["running"])

    def test_polling_with_no_active_accounts_is_skipped(self):
        self.service.store.set_account_active("una", False)
        self.service.store.set_account_active("otra", False)
        self._provider()
        poller = Poller(self.service, periodic=False)
        self.addCleanup(poller.stop)
        poller.start()
        poller.trigger()
        self.assertTrue(wait_until(lambda: poller.status()["last_run"] is not None, timeout=10))
        self.assertEqual(poller.status()["last_run"]["new"], 0)
        self.assertIn("sin cuentas activas", poller.status()["last_run"].get("skipped", ""))

    def test_one_broken_account_does_not_prevent_the_other(self):
        self._provider({"una": RuntimeError("bloqueada"), "otra": [{"tweet_id": "7", "source_handle": "otra"}]})
        poller = Poller(self.service, periodic=False)
        self.addCleanup(poller.stop)
        poller.start()
        poller.trigger()
        self.assertTrue(wait_until(lambda: poller.status()["last_run"] is not None, timeout=10))

        last = poller.status()["last_run"]
        by_handle = {item["handle"]: item for item in last["accounts"]}
        self.assertFalse(by_handle["una"]["ok"])
        self.assertTrue(by_handle["otra"]["ok"])
        self.assertEqual(last["new"], 1)
        account = self.service.store.get_account("una")
        self.assertIn("bloqueada", account["last_error"])

    def test_a_total_failure_marks_every_account_with_the_reason(self):
        from dashboard.providers.base import ProviderError

        original_batch = timeline_providers.fetch_timelines_batch
        self.addCleanup(setattr, timeline_providers, "fetch_timelines_batch", original_batch)

        def explode(handles, preferred=None, on_progress=None):
            raise ProviderError("sin red")

        timeline_providers.fetch_timelines_batch = explode
        poller = Poller(self.service, periodic=False)
        self.addCleanup(poller.stop)
        poller.start()
        poller.trigger()
        self.assertTrue(wait_until(lambda: poller.status()["last_run"] is not None, timeout=10))

        last = poller.status()["last_run"]
        self.assertEqual(last["new"], 0)
        self.assertTrue(all(not item["ok"] for item in last["accounts"]))
        self.assertIn("sin red", self.service.store.get_account("una")["last_error"])


    def test_progress_advances_while_the_batch_is_still_reading(self):
        """Regresión: el lote tardaba minutos y el progreso se quedaba en 0/24."""
        seen: list[dict] = []

        def slow_fetch(handles, preferred=None, on_progress=None):
            for position, handle in enumerate(handles, 1):
                payload = {"provider": "browser", "handle": handle, "index": position,
                           "total": len(handles)}
                if on_progress is not None:
                    on_progress(payload)
                seen.append(dict(payload))
            return {
                "provider": "browser",
                "results": {handle: [] for handle in handles},
                "attempts": [],
            }

        original = timeline_providers.fetch_timelines_batch
        self.addCleanup(setattr, timeline_providers, "fetch_timelines_batch", original)
        timeline_providers.fetch_timelines_batch = slow_fetch

        progress: dict = {}
        self.service.store.add_account("alpha")
        self.service.store.add_account("beta")
        self.service.store.add_account("gamma")
        expected = [
            account["handle"] for account in self.service.store.list_accounts(active_only=True)
        ]
        self.service.poll(progress=progress)

        # Se avisó de cada cuenta, en el mismo orden en que se van a procesar.
        self.assertEqual([item["handle"] for item in seen], expected)
        self.assertIn("alpha", expected)
        # Y el progreso queda cerrado al terminar.
        self.assertEqual(progress["total"], len(expected))
        self.assertEqual(progress["done"], len(expected))
        self.assertFalse(progress["running"])
        self.assertIsNone(progress["current"])
        self.assertEqual(progress["provider"], "browser")

    def test_batch_receives_the_progress_callback(self):
        received = {}

        def capture(handles, preferred=None, on_progress=None):
            received["callback"] = on_progress
            return {"provider": "fake", "results": {h: [] for h in handles}, "attempts": []}

        original = timeline_providers.fetch_timelines_batch
        self.addCleanup(setattr, timeline_providers, "fetch_timelines_batch", original)
        timeline_providers.fetch_timelines_batch = capture

        self.service.store.add_account("una")
        self.service.poll(progress={})
        self.assertIsNotNone(received.get("callback"), "el lote debe poder informar del avance")


class ServicePollTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
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
        self.service._enrich = lambda tweets: None  # type: ignore[method-assign]

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(config, name, value)
        self._temporary.cleanup()

    def test_poll_without_accounts_is_reported(self):
        from dashboard.service import DashboardError

        with self.assertRaises(DashboardError):
            self.service.poll()

    def test_poll_uses_the_batch_provider_and_reports_progress(self):
        self.service.store.add_account("una")
        self.service.store.add_account("otra")

        original = timeline_providers.fetch_timelines_batch
        self.addCleanup(setattr, timeline_providers, "fetch_timelines_batch", original)
        timeline_providers.fetch_timelines_batch = lambda handles, preferred=None, on_progress=None: {
            "provider": "browser",
            "results": {
                "una": [{"tweet_id": "11", "source_handle": "una", "text": "nueva"}],
                "otra": RuntimeError("privada"),
            },
            "attempts": [],
        }

        progress: dict = {}
        outcome = self.service.poll(progress=progress)

        self.assertEqual(outcome["new"], 1)
        self.assertEqual(outcome["provider"], "browser")
        by_handle = {item["handle"]: item for item in outcome["accounts"]}
        self.assertTrue(by_handle["una"]["ok"])
        self.assertFalse(by_handle["otra"]["ok"])
        # El progreso queda completo y cerrado.
        self.assertEqual(progress["total"], 2)
        self.assertEqual(progress["done"], 2)
        self.assertFalse(progress["running"])
        self.assertEqual(len(self.service.list_tweets(limit=10)), 1)


if __name__ == "__main__":
    unittest.main()
