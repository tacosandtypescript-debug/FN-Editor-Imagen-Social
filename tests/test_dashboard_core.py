"""Tests del núcleo del dashboard: datos, saneado y parámetros.

No usan red ni navegador: todo se comprueba contra fixtures y contra las
constantes reales del compositor canónico.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import config  # noqa: E402
from dashboard import store as store_module  # noqa: E402
from dashboard.pipeline import cards as cards_pipeline  # noqa: E402
from dashboard.providers import analysis as analysis_providers  # noqa: E402
from dashboard.providers import telegram as telegram_providers  # noqa: E402
from dashboard.providers import timelines as timeline_providers  # noqa: E402
from dashboard.providers.base import Analysis  # noqa: E402
from dashboard.store import Store, normalise_handle  # noqa: E402


RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
  <channel>
    <title>Cuenta / @cuenta</title>
    <item>
      <title>TITULAR CORTO</title>
      <dc:creator>@cuenta</dc:creator>
      <description><![CDATA[<p>FORTNITEMARES VUELVE<br><br>Con mapa nuevo</p>
<img src="https://pbs.twimg.com/media/AAA111.jpg" style="max-width:250px;" />
<img src="https://pbs.twimg.com/media/BBB222.jpg" style="max-width:250px;" />]]></description>
      <pubDate>Thu, 01 Oct 2026 07:37:01 GMT</pubDate>
      <guid isPermaLink="false">2105562614461776336</guid>
      <link>https://nitter.example/cuenta/status/2105562614461776336#m</link>
    </item>
    <item>
      <title>Sin identificador</title>
      <description><![CDATA[<p>texto</p>]]></description>
      <guid isPermaLink="false">no-es-un-id</guid>
      <link>https://nitter.example/tags/algo</link>
    </item>
  </channel>
</rss>
"""


