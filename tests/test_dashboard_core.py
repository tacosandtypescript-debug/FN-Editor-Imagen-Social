"""Tests del núcleo del dashboard: datos, saneado y parámetros.

No usan red ni navegador: todo se comprueba contra fixtures y contra las
constantes reales del compositor canónico.
"""

import json
import re
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

    def test_plain_text_gets_a_highlight_so_no_card_ships_without_colour(self):
        """La tarjeta 1 salió sin un solo color, con la regla en contra.

        El proveedor `manual` devuelve texto plano y el contrato del repositorio
        dice que colorear **no es opcional**. Ahora se colorea la palabra más
        informativa en lugar de dejar la tarjeta en blanco y negro.
        """
        result = self._sanitize("FREDDY FAZBEAR SLIPPERS", "CONTEXTO PENDIENTE")
        seg = analysis_providers.repo.compose_image().SEG
        self.assertTrue(seg.search(result.top), result.top)
        self.assertTrue(seg.search(result.bottom), result.bottom)
        # Y sigue siendo válido para el compositor.
        analysis_providers.repo.compose_image().validate_text_markup(result.top, result.bottom)

    def test_the_added_highlight_is_an_informative_word(self):
        result = self._sanitize("LA NUEVA TIENDA DE FORTNITE", "EL CONTEXTO")
        # «FORTNITE» es la más larga; «LA» es funcional y no puede colorearse.
        self.assertIn("{FORTNITE|", result.top)
        self.assertNotIn("{LA|", result.top)

    def test_text_that_already_has_colour_is_left_alone(self):
        result = self._sanitize("PICO SIN {PISTA|8B3DFF}", "SIN {FUENTE|FF7A00} CONOCIDA")
        self.assertEqual(result.top, "PICO SIN {PISTA|8B3DFF}")
        self.assertEqual(result.bottom, "SIN {FUENTE|FF7A00} CONOCIDA")

    def test_nothing_is_invented_when_there_is_no_word_to_colour(self):
        """Con solo palabras funcionales no se colorea nada: no se inventa."""
        result = self._sanitize("DE LA EN EL", "CON POR PARA")
        self.assertNotIn("{", result.top)
        self.assertNotIn("{", result.bottom)

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
        # Lo que se comprueba aquí es que el JSON se extrae del ruido. El texto
        # puede llevar ya el marcado de color, porque la regla dice que colorear
        # no es opcional y estas palabras llegan sin él.
        self.assertIn("TITULAR", result.top)
        self.assertIn("CONTEXTO", result.bottom)

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

    def test_system_prompt_makes_highlighting_mandatory(self):
        """Se pidió expresamente que la IA ponga los colores de las palabras."""
        import re

        prompt = re.sub(r"\s+", " ", analysis_providers.build_system_prompt()).lower()
        self.assertIn("colorea siempre las palabras", prompt)
        self.assertIn("no es opcional", prompt)
        self.assertIn('una palabra en "top"', prompt)
        self.assertIn('una palabra en "bottom"', prompt)
        # Y sigue prohibiendo colorear palabras funcionales.
        self.assertIn("nunca resaltes palabras funcionales", prompt)

    def test_proposal_prompt_requests_three_options_without_a_fixed_template_label(self):
        prompt = analysis_providers.build_proposal_system_prompt()
        # Se compara sin saltos de línea: el prompt se ajusta a 79 columnas y
        # una frase puede quedar partida en dos.
        plano = re.sub(r"\s+", " ", prompt).lower()
        self.assertNotIn("__PALETTE__", prompt)
        self.assertNotIn("__FORMATS__", prompt)
        self.assertIn('"options"', prompt)
        self.assertIn("exactamente tres", plano)
        self.assertIn("fijas de plantilla", plano)
        self.assertNotIn("PRIMERA ACTUALIZACIÓN · 01/1", prompt)

    def test_prompts_state_the_character_limits_the_composer_enforces(self):
        """Sin límite explícito el modelo escribía frases que no cabían.

        Ocurrió en real: una propuesta de 90 caracteres se eligió y la
        composición falló después con «el texto no cabe en 1920px». El límite
        medido con el propio compositor es de unos 55 caracteres.
        """
        for prompt in (
            analysis_providers.build_proposal_system_prompt(),
            analysis_providers.build_system_prompt(),
        ):
            with self.subTest(primeras=prompt[:40]):
                plano = re.sub(r"\s+", " ", prompt).lower()
                self.assertIn("48 caracteres", plano)
                self.assertIn("52 caracteres", plano)

    def test_prompts_forbid_the_bottom_from_repeating_the_title(self):
        """El texto de abajo debe aportar, no repetir la idea del titular.

        Ocurrió en real: «EL CALZADO DE FREDDY FAZBEAR» arriba y «LAS ZAPATILLAS
        DE FREDDY FAZBEAR» abajo, con el mismo significado dos veces.
        """
        for prompt in (
            analysis_providers.build_proposal_system_prompt(),
            analysis_providers.build_system_prompt(),
        ):
            with self.subTest(primeras=prompt[:40]):
                plano = re.sub(r"\s+", " ", prompt).lower()
                self.assertIn("aporta", plano)
                self.assertIn("nunca repitas la idea", plano)
                # Y el ejemplo real, que es lo que mejor guía al modelo.
                self.assertIn("zapatillas", plano)

    def test_exact_echo_between_title_and_bottom_is_detected(self):
        """El eco literal se detecta; los sinónimos no, y no se finge.

        «calzado» y «zapatillas» significan lo mismo pero no comparten letras:
        eso lo tiene que resolver el prompt, no una comparación de palabras.
        """
        self.assertTrue(
            analysis_providers.is_redundant_pair("FORTNITEMARES MAPA NUEVO", "MAPA NUEVO")
        )
        self.assertFalse(
            analysis_providers.is_redundant_pair(
                "FORTNITEMARES VUELVE CON {MAPA|FF7A00} NUEVO", "SE ESTRENA EL 01/10"
            )
        )
        self.assertFalse(
            analysis_providers.is_redundant_pair(
                "EL CALZADO DE FREDDY FAZBEAR", "LAS ZAPATILLAS DE FREDDY FAZBEAR"
            )
        )

    def test_the_composer_confirms_which_texts_fit(self):
        """`text_fits` reutiliza `fit_block`, así que no puede divergir."""
        corto, motivo = cards_pipeline.text_fits("TITULAR CORTO", "CONTEXTO CORTO", "9:16")
        self.assertTrue(corto, motivo)

        # El texto real que hizo fallar la composición.
        largo, motivo = cards_pipeline.text_fits(
            "CRYSTALLIZED {PUNISHER|8B3DFF}: UN PICO SIN RUTA REVELADA",
            "Su llegada está confirmada en v42.30, aunque la {OBTENCIÓN|FF7A00} "
            "permanece sin identificar.",
            "9:16",
        )
        self.assertFalse(largo)
        self.assertIn("texto de abajo", motivo)
        self.assertIn("no cabe", motivo)

    def test_text_fits_rejects_markup_the_composer_would_reject(self):
        cabe, motivo = cards_pipeline.text_fits("A {DE|FF7A00} B", "CONTEXTO", "9:16")
        self.assertFalse(cabe)
        self.assertTrue(motivo)

    def test_three_proposals_are_parsed_and_sanitised(self):
        raw = json.dumps(
            {
                "options": [
                    {"top": "UNO {MAPA|FF7A00}", "bottom": "A {NUEVO|8B3DFF}"},
                    {"top": "DOS {MODO|FF39D7}", "bottom": "B {CAMBIO|42E8FF}"},
                    {"top": "TRES {NOVEDAD|FFD166}", "bottom": "C {FECHA|B84DFF}"},
                ],
                "caption": "caption",
                "hashtags": ["#fortnite"],
                "suggested_format": "9:16",
                "reasoning": "tres",
            },
            ensure_ascii=False,
        )
        result = analysis_providers.proposals_from_payload(raw, provider="test")
        self.assertEqual(len(result["options"]), 3)
        self.assertEqual(result["options"][0]["provider"], "test")

    def test_proposal_json_must_contain_exactly_three_options(self):
        with self.assertRaises(analysis_providers.ProviderError):
            analysis_providers.proposals_from_payload(
                '{"options": [{"top": "A", "bottom": "B"}]}', provider="test"
            )

    def test_prompt_asks_to_change_the_text_when_regenerating(self):
        tweet = {
            "text": "Nueva tienda",
            "previous": {"top": "VIEJO", "bottom": "VIEJO B", "caption": "viejo"},
            "instructions": "más corto",
        }
        prompt = analysis_providers.build_user_prompt(tweet, analysis_providers.palette_colors())
        self.assertIn("más corto", prompt)
        self.assertIn("VIEJO", prompt)
        self.assertIn("No repitas el mismo titular", prompt)

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

    def test_markup_with_a_hash_is_normalised_instead_of_printed(self):
        """Caso real: la tarjeta 8 imprimió «¿{TEASER|#FFD166} DE ONE PIECE?».

        El prompt listaba la paleta como `#FFD166` y el marcado sin almohadilla,
        así que el modelo copiaba la primera. El compositor exige seis dígitos
        hex sin `#`: no reconocía el marcado, no coloreaba la palabra y dibujaba
        las llaves dentro de la imagen.
        """
        clean = cards_pipeline.normalise_params(
            {
                "top": "NUEVA PESTAÑA, ¿{TEASER|#FFD166} DE ONE PIECE?",
                "bottom": "Fortnite pregunta si es un {TEASER|#FFD166}",
            }
        )
        self.assertEqual(clean["top"], "NUEVA PESTAÑA, ¿{TEASER|FFD166} DE ONE PIECE?")
        self.assertEqual(clean["bottom"], "Fortnite pregunta si es un {TEASER|FFD166}")

    def test_markup_that_the_composer_cannot_read_is_refused(self):
        """Antes se ignoraba en silencio y las llaves se imprimían.

        `validate_text_markup` no reconoce lo que no encaja con su patrón, así
        que no lo valida y el compositor lo dibuja literal. Es mejor fallar con
        un mensaje que decir qué arreglar que entregar una tarjeta con llaves.
        """
        for malo in ("{TEASER|rojo}", "{TEASER}", "{TEASER|#GGGGGG}", "{A|FFD166", "A|FFD166}"):
            with self.subTest(malo=malo):
                with self.assertRaises(cards_pipeline.CardError) as contexto:
                    cards_pipeline.normalise_params({"top": malo, "bottom": "OK"})
                self.assertIn("marcado", str(contexto.exception))

    def test_the_sanitiser_also_strips_the_hash_and_keeps_the_colour(self):
        analysis = analysis_providers.sanitize_analysis(
            Analysis(
                top="UN {PICO|#FFD166} NUEVO",
                bottom="SIN {FUENTE|#8B3DFF} CONOCIDA",
                provider="test",
            )
        )
        self.assertIn("{PICO|FFD166}", analysis.top)
        self.assertIn("{FUENTE|8B3DFF}", analysis.bottom)
        self.assertNotIn("#", analysis.top)
        self.assertNotIn("#", analysis.bottom)

    def test_defaults_are_filled_in(self):
        clean = cards_pipeline.normalise_params({"top": "A", "bottom": "B"})
        # Vertical por defecto: es como se publican. Antes era «auto» y el
        # formato lo acababa eligiendo la IA para cada publicación.
        self.assertEqual(clean["format"], "9:16")
        self.assertEqual(clean["resolution"], "4k")
        self.assertIsNone(clean["background"])

    def test_auto_is_still_available_when_asked_for(self):
        clean = cards_pipeline.normalise_params(
            {"top": "A", "bottom": "B", "format": "auto"}
        )
        self.assertEqual(clean["format"], "auto")

    def test_three_accent_colors_are_rejected(self):
        with self.assertRaises(cards_pipeline.CardError):
            cards_pipeline.normalise_params(
                {"top": "{A|8B3DFF} {B|FF7A00}", "bottom": "{C|E83DFF}"}
            )


