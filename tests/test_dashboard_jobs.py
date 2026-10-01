"""Tests de la cola de trabajos.

Motivo real: «Procesar» tardaba entre treinta y sesenta segundos y el navegador
esperaba todo ese rato. Si el móvil bloqueaba la pantalla o la conexión
parpadeaba, la petición se cortaba, la interfaz decía «fallido» y el usuario no
veía el botón de abrir el editor... aunque el servidor hubiera terminado bien.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import config  # noqa: E402
from dashboard.jobs import DONE, FAILED, QUEUED, RUNNING, JobQueue  # noqa: E402
from dashboard.service import DashboardError, DashboardService  # noqa: E402
from dashboard.store import Store  # noqa: E402


def wait_until(predicate, timeout=15.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class JobQueueTests(unittest.TestCase):
    def setUp(self):
        self.queue = JobQueue(workers=1)
        self.addCleanup(self.queue.stop)

    def test_a_job_runs_and_reports_done(self):
        job = self.queue.submit("prueba", "etiqueta", lambda: {"ok": True})
        self.assertEqual(job.state, QUEUED)
        finished = self.queue.wait(job.id)
        self.assertEqual(finished.state, DONE)
        self.assertEqual(finished.result, {"ok": True})
        self.assertIsNotNone(finished.started_at)
        self.assertIsNotNone(finished.finished_at)

    def test_a_failing_job_is_marked_and_keeps_the_reason(self):
        def explode():
            raise ValueError("algo salió mal")

        job = self.queue.submit("prueba", "etiqueta", explode)
        finished = self.queue.wait(job.id)
        self.assertEqual(finished.state, FAILED)
        self.assertIn("algo salió mal", finished.detail)

    def test_the_queue_keeps_running_after_a_failure(self):
        self.queue.submit("prueba", "falla", lambda: 1 / 0)
        second = self.queue.submit("prueba", "funciona", lambda: "bien")
        finished = self.queue.wait(second.id)
        self.assertEqual(finished.state, DONE)
        self.assertEqual(finished.result, "bien")

    def test_jobs_run_in_order_and_one_at_a_time(self):
        """Un solo trabajador a propósito: no se desbordan Codex ni los renders."""
        active = {"now": 0, "max": 0}
        order: list[int] = []

        def work(value):
            def inner():
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
                time.sleep(0.25)
                order.append(value)
                active["now"] -= 1
                return value
            return inner

        jobs = [self.queue.submit("prueba", str(i), work(i)) for i in range(4)]
        for job in jobs:
            self.queue.wait(job.id)
        self.assertEqual(order, [0, 1, 2, 3])
        self.assertEqual(active["max"], 1, "no deberían solaparse")

    def test_state_reports_the_queue(self):
        self.queue.submit("prueba", "lento", lambda: time.sleep(0.6))
        self.queue.submit("prueba", "otro", lambda: None)
        state = self.queue.state()
        self.assertEqual(state["workers"], 1)
        self.assertTrue(state["busy"])
        self.assertGreaterEqual(state["pending"] + state["running"], 1)
        self.assertTrue(any(job["kind"] == "prueba" for job in state["recent"]))

    def test_state_is_quiet_when_idle(self):
        job = self.queue.submit("prueba", "rapido", lambda: None)
        self.queue.wait(job.id)
        state = self.queue.state()
        self.assertFalse(state["busy"])
        self.assertEqual(state["pending"], 0)
        self.assertEqual(state["running"], 0)

    def test_the_history_does_not_grow_without_limit(self):
        queue = JobQueue(workers=1, history=4)
        self.addCleanup(queue.stop)
        for i in range(12):
            queue.wait(queue.submit("prueba", str(i), lambda: None).id)
        self.assertLessEqual(len(queue.state()["recent"]), 12)

    def test_an_unknown_job_is_none(self):
        self.assertIsNone(self.queue.get(9999))


class ServiceQueueTests(unittest.TestCase):
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

        self.service = DashboardService(store=Store(config.DB_PATH), workers=1)
        self.addCleanup(self.service.shutdown)
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
        from PIL import Image

        Image.new("RGB", (400, 400), (120, 40, 160)).save(media_dir / "01.png")

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(config, name, value)
        self._temporary.cleanup()

    def test_enqueue_returns_immediately(self):
        """Lo importante: la petición no espera a que termine el trabajo."""
        started = time.time()
        job = self.service.enqueue_process(
            self.tweet_id, {"resolution": "native", "backend": "cpu"}
        )
        elapsed = time.time() - started
        self.assertLess(elapsed, 2.0, "encolar debe ser inmediato")
        self.assertEqual(job["kind"], "procesar")
        self.assertIn(job["state"], (QUEUED, RUNNING))

        finished = self.service.jobs.wait(job["id"], timeout=120)
        self.assertEqual(finished.state, DONE)
        self.assertTrue(Path(finished.result["card"]["output_path"]).is_file())

    def test_the_publication_shows_as_processing_right_away(self):
        self.service.enqueue_process(self.tweet_id, {"resolution": "native", "backend": "cpu"})
        self.assertEqual(self.service.get_tweet(self.tweet_id)["status"], "procesando")

    def test_a_failed_job_does_not_leave_the_publication_stuck(self):
        """Si algo revienta, no puede quedarse en «procesando» para siempre."""
        original = self.service.process_tweet
        self.addCleanup(setattr, self.service, "process_tweet", original)

        def explode(*args, **kwargs):
            raise RuntimeError("fallo simulado")

        self.service.process_tweet = explode
        job = self.service.enqueue_process(self.tweet_id)
        finished = self.service.jobs.wait(job["id"], timeout=30)

        self.assertEqual(finished.state, FAILED)
        self.assertIn("fallo simulado", finished.detail)
        tweet = self.service.get_tweet(self.tweet_id)
        self.assertEqual(tweet["status"], "fallido")
        self.assertIn("fallo simulado", tweet["status_detail"])

    def test_enqueueing_a_publication_without_media_is_refused(self):
        self.service.store.upsert_tweets(
            [{"tweet_id": "999", "source_handle": "c", "text": "solo texto"}]
        )
        with self.assertRaises(DashboardError):
            self.service.enqueue_process("999")

    def test_several_jobs_are_queued_and_all_complete(self):
        ids = [self.tweet_id]
        for extra in ("222", "333"):
            self.service.store.upsert_tweets(
                [
                    {
                        "tweet_id": extra,
                        "source_handle": "Cuenta",
                        "text": f"noticia {extra}",
                        "url": f"https://x.com/Cuenta/status/{extra}",
                        "media": ["https://pbs.twimg.com/media/AAA111.jpg"],
                    }
                ]
            )
            media_dir = self.service.media_directory(extra)
            media_dir.mkdir(parents=True, exist_ok=True)
            from PIL import Image

            Image.new("RGB", (400, 400), (10, 90, 40)).save(media_dir / "01.png")
            ids.append(extra)

        jobs = [
            self.service.enqueue_process(tid, {"resolution": "native", "backend": "cpu"})
            for tid in ids
        ]
        for job in jobs:
            finished = self.service.jobs.wait(job["id"], timeout=180)
            self.assertEqual(finished.state, DONE, finished.detail)

        for tid in ids:
            self.assertEqual(self.service.get_tweet(tid)["status"], "tarjeta_lista")
            self.assertIsNotNone(self.service.store.latest_card(tid))

    def test_the_queue_appears_in_the_state(self):
        state = self.service.state()
        self.assertIn("jobs", state)
        self.assertIn("workers", state["jobs"])


if __name__ == "__main__":
    unittest.main()