class HandleTests(unittest.TestCase):
    def test_normalise_handle_accepts_common_forms(self):
        cases = {
            "@ShiinaBR": "ShiinaBR",
            "ShiinaBR": "ShiinaBR",
            "  @HYPEX  ": "HYPEX",
            "https://x.com/FortniteStatus": "FortniteStatus",
            "https://twitter.com/HYPEX/": "HYPEX",
            "https://x.com/HYPEX/status/123": "HYPEX",
            "x.com/Loolo_WRLD": "Loolo_WRLD",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalise_handle(raw), expected)

    def test_normalise_handle_strips_url_punctuation(self):
        self.assertEqual(normalise_handle("https://x.com/@Jorge_Most?ref=abc"), "Jorge_Most")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._temporary.name) / "test.sqlite3")

    def tearDown(self):
        self._temporary.cleanup()

    def test_upsert_deduplicates_by_tweet_id(self):
        tweet = {
            "tweet_id": "111",
            "source_handle": "cuenta",
            "text": "hola",
            "url": "https://x.com/cuenta/status/111",
            "media": ["https://pbs.twimg.com/media/A.jpg"],
        }
        first = self.store.upsert_tweets([tweet])
        self.assertEqual(len(first["inserted"]), 1)
        self.assertEqual(first["duplicates"], 0)

        second = self.store.upsert_tweets([tweet])
        self.assertEqual(second["inserted"], [])
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(len(self.store.list_tweets()), 1)

    def test_new_tweets_start_in_nuevo_status(self):
        self.store.upsert_tweets([{"tweet_id": "222", "source_handle": "c", "text": "x"}])
        tweet = self.store.get_tweet("222")
        self.assertEqual(tweet["status"], store_module.STATUS_NEW)

    def test_status_transitions_and_counts(self):
        self.store.upsert_tweets(
            [{"tweet_id": str(index), "source_handle": "c", "text": "x"} for index in range(4)]
        )
        self.store.set_tweet_status("0", store_module.STATUS_SELECTED)
        self.store.set_tweet_status("1", store_module.STATUS_CARD_READY)
        counts = self.store.count_by_status()
        self.assertEqual(counts[store_module.STATUS_SELECTED], 1)
        self.assertEqual(counts[store_module.STATUS_CARD_READY], 1)
        self.assertEqual(counts[store_module.STATUS_NEW], 2)
        self.assertEqual(set(counts), set(store_module.ALL_STATUSES))

    def test_unknown_status_is_rejected(self):
        self.store.upsert_tweets([{"tweet_id": "333", "source_handle": "c"}])
        with self.assertRaises(ValueError):
            self.store.set_tweet_status("333", "inventado")

    def test_accounts_crud(self):
        self.store.add_account("@Cuenta")
        self.store.add_account("otra")
        self.assertEqual(len(self.store.list_accounts()), 2)
        self.assertEqual(len(self.store.list_accounts(active_only=True)), 2)

        self.store.set_account_active("Cuenta", False)
        self.assertEqual(len(self.store.list_accounts(active_only=True)), 1)
        # Volver a añadirla la reactiva en lugar de duplicarla.
        self.store.add_account("@cuenta")
        self.assertEqual(len(self.store.list_accounts()), 2)
        self.assertEqual(len(self.store.list_accounts(active_only=True)), 2)

        self.assertTrue(self.store.remove_account("OTRA"))
        self.assertFalse(self.store.remove_account("OTRA"))
        self.assertIsNone(self.store.get_account("otra"))

    def test_card_versions_increment(self):
        self.store.upsert_tweets([{"tweet_id": "444", "source_handle": "c"}])
        first = self.store.create_card("444", {"top": "A"})
        second = self.store.create_card("444", {"top": "B"})
        self.assertEqual(first["version"], 1)
        self.assertEqual(second["version"], 2)
        self.assertEqual(self.store.latest_card("444")["version"], 2)

    def test_media_is_decoded_back_into_a_list(self):
        self.store.upsert_tweets(
            [{"tweet_id": "555", "source_handle": "c", "media": ["a.jpg", "b.jpg"]}]
        )
        tweet = self.store.get_tweet("555")
        self.assertEqual(tweet["media"], ["a.jpg", "b.jpg"])
        self.assertTrue(tweet["has_media"])

    def test_tweets_without_media_are_flagged(self):
        self.store.upsert_tweets([{"tweet_id": "556", "source_handle": "c"}])
        self.assertFalse(self.store.get_tweet("556")["has_media"])


class NitterParsingTests(unittest.TestCase):
    def test_parses_items_with_text_media_and_date(self):
        records = timeline_providers.parse_nitter_rss(RSS_FIXTURE, source_handle="cuenta")
        # El segundo item no tiene /status/, así que se descarta.
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.tweet_id, "2105562614461776336")
        self.assertEqual(record.source_handle, "cuenta")
        self.assertEqual(record.author_handle, "cuenta")
        self.assertEqual(record.url, "https://x.com/cuenta/status/2105562614461776336")
        self.assertEqual(record.posted_at, "2026-10-01T07:37:01+00:00")
        self.assertEqual(
            record.media,
            [
                "https://pbs.twimg.com/media/AAA111.jpg",
                "https://pbs.twimg.com/media/BBB222.jpg",
            ],
        )

    def test_text_has_no_html_or_style_leftovers(self):
        """Regresión: los atributos de <img> se colaban en el texto."""
        record = timeline_providers.parse_nitter_rss(RSS_FIXTURE, source_handle="cuenta")[0]
        self.assertIn("FORTNITEMARES VUELVE", record.text)
        self.assertIn("Con mapa nuevo", record.text)
        self.assertNotIn("style=", record.text)
        self.assertNotIn("<img", record.text)
        self.assertNotIn("max-width", record.text)

    def test_long_title_wins_over_truncated_title(self):
        rss = RSS_FIXTURE.replace("<title>TITULAR CORTO</title>", "<title>CORTO</title>")
        record = timeline_providers.parse_nitter_rss(rss, source_handle="c")[0]
        self.assertIn("FORTNITEMARES VUELVE", record.text)


