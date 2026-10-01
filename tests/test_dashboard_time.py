"""Tests de la hora y el huso horario del dashboard.

Cubren el caso real de esta máquina: el reloj del sistema va seis horas
desviado y además la zona configurada ha cambiado durante la sesión. Por eso
la hora se mide en red y el huso se resuelve con reglas, no con el sistema.
"""

import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import localtime, timefmt  # noqa: E402
from dashboard.clock import Clock, describe_offset  # noqa: E402

UTC = timezone.utc


class ParseMomentTests(unittest.TestCase):
    def test_accepts_the_shapes_the_sources_use(self):
        cases = [
            "2026-10-01T07:37:01+00:00",
            "2026-10-01T07:37:01Z",
            "2026-10-01T07:37:01",
            "Thu, 01 Oct 2026 07:37:01 GMT",
            "Thu, 01 Oct 2026 07:37:01 +0000",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                moment = timefmt.parse_moment(raw)
                self.assertIsNotNone(moment)
                self.assertEqual(
                    moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"), "2026-10-01 07:37:01"
                )

    def test_accepts_epoch_numbers(self):
        moment = timefmt.parse_moment(1790821021)
        self.assertIsNotNone(moment)
        self.assertEqual(moment.tzinfo, UTC)

    def test_rejects_junk(self):
        for raw in (None, "", "   ", "no es una fecha", object()):
            with self.subTest(raw=raw):
                self.assertIsNone(timefmt.parse_moment(raw))


class HumanizeRelativeTests(unittest.TestCase):
    NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)

    def _relative(self, **delta):
        return timefmt.humanize_relative(self.NOW - timedelta(**delta), self.NOW)

    def test_seconds(self):
        self.assertEqual(self._relative(seconds=5), "hace unos segundos")
        self.assertEqual(self._relative(seconds=59), "hace unos segundos")

    def test_minutes(self):
        self.assertEqual(self._relative(minutes=1), "hace 1 min")
        self.assertEqual(self._relative(minutes=42), "hace 42 min")

    def test_hours_include_the_minutes(self):
        """Lo pedido: «hace una hora y tantos minutos», no solo la hora."""
        self.assertEqual(self._relative(hours=1, minutes=15), "hace 1 h 15 min")
        self.assertEqual(self._relative(hours=2, minutes=30), "hace 2 h 30 min")
        self.assertEqual(self._relative(hours=2, minutes=45), "hace 2 h 45 min")

    def test_exact_hour_omits_zero_minutes(self):
        self.assertEqual(self._relative(hours=3), "hace 3 h")

    def test_minutes_do_not_leak_into_hours(self):
        self.assertEqual(self._relative(minutes=119), "hace 1 h 59 min")
        self.assertEqual(self._relative(minutes=60), "hace 1 h")

    def test_days_and_beyond(self):
        self.assertEqual(self._relative(days=1), "hace 1 día")
        self.assertEqual(self._relative(days=3), "hace 3 días")
        self.assertEqual(self._relative(days=40), "hace 1 mes")
        self.assertEqual(self._relative(days=100), "hace 3 meses")
        self.assertEqual(self._relative(days=400), "hace 1 año")

    def test_a_future_timestamp_never_says_in_the_future(self):
        """Con un reloj desviado, una publicación parecía del futuro."""
        future = self.NOW + timedelta(hours=6)
        self.assertEqual(timefmt.humanize_relative(future, self.NOW), "hace unos segundos")

    def test_invalid_input_returns_empty(self):
        self.assertEqual(timefmt.humanize_relative(None, self.NOW), "")
        self.assertEqual(timefmt.humanize_relative("basura", self.NOW), "")


class AbsoluteFormatTests(unittest.TestCase):
    def test_quebec_offset_is_applied(self):
        """Quebec en octubre es UTC−4: 07:37 UTC deben verse como 03:37."""
        text = timefmt.format_absolute("2026-10-01T07:37:01+00:00", offset_seconds=-4 * 3600)
        self.assertEqual(text, "01 oct 2026 · 03:37")

    def test_utc_when_offset_is_zero(self):
        self.assertEqual(
            timefmt.format_absolute("2026-10-01T07:37:01+00:00", offset_seconds=0),
            "01 oct 2026 · 07:37",
        )

    def test_short_date(self):
        self.assertEqual(
            timefmt.format_short_date("2026-10-01T07:37:01+00:00", offset_seconds=-4 * 3600),
            "01/10/2026",
        )

    def test_day_rolls_back_with_negative_offset(self):
        self.assertEqual(
            timefmt.format_absolute("2026-10-01T02:30:00+00:00", offset_seconds=-4 * 3600),
            "30 sep 2026 · 22:30",
        )