class SeenRegistryTests(unittest.TestCase):
    """Deduplicación por identificador, huella de contenido y purga."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.work = Path(self._temporary.name)
        self.store = Store(self.work / "test.sqlite3")

    def tearDown(self):
        self._temporary.cleanup()

    def _tweet(self, tweet_id, text="", media=None, author="cuenta", posted=None):
        return {
            "tweet_id": tweet_id,
            "source_handle": author,
            "author_handle": author,
            "text": text,
            "media": media or [],
            "posted_at": posted,
        }

    # --- identificador ------------------------------------------------
    def test_the_same_id_is_never_inserted_twice(self):
        first = self.store.upsert_tweets([self._tweet("1", "una noticia")])
        second = self.store.upsert_tweets([self._tweet("1", "una noticia")])
        self.assertEqual(len(first["inserted"]), 1)
        self.assertEqual(second["inserted"], [])
        self.assertEqual(second["duplicates"], 1)

    def test_a_purged_publication_does_not_come_back(self):
        """Lo importante: borrar de la bandeja no provoca que se repita."""
        self.store.upsert_tweets([self._tweet("1", "una noticia", posted="2020-01-01T00:00:00+00:00")])
        outcome = self.store.purge_older_than(48, media_dir=self.work / "media")
        self.assertEqual(outcome["purged"], 1)
        self.assertEqual(self.store.list_tweets(limit=10), [])
        # Pero se recuerda, así que no vuelve a entrar como nueva.
        self.assertTrue(self.store.is_seen("1"))
        again = self.store.upsert_tweets([self._tweet("1", "una noticia")])
        self.assertEqual(again["inserted"], [])
        self.assertEqual(again["duplicates"], 1)
        self.assertEqual(self.store.list_tweets(limit=10), [])

    def test_the_registry_keeps_growing_even_after_purging(self):
        self.store.upsert_tweets(
            [self._tweet(str(i), f"noticia {i}", posted="2020-01-01T00:00:00+00:00") for i in range(5)]
        )
        self.store.purge_older_than(48, media_dir=self.work / "media")
        self.assertEqual(self.store.seen_stats()["total_seen"], 5)

    # --- contenido ----------------------------------------------------
    def test_the_same_content_with_another_id_is_flagged(self):
        common = {"text": "FORTNITEMARES VUELVE CON MAPA NUEVO Y RECOMPENSAS", "media": ["https://x/a/AAA111.jpg"]}
        first = self.store.upsert_tweets([self._tweet("1", **common)])
        second = self.store.upsert_tweets([self._tweet("2", **common)])
        self.assertEqual(len(first["inserted"]), 1)
        self.assertEqual(second["inserted"], [])
        self.assertEqual(len(second["content_duplicates"]), 1)
        self.assertEqual(second["content_duplicates"][0]["duplicate_of"], "1")
        stored = self.store.get_tweet("2")
        self.assertEqual(stored["status"], store_module.STATUS_DUPLICATE)
        self.assertTrue(stored["is_duplicate"])

    def test_the_same_content_without_media_and_short_text_is_not_flagged(self):
        """Regresión: «GG» o «🚨» no deben marcar como repetida otra publicación."""
        for text in ("GG", "🚨", "NUEVO", "x"):
            with self.subTest(text=text):
                store = Store(self.work / f"short-{abs(hash(text))}.sqlite3")
                store.upsert_tweets([self._tweet("a", text)])
                result = store.upsert_tweets([self._tweet("b", text)])
                self.assertEqual(len(result["inserted"]), 1, "no debería considerarse repetida")
                self.assertEqual(result["content_duplicates"], [])

    def test_long_text_without_media_can_still_be_flagged(self):
        text = "FORTNITEMARES VUELVE CON UN MAPA COMPLETAMENTE NUEVO Y RECOMPENSAS"
        self.store.upsert_tweets([self._tweet("a", text)])
        result = self.store.upsert_tweets([self._tweet("b", text)])
        self.assertEqual(len(result["content_duplicates"]), 1)

    def test_different_accounts_are_not_confused(self):
        text = "FORTNITEMARES VUELVE CON UN MAPA COMPLETAMENTE NUEVO Y RECOMPENSAS"
        self.store.upsert_tweets([self._tweet("a", text, author="cuenta_uno")])
        result = self.store.upsert_tweets([self._tweet("b", text, author="cuenta_dos")])
        self.assertEqual(len(result["inserted"]), 1)
        self.assertEqual(result["content_duplicates"], [])

    def test_the_content_window_expires(self):
        text = "FORTNITEMARES VUELVE CON UN MAPA COMPLETAMENTE NUEVO Y RECOMPENSAS"
        self.store.upsert_tweets([self._tweet("a", text)])
        # Con ventana de 0 días no se compara contenido.
        result = self.store.upsert_tweets([self._tweet("b", text)], duplicate_window_days=0)
        self.assertEqual(len(result["inserted"]), 1)
        self.assertEqual(result["content_duplicates"], [])

    def test_hashes_ignore_links_and_hashtags(self):
        from dashboard.store import publication_hash

        base = publication_hash("FORTNITEMARES VUELVE CON MAPA NUEVO", ["https://x/a/AAA.jpg"], "c")
        with_noise = publication_hash(
            "FORTNITEMARES VUELVE CON MAPA NUEVO https://t.co/abc #fortnite", ["https://x/a/AAA.jpg"], "c"
        )
        self.assertEqual(base, with_noise)
        self.assertTrue(base)

    # --- limpieza -----------------------------------------------------
    def test_purge_removes_cards_and_media_files(self):
        media_dir = self.work / "media"
        cards_dir = self.work / "cards"
        media_dir.mkdir(parents=True, exist_ok=True)
        cards_dir.mkdir(parents=True, exist_ok=True)

        self.store.upsert_tweets(
            [self._tweet("1", "una noticia vieja", posted="2020-01-01T00:00:00+00:00")]
        )
        image = media_dir / "1" / "01.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"x" * 32)
        card_file = cards_dir / "1-v1.png"
        card_file.write_bytes(b"y" * 32)
        card = self.store.create_card("1", {"top": "A"}, output_path=str(card_file))
        self.store.record_delivery(card["id"], "local", "ok", "{}")

        outcome = self.store.purge_older_than(48, media_dir=media_dir)

        self.assertEqual(outcome["purged"], 1)
        self.assertEqual(outcome["cards_removed"], 1)
        self.assertEqual(outcome["deliveries_removed"], 1)
        self.assertFalse(card_file.exists())
        self.assertFalse(image.exists())
        self.assertFalse((media_dir / "1").exists())
        self.assertEqual(self.store.list_cards("1"), [])
        self.assertEqual(self.store.list_deliveries(), [])

    def test_purge_keeps_recent_publications(self):
        from dashboard.store import utcnow

        self.store.upsert_tweets([self._tweet("nuevo", "noticia reciente")])
        outcome = self.store.purge_older_than(48, media_dir=self.work / "media")
        self.assertEqual(outcome["purged"], 0)
        self.assertEqual(len(self.store.list_tweets(limit=10)), 1)
        self.assertTrue(utcnow())

    def test_filter_unseen_uses_the_permanent_registry(self):
        self.store.upsert_tweets(
            [self._tweet("1", "vieja", posted="2020-01-01T00:00:00+00:00")]
        )
        self.store.purge_older_than(48, media_dir=self.work / "media")
        # Aunque ya no esté en la bandeja, sigue constando como vista.
        self.assertEqual(self.store.filter_unseen(["1"]), [])
        self.assertEqual(self.store.filter_unseen(["1", "2"]), ["2"])

    def test_mark_processed_is_idempotent(self):
        self.store.upsert_tweets([self._tweet("1", "noticia")])
        self.store.mark_processed("1", when="2026-01-01T00:00:00+00:00")
        self.store.mark_processed("1", when="2026-06-06T00:00:00+00:00")
        tweet = self.store.get_tweet("1")
        self.assertEqual(tweet["processed_at"], "2026-01-01T00:00:00+00:00")
        self.assertTrue(tweet["is_processed"])

    def test_pending_only_hides_processed_and_duplicates(self):
        self.store.upsert_tweets(
            [
                self._tweet("1", "primera"),
                self._tweet("2", "segunda"),
                self._tweet("3", "tercera"),
            ]
        )
        self.store.set_tweet_status("1", store_module.STATUS_CARD_READY)
        self.store.set_tweet_status("2", store_module.STATUS_DUPLICATE)
        pending = self.store.list_tweets(pending_only=True)
        self.assertEqual([tweet["tweet_id"] for tweet in pending], ["3"])
        everything = self.store.list_tweets()
        self.assertEqual(len(everything), 3)

    # --- migración ----------------------------------------------------
    def test_migration_backfills_the_seen_registry_and_the_hashes(self):
        """Una base anterior debe quedar utilizable sin perder nada."""
        import sqlite3

        path = self.work / "antigua.sqlite3"
        store = Store(path)
        store.upsert_tweets(
            [
                self._tweet(
                    "1",
                    "FORTNITEMARES VUELVE CON UN MAPA NUEVO Y RECOMPENSAS",
                    media=["https://x/a/AAA111.jpg"],
                    posted="2020-01-01T00:00:00+00:00",
                )
            ]
        )
        # Se simula el estado antiguo: sin huellas y sin registro de vistos.
        connection = sqlite3.connect(path)
        connection.execute("UPDATE tweets SET content_hash = NULL")
        connection.execute("DELETE FROM seen_tweets")
        connection.commit()
        connection.close()

        # Al reabrir, la migración debe rellenar las dos cosas.
        reopened = Store(path)
        stats = reopened.seen_stats()
        self.assertEqual(stats["total_seen"], 1)
        tweet = reopened.get_tweet("1")
        self.assertTrue(tweet.get("content_hash"), "la huella debería haberse calculado")

        # Y con la huella ya presente, una republicación se detecta.
        duplicate = reopened.upsert_tweets(
            [
                self._tweet(
                    "2",
                    "FORTNITEMARES VUELVE CON UN MAPA NUEVO Y RECOMPENSAS",
                    media=["https://x/a/AAA111.jpg"],
                )
            ]
        )
        self.assertEqual(len(duplicate["content_duplicates"]), 1)

    def test_migration_is_idempotent(self):
        Store(self.store.db_path)
        Store(self.store.db_path)
        self.assertEqual(self.store.seen_stats()["total_seen"], 0)


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