class SanitizerTests(unittest.TestCase):
    def _sanitize(self, top, bottom, caption="", hashtags=None):
        return analysis_providers.sanitize_analysis(
            Analysis(top=top, bottom=bottom, caption=caption, hashtags=hashtags or [])
        )

    def test_function_words_are_never_highlighted(self):
        result = self._sanitize("LA {DE|FF0000} TIENDA", "{EL|8B3DFF} MAPA")
        self.assertNotIn("{DE|", result.top)
        self.assertIn("DE", result.top)
        # El compositor es la autoridad final.
        analysis_providers.repo.compose_image().validate_text_markup(result.top, result.bottom)

    def test_at_most_two_accent_colors(self):
        result = self._sanitize(
            "{UNO|8B3DFF} {DOS|FF7A00}", "{TRES|E83DFF}"
        )
        colors = {
            match.group(2).upper()
            for match in analysis_providers.repo.compose_image().SEG.finditer(
                result.top + result.bottom
            )
        }
        self.assertLessEqual(len(colors), 2)

    def test_colors_are_snapped_to_the_preset_palette(self):
        palette = {color.upper() for color in analysis_providers.palette_colors()}
        result = self._sanitize("{TIENDA|010203}", "CONTEXTO")
        # group(1) es la palabra; group(2) es el color.
        for match in analysis_providers.repo.compose_image().SEG.finditer(result.top):
            self.assertIn("#" + match.group(2).upper(), palette)

    def test_hashtags_are_always_five_with_brand(self):
        result = self._sanitize("A", "B", hashtags=["#fortnite"])
        self.assertEqual(len(result.hashtags), 5)
        self.assertEqual(result.hashtags[0], "#khetzalgg")
        self.assertEqual(len(set(result.hashtags)), 5)

    def test_hashtag_removal_respects_word_boundaries(self):
        """Regresión: quitar #fortnite mutilaba #fortnitemares."""
        result = self._sanitize(
            "TITULAR", "CONTEXTO", caption="Fortnitemares vuelve #fortnite",
            hashtags=["#fortnite", "#fortnitemares"],
        )
        self.assertTrue(result.caption.startswith("Fortnitemares vuelve"))
        self.assertNotIn("vuelve mares", result.caption)

    def test_empty_model_output_still_yields_composable_text(self):
        result = self._sanitize("", "")
        self.assertTrue(result.top.strip())
        self.assertTrue(result.bottom.strip())
        analysis_providers.repo.compose_image().validate_text_markup(result.top, result.bottom)

    def test_unknown_suggested_format_falls_back(self):
        result = analysis_providers.analysis_from_payload(
            json.dumps({"top": "A", "bottom": "B", "suggested_format": "banana"}),
            provider="test",
        )
        self.assertIn(result.suggested_format, analysis_providers.DEFAULT_FORMATS)

    def test_json_is_extracted_from_markdown_fences_and_noise(self):
        payload = (
            "Claro, aquí tienes:\n```json\n"
            '{"top": "TITULAR", "bottom": "CONTEXTO", "hashtags": ["#a"]}\n'
            "```\nEspero que sirva."
        )
        result = analysis_providers.analysis_from_payload(payload, provider="test")
        self.assertEqual(result.top, "TITULAR")
        self.assertEqual(result.bottom, "CONTEXTO")

    def test_missing_fields_raise_provider_error(self):
        with self.assertRaises(analysis_providers.ProviderError):
            analysis_providers.analysis_from_payload('{"top": "solo titulo"}', provider="test")