class LocalTimeRuleTests(unittest.TestCase):
    """Regla de horario de verano de Eastern Time (la de Quebec)."""

    def test_nth_weekday(self):
        # Segundo domingo de marzo de 2026 y primer domingo de noviembre.
        self.assertEqual(localtime.nth_weekday(2026, 3, 6, 2), date(2026, 3, 8))
        self.assertEqual(localtime.nth_weekday(2026, 11, 6, 1), date(2026, 11, 1))
        self.assertEqual(localtime.nth_weekday(2026, 3, 6, 1), date(2026, 3, 1))

    def test_summer_is_utc_minus_four(self):
        moment = datetime(2026, 10, 1, 7, 37, tzinfo=UTC)
        self.assertEqual(localtime.eastern_offset(moment), timedelta(hours=-4))

    def test_winter_is_utc_minus_five(self):
        moment = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        self.assertEqual(localtime.eastern_offset(moment), timedelta(hours=-5))

    def test_switches_exactly_at_the_dst_boundaries(self):
        # 8 de marzo de 2026, 07:00 UTC: justo antes sigue en horario estándar.
        before = datetime(2026, 3, 8, 6, 59, tzinfo=UTC)
        after = datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
        self.assertEqual(localtime.eastern_offset(before), timedelta(hours=-5))
        self.assertEqual(localtime.eastern_offset(after), timedelta(hours=-4))

        # 1 de noviembre de 2026, 06:00 UTC: vuelve el horario estándar.
        late = datetime(2026, 11, 1, 5, 59, tzinfo=UTC)
        ended = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
        self.assertEqual(localtime.eastern_offset(late), timedelta(hours=-4))
        self.assertEqual(localtime.eastern_offset(ended), timedelta(hours=-5))

    def test_fixed_offset_takes_priority(self):
        offset, source = localtime.resolve_offset(
            datetime(2026, 10, 1, tzinfo=UTC),
            timezone_name="America/Toronto",
            fixed_hours=-3,
        )
        self.assertEqual(offset, timedelta(hours=-3))
        self.assertEqual(source, "configurado")

    def test_eastern_zone_resolves_without_tzdata(self):
        """Sin `tzdata` instalado, la regla incorporada cubre Quebec."""
        offset, source = localtime.resolve_offset(
            datetime(2026, 10, 1, 7, 0, tzinfo=UTC), timezone_name="America/Toronto"
        )
        self.assertEqual(offset, timedelta(hours=-4))
        self.assertIn(source, {"regla Eastern Time", "zona America/Toronto"})

    def test_unknown_zone_falls_back_to_the_system(self):
        offset, source = localtime.resolve_offset(
            datetime(2026, 10, 1, tzinfo=UTC), timezone_name="Marte/Olympus"
        )
        self.assertIsInstance(offset, timedelta)
        self.assertEqual(source, "sistema")

    def test_describe(self):
        self.assertEqual(localtime.describe(timedelta(hours=-4)), "UTC-4")
        self.assertEqual(localtime.describe(timedelta(hours=2)), "UTC+2")
        self.assertEqual(localtime.describe(timedelta(hours=-3, minutes=-30)), "UTC-3:30")
        self.assertEqual(localtime.describe(timedelta(0)), "UTC+0")


class DecorateTweetTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(hosts=(), fixed_offset_hours=-4)
        self.now = datetime(2026, 10, 1, 8, 0, 0, tzinfo=UTC)
        self.clock.set_offset(0.0)
        self.clock.now = lambda: self.now  # type: ignore[method-assign]

    def test_decorates_with_relative_and_absolute(self):
        tweet = timefmt.decorate_tweet(
            {"posted_at": "2026-10-01T07:37:01+00:00", "fetched_at": "2026-10-01T07:40:00+00:00"},
            self.clock,
        )
        self.assertEqual(tweet["posted_relative"], "hace 22 min")
        self.assertEqual(tweet["posted_absolute"], "01 oct 2026 · 03:37")
        self.assertFalse(tweet["date_is_estimated"])

    def test_falls_back_to_fetch_time_and_marks_it_estimated(self):
        tweet = timefmt.decorate_tweet(
            {"posted_at": None, "fetched_at": "2026-10-01T07:37:01+00:00"}, self.clock
        )
        self.assertEqual(tweet["posted_relative"], "hace 22 min")
        self.assertTrue(tweet["date_is_estimated"])

    def test_uses_the_relative_time_from_the_browser_when_there_is_no_date(self):
        tweet = timefmt.decorate_tweet(
            {"posted_at": None, "fetched_at": "2026-10-01T07:37:01+00:00", "relative_time": "9h"},
            self.clock,
        )
        self.assertEqual(tweet["posted_relative"], "hace 9h")

    def test_dst_is_applied_per_publication_not_globally(self):
        """Una publicación de enero debe mostrarse en UTC−5, no en UTC−4."""
        clock = Clock(hosts=(), fixed_offset_hours=None, timezone_name="America/Toronto")
        clock.now = lambda: datetime(2026, 1, 20, 12, 0, tzinfo=UTC)  # type: ignore[method-assign]
        tweet = timefmt.decorate_tweet({"posted_at": "2026-01-15T12:00:00+00:00"}, clock)
        self.assertEqual(tweet["posted_absolute"], "15 ene 2026 · 07:00")
        self.assertEqual(clock.offset_for(timefmt.parse_moment("2026-01-15T12:00:00+00:00")),
                         timedelta(hours=-5))


