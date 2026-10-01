"""Tests de las URL de medios, del enriquecimiento selectivo y del formato.

Cubren tres cosas medidas o encontradas en este equipo:

* mostrar miniaturas reducidas en lugar del archivo original (14 KB contra
  99 KB por imagen),
* no volver a preguntar a los mirrors por datos que la fuente ya trae, y
* que el formato elegido (vertical por defecto) mande sobre lo que sugiera la
  IA, porque antes cada tarjeta salía con la proporción que le parecía.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import config, urls  # noqa: E402
from dashboard.service import DashboardService  # noqa: E402
from dashboard.store import Store  # noqa: E402


class ThumbnailTests(unittest.TestCase):
    BASE = "https://pbs.twimg.com/media/HTh1ytnXQAASGGE.jpg"

    def test_replaces_the_requested_size(self):
        result = urls.thumbnail_url(self.BASE)
        self.assertIn("name=360x360", result)
        self.assertNotIn("name=orig", result)

    def test_keeps_the_path_and_host(self):
        result = urls.thumbnail_url(self.BASE)
        self.assertTrue(result.startswith("https://pbs.twimg.com/media/HTh1ytnXQAASGGE.jpg"))

    def test_overrides_an_existing_size_instead_of_duplicating_it(self):
        result = urls.thumbnail_url(f"{self.BASE}?name=orig")
        self.assertEqual(result.count("name="), 1)
        self.assertIn("name=360x360", result)

    def test_adds_format_when_the_path_has_no_extension(self):
        """Regresión: sin `format`, el CDN responde 404 si la ruta no lleva extensión."""
        result = urls.thumbnail_url("https://pbs.twimg.com/media/HTh1ytnXQAASGGE")
        self.assertIn("format=jpg", result)
        self.assertIn("name=360x360", result)

    def test_other_domains_are_left_alone(self):
        for raw in (
            "https://example.com/foto.jpg",
            "https://i.imgur.com/abc.png?name=orig",
            "",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(urls.thumbnail_url(raw), raw)

    def test_custom_size(self):
        self.assertIn("name=120x120", urls.thumbnail_url(self.BASE, size=120))

    def test_full_size_helper(self):
        result = urls.full_size_url(f"{self.BASE}?name=small")
        self.assertEqual(result.count("name="), 1)
        self.assertIn("name=orig", result)

    def test_is_twitter_media(self):
        self.assertTrue(urls.is_twitter_media(self.BASE))
        self.assertTrue(urls.is_twitter_media("https://video.twimg.com/x.mp4"))
        self.assertFalse(urls.is_twitter_media("https://pbs.twimg.com.evil.com/x.jpg"))
        self.assertFalse(urls.is_twitter_media("https://example.com/x.jpg"))

    def test_thumbnails_maps_a_list(self):
        result = urls.thumbnails([self.BASE, "https://example.com/a.jpg"])
        self.assertEqual(len(result), 2)
        self.assertIn("name=360x360", result[0])
        self.assertEqual(result[1], "https://example.com/a.jpg")

    def test_the_thumbnail_is_much_smaller_than_the_default(self):
        """La razón de ser: se pide un cuadrado pequeño, no el archivo entero."""
        thumb = urls.thumbnail_url(self.BASE)
        self.assertIn("360x360", thumb)
        self.assertNotEqual(thumb, self.BASE)


class SelectiveEnrichmentTests(unittest.TestCase):
    """No se pregunta a los mirrors por lo que la fuente ya entregó.

    Medido: cada consulta de enriquecido cuesta ~0,34 s y los mirrors acaban
    respondiendo 429. Nitter ya entrega fecha y medios en su RSS.
    """

    def setUp(self):
        import tempfile

        from dashboard import config
        from dashboard.service import DashboardService
        from dashboard.store import Store

        self._temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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
        self.enriched: list[str] = []

        def fake_enrich(tweets):
            self.enriched.extend(tweet["tweet_id"] for tweet in tweets)

        self.service._enrich = fake_enrich  # type: ignore[method-assign]

    def tearDown(self):
        from dashboard import config

        for name, value in self._originals.items():
            setattr(config, name, value)
        self._temporary.cleanup()

    def _tweet(self, tweet_id, posted_at, media):
        return {
            "tweet_id": tweet_id,
            "source_handle": "cuenta",
            "text": f"noticia {tweet_id}",
            "posted_at": posted_at,
            "media": media,
        }

    def test_only_tweets_missing_date_or_media_are_enriched(self):
        tweets = [
            self._tweet("completo", "2026-10-01T07:00:00+00:00", ["https://x/a.jpg"]),
            self._tweet("sin-fecha", None, ["https://x/b.jpg"]),
            self._tweet("sin-media", "2026-10-01T07:00:00+00:00", []),
            self._tweet("sin-nada", None, []),
        ]
        self.service._record_success("cuenta", tweets, "nitter")

        self.assertEqual(sorted(self.enriched), ["sin-fecha", "sin-media", "sin-nada"])
        # El que ya venía completo no se consulta.
        self.assertNotIn("completo", self.enriched)

    def test_the_limit_is_applied_after_filtering_not_before(self):
        """Regresión: el tope se aplicaba antes de filtrar y perdía publicaciones.

        Con 25 completas delante, las que necesitaban enriquecido caían más
        allá del puesto 25 y se quedaban sin fecha para siempre.
        """
        from dashboard.service import ENRICH_LIMIT

        tweets = [
            self._tweet(f"ok{i}", "2026-10-01T07:00:00+00:00", ["https://x/a.jpg"])
            for i in range(ENRICH_LIMIT)
        ]
        tweets += [
            self._tweet(f"falta{i}", None, ["https://x/b.jpg"]) for i in range(5)
        ]
        self.service._record_success("cuenta", tweets, "nitter")

        self.assertEqual(
            sorted(self.enriched),
            [f"falta{i}" for i in range(5)],
            "las que faltan de fecha deben intentarse aunque vengan al final",
        )

    def test_the_per_account_limit_still_applies(self):
        from dashboard.service import ENRICH_LIMIT

        tweets = [self._tweet(f"falta{i}", None, ["https://x/b.jpg"]) for i in range(ENRICH_LIMIT + 10)]
        self.service._record_success("cuenta", tweets, "nitter")
        self.assertEqual(len(self.enriched), ENRICH_LIMIT)

    def test_a_global_budget_caps_the_whole_poll(self):
        """Sin presupuesto global, 24 cuentas por 25 darían 600 peticiones."""
        budget = {"remaining": 3, "skipped": 0}
        for cuenta in range(4):
            tweets = [
                self._tweet(f"{cuenta}-{i}", None, ["https://x/b.jpg"]) for i in range(5)
            ]
            self.service._record_success("c", tweets, "nitter", None, budget)

        self.assertEqual(len(self.enriched), 3, "el presupuesto debe cortar en seco")
        self.assertEqual(budget["remaining"], 0)
        # Y queda constancia de cuántas se aplazaron, en vez de perderse calladas.
        self.assertEqual(budget["skipped"], 17)

    def test_without_a_budget_nothing_is_capped_globally(self):
        for cuenta in range(3):
            tweets = [self._tweet(f"{cuenta}-{i}", None, []) for i in range(4)]
            self.service._record_success("c", tweets, "nitter")
        self.assertEqual(len(self.enriched), 12)

    def test_a_fully_provided_batch_costs_no_extra_requests(self):
        tweets = [
            self._tweet(f"t{i}", "2026-10-01T07:00:00+00:00", ["https://x/a.jpg"])
            for i in range(20)
        ]
        self.service._record_success("cuenta", tweets, "nitter")
        self.assertEqual(self.enriched, [], "20 publicaciones no deberían costar 20 peticiones")
        # Y aun así entran todas.
        self.assertEqual(len(self.service.list_tweets(limit=50)), 20)

    def test_tweets_are_still_stored_with_their_media(self):
        original = "https://pbs.twimg.com/media/AAA111.jpg"
        tweets = [self._tweet("uno", "2026-10-01T07:00:00+00:00", [original])]
        self.service._record_success("cuenta", tweets, "nitter")
        stored = self.service.get_tweet("uno")
        # El original se conserva intacto: es el que se usa al componer.
        self.assertEqual(stored["media"], [original])
        # Y se añade la miniatura reducida para mostrarla.
        self.assertIn("name=360x360", stored["thumbs"][0])

    def test_a_non_twitter_url_is_shown_as_is(self):
        tweets = [self._tweet("dos", "2026-10-01T07:00:00+00:00", ["https://example.com/a.jpg"])]
        self.service._record_success("cuenta", tweets, "nitter")
        stored = self.service.get_tweet("dos")
        self.assertEqual(stored["thumbs"], ["https://example.com/a.jpg"])


class ChosenFormatTests(unittest.TestCase):
    """El formato elegido debe mandar sobre la sugerencia de la IA.

    Fallo real: el formato lo imponía la IA para cada publicación, así que unas
    tarjetas salían verticales, otras cuadradas y otras horizontales. El usuario
    pidió verticales y recibió una mezcla.
    """

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.work = Path(self._temporary.name)
        self._originals = {
            n: getattr(config, n) for n in ("VAR_DIR", "DB_PATH", "MEDIA_DIR", "CARDS_DIR")
        }
        config.VAR_DIR = self.work
        config.DB_PATH = self.work / "dashboard.sqlite3"
        config.MEDIA_DIR = self.work / "media"
        config.CARDS_DIR = self.work / "cards"
        config.ensure_directories()
        self._env = {}
        self.service = DashboardService(store=Store(config.DB_PATH))
        self.service.store.upsert_tweets(
            [
                {
                    "tweet_id": "1",
                    "source_handle": "Cuenta",
                    "text": "NOVEDAD",
                    "media": ["https://pbs.twimg.com/media/A.jpg"],
                }
            ]
        )
        # Análisis guardado que propone cuadrado, como haría la IA.
        self.service.store.update_tweet(
            "1",
            analysis_json={
                "provider": "manual",
                "top": "UN TITULAR",
                "bottom": "UN CONTEXTO",
                "suggested_format": "1:1",
            },
        )

    def tearDown(self):
        self.service.shutdown()
        for name, value in self._originals.items():
            setattr(config, name, value)
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._temporary.cleanup()

    def _with_default_format(self, value):
        key = "DASHBOARD_DEFAULT_FORMAT"
        self._env.setdefault(key, os.environ.get(key))
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def test_vertical_is_the_default(self):
        self._with_default_format(None)
        params = self.service.default_params("1")
        self.assertEqual(params["format"], "9:16")
        self.assertTrue(params["format_is_forced"])

    def test_the_chosen_format_beats_the_ai_suggestion(self):
        self._with_default_format("9:16")
        params = self.service.default_params("1")
        self.assertEqual(params["format"], "9:16")
        # La sugerencia se conserva para poder avisar, pero no se usa.
        self.assertEqual(params["suggested_format"], "1:1")
        self.assertTrue(params["format_is_forced"])

    def test_another_chosen_format_is_also_respected(self):
        self._with_default_format("16:9")
        self.assertEqual(self.service.default_params("1")["format"], "16:9")

    def test_auto_does_let_the_ai_decide(self):
        self._with_default_format("auto")
        params = self.service.default_params("1")
        self.assertEqual(params["format"], "1:1")
        self.assertFalse(params["format_is_forced"])

    def test_the_preset_for_vertical_is_the_vertical_one(self):
        # `vertical` no es una clave del mapa y cae al preset por defecto, que
        # es precisamente el vertical; se comprueba que siguen coincidiendo.
        self.assertEqual(
            config.preset_for_format("9:16"), config.preset_for_format("vertical")
        )
        self.assertNotEqual(
            config.preset_for_format("9:16"), config.preset_for_format("1:1")
        )
        self.assertNotEqual(
            config.preset_for_format("9:16"), config.preset_for_format("16:9")
        )

    def test_every_offered_format_has_a_real_preset(self):
        """El selector no debe ofrecer formatos que no existen.

        Ofrecía 4:5, que no tiene preset propio: al elegirlo se componía en
        9:16 sin avisar, así que la interfaz prometía algo que no cumplía.
        """
        from dashboard.pipeline.cards import ALLOWED_FORMATS

        for output_format in ALLOWED_FORMATS:
            if output_format == "auto":
                continue
            with self.subTest(formato=output_format):
                self.assertIn(
                    output_format,
                    config.PRESETS,
                    f"el formato {output_format} se ofrece pero no tiene preset",
                )
                self.assertTrue(config.preset_for_format(output_format).is_file())



if __name__ == "__main__":
    unittest.main()