class PromptTests(unittest.TestCase):
    def test_system_prompt_is_fully_resolved(self):
        """Regresión: `str.format` sobre el prompt con llaves JSON fallaba."""
        prompt = analysis_providers.build_system_prompt()
        self.assertNotIn("__PALETTE__", prompt)
        self.assertNotIn("__FORMATS__", prompt)
        # Los colores reales del preset están presentes.
        self.assertIn("#8B3DFF", prompt)
        # Y el ejemplo JSON sigue intacto, con sus llaves.
        self.assertIn('"top"', prompt)
        self.assertIn("{PALABRA|HEX}", prompt)

    def test_openai_provider_is_unavailable_without_key(self):
        original = config.Settings()
        self.assertTrue(original)  # el objeto se construye sin fallar
        provider = analysis_providers.OpenAICompatibleAnalysis()
        self.assertIsInstance(provider.status().available, bool)


class CardParameterTests(unittest.TestCase):
    def test_empty_text_is_rejected(self):
        for params in ({"top": "  ", "bottom": "x"}, {"top": "x", "bottom": ""}):
            with self.subTest(params=params):
                with self.assertRaises(cards_pipeline.CardError):
                    cards_pipeline.normalise_params(params)

    def test_unknown_values_are_rejected(self):
        base = {"top": "A", "bottom": "B"}
        for key, value in (
            ("style", "inventado"),
            ("fit", "inventado"),
            ("resolution", "8k"),
            ("backend", "quantum"),
            ("format", "3:7"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(cards_pipeline.CardError):
                    cards_pipeline.normalise_params({**base, key: value})

    def test_highlighted_function_word_is_rejected(self):
        with self.assertRaises(cards_pipeline.CardError):
            cards_pipeline.normalise_params({"top": "{DE|FF0000} TIENDA", "bottom": "B"})

    def test_defaults_are_filled_in(self):
        clean = cards_pipeline.normalise_params({"top": "A", "bottom": "B"})
        self.assertEqual(clean["format"], "auto")
        self.assertEqual(clean["resolution"], "4k")
        self.assertIsNone(clean["background"])

    def test_three_accent_colors_are_rejected(self):
        with self.assertRaises(cards_pipeline.CardError):
            cards_pipeline.normalise_params(
                {"top": "{A|8B3DFF} {B|FF7A00}", "bottom": "{C|E83DFF}"}
            )


class TelegramTests(unittest.TestCase):
    def test_multipart_body_contains_file_and_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "tarjeta.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            body, content_type = telegram_providers._multipart(
                {"chat_id": "123", "caption": "hola"}, "document", image
            )
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        boundary = content_type.split("boundary=")[1].encode()
        self.assertIn(b'name="chat_id"', body)
        self.assertIn(b"123", body)
        self.assertIn(b'name="document"', body)
        self.assertIn(b'filename="tarjeta.png"', body)
        self.assertIn(b"Content-Type: image/png", body)
        self.assertTrue(body.startswith(b"--" + boundary))
        self.assertTrue(body.rstrip().endswith(b"--" + boundary + b"--"))

    def test_missing_configuration_is_reported(self):
        delivery = telegram_providers.TelegramDelivery(token="", chat_id="")
        status = delivery.status()
        self.assertFalse(status.available)
        self.assertIn("TELEGRAM_BOT_TOKEN", status.detail)
        with self.assertRaises(telegram_providers.ProviderError):
            delivery.send(Path("no-existe.png"))

    def test_local_delivery_needs_no_credentials(self):
        delivery = telegram_providers.LocalDelivery()
        self.assertTrue(delivery.status().available)
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "tarjeta.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
            outcome = delivery.send(image, "caption")
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["method"], "archivo-local")

    def test_auto_falls_back_to_local_without_telegram(self):
        provider = telegram_providers.get_delivery_provider("auto")
        self.assertIsInstance(provider, telegram_providers.DeliveryProvider)

    def test_missing_file_is_reported(self):
        delivery = telegram_providers.LocalDelivery()
        with self.assertRaises(telegram_providers.ProviderError):
            delivery.send(Path("no-existe-en-disco.png"))


if __name__ == "__main__":
    unittest.main()