class _TimeServerHandler(BaseHTTPRequestHandler):
    """Servidor que declara la hora que le digamos, para probar la medición."""

    server_epoch = 0.0

    def log_message(self, *args):  # noqa: D102 - silencio en los tests
        return

    def date_time_string(self, timestamp=None):  # noqa: D102
        return formatdate(self.server_epoch, usegmt=True)

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class ClockMeasurementTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        # El servidor de prueba va seis horas por delante del reloj del sistema.
        _TimeServerHandler.server_epoch = (datetime.now(UTC) + timedelta(hours=6)).timestamp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _TimeServerHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._temporary.cleanup()

    def test_measures_the_offset_against_the_server(self):
        clock = Clock(hosts=(self.url,))
        status = clock.measure()
        self.assertEqual(status["sample_count"], 1)
        self.assertAlmostEqual(clock.offset_seconds, 6 * 3600, delta=5)
        self.assertAlmostEqual(
            clock.now().timestamp(), datetime.now(UTC).timestamp() + 6 * 3600, delta=5
        )

    def test_after_measuring_the_clock_no_longer_depends_on_the_machine(self):
        """El requisito: la hora sale de internet, no del reloj de la PC."""
        clock = Clock(hosts=(self.url,))
        clock.measure()
        self.assertTrue(clock.anchored)
        self.assertTrue(clock.status()["independent_of_system_clock"])

        # Se falsea el respaldo basado en el reloj de pared: si `now()` lo
        # usara, la hora se iría seis horas. Con ancla, no le afecta.
        clock._offset_seconds = 0.0
        self.assertAlmostEqual(
            clock.now().timestamp(), datetime.now(UTC).timestamp() + 6 * 3600, delta=5
        )

    def test_the_anchor_can_be_fixed_and_now_follows_the_monotonic_clock(self):
        clock = Clock(hosts=(self.url,))
        clock.measure()
        target = 1_800_000_000.0
        clock._anchor_server_epoch = target
        clock._anchor_monotonic = time.monotonic()
        self.assertAlmostEqual(clock.now().timestamp(), target, delta=1)

    def test_a_failed_remeasure_keeps_the_previous_anchor(self):
        clock = Clock(hosts=(self.url,))
        clock.measure()
        self.assertTrue(clock.anchored)
        clock.hosts = ("http://127.0.0.1:1/",)
        status = clock.measure()
        self.assertFalse(status["trusted"])
        self.assertEqual(status["sample_count"], 0)
        # El ancla anterior se conserva: la hora sigue siendo la real.
        self.assertTrue(clock.anchored)
        self.assertAlmostEqual(
            clock.now().timestamp(), datetime.now(UTC).timestamp() + 6 * 3600, delta=10
        )

    def test_clock_still_reports_the_right_local_hour_when_the_system_is_wrong(self):
        """El caso real: reloj desviado y zona ajena, y aun así la hora es correcta."""
        clock = Clock(hosts=(self.url,), timezone_name="America/Toronto")
        clock.measure()
        local = clock.to_local(clock.now())
        expected_utc = datetime.now(UTC) + timedelta(seconds=clock.offset_seconds)
        # La hora local debe ser la de Quebec: UTC−4 en octubre.
        self.assertAlmostEqual((local - expected_utc).total_seconds(), -4 * 3600, delta=5)

    def test_fixed_offset_override_wins(self):
        clock = Clock(hosts=(self.url,), fixed_offset_hours=-4)
        clock.measure()
        self.assertEqual(clock.local_offset_seconds, -4 * 3600)
        clock.set_local_offset_override(-6)
        self.assertEqual(clock.local_offset_seconds, -6 * 3600)

    def test_to_local_converts_from_true_utc(self):
        clock = Clock(hosts=(), fixed_offset_hours=-4)
        converted = clock.to_local(datetime(2026, 10, 1, 7, 37, tzinfo=UTC))
        self.assertEqual(converted.strftime("%Y-%m-%d %H:%M"), "2026-10-01 03:37")

    def test_unreachable_hosts_are_reported_without_raising(self):
        clock = Clock(hosts=("http://127.0.0.1:1/",))
        status = clock.measure()
        self.assertFalse(status["trusted"])
        self.assertEqual(status["sample_count"], 0)
        self.assertTrue(status["failures"])
        self.assertIsNotNone(clock.now())

    def test_status_reports_the_zone_and_its_origin(self):
        clock = Clock(hosts=(self.url,), timezone_name="America/Toronto")
        status = clock.measure()
        self.assertTrue(status["trusted"] or status["sample_count"] == 1)
        self.assertEqual(status["local_offset_human"], "UTC-4")
        self.assertEqual(status["timezone_name"], "America/Toronto")

    def test_a_single_skewed_server_does_not_spoil_the_measurement(self):
        """Caso real: la cabecera Date de GitHub iba 6 s por detrás de las demás."""
        base = (datetime.now(UTC) + timedelta(hours=6)).timestamp()
        # Tres servidores correctos y uno seis segundos atrasado.
        readings = {
            "a": base,
            "b": base + 0.2,
            "c": base + 0.4,
            "d": base - 6.0,
        }
        clock = Clock(hosts=("a", "b", "c", "d"))
        clock._probe = lambda host: readings[host]  # type: ignore[method-assign]
        status = clock.measure()

        self.assertTrue(status["trusted"], status)
        self.assertEqual(status["agreeing_count"], 3)
        self.assertEqual(status["disagreeing_hosts"], ["d"])
        # La mediana se queda con los que coinciden, no con el desviado.
        self.assertAlmostEqual(clock.offset_seconds, 6 * 3600, delta=1)

    def test_restored_offset_is_marked_as_not_locally_verified(self):
        clock = Clock(hosts=())
        clock.set_offset(21600.0, source="guardado", trusted=False)
        self.assertEqual(clock.offset_seconds, 21600.0)
        self.assertFalse(clock.trusted)
        self.assertFalse(clock.is_stale())
        # Un desfase guardado no es un ancla: sirve solo hasta poder medir.
        self.assertFalse(clock.anchored)
        self.assertAlmostEqual(
            clock.now().timestamp(), datetime.now(UTC).timestamp() + 21600, delta=5
        )


class DescriptionTests(unittest.TestCase):
    def test_describe_offset(self):
        self.assertEqual(describe_offset(0), "reloj correcto")
        # Positivo = el servidor de referencia va por delante = reloj atrasado.
        self.assertIn("atrasado", describe_offset(6 * 3600))
        self.assertIn("6 h", describe_offset(6 * 3600))
        self.assertIn("adelantado", describe_offset(-3600))
        self.assertIn("30 min", describe_offset(-1800))

    def test_offset_label_matches_the_real_measured_case(self):
        """En esta máquina el reloj va seis horas por detrás de la hora real."""
        self.assertEqual(describe_offset(21608.8), "reloj del sistema atrasado 6 h")


if __name__ == "__main__":
    unittest.main()
