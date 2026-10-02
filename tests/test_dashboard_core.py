"""Tests del núcleo del dashboard: datos, cuentas y publicaciones.

No usan red ni navegador: todo se comprueba contra fixtures y contra la base
de datos real. Ya no hay nada de análisis ni de composición que probar."""

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
from dashboard.providers import timelines as timeline_providers  # noqa: E402
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

    def test_media_filters_separate_images_videos_and_text(self):
        self.store.upsert_tweets(
            [
                {"tweet_id": "img", "source_handle": "c", "media": ["https://pbs.twimg.com/media/a.jpg"]},
                {"tweet_id": "vid", "source_handle": "c", "has_video": True},
                {
                    "tweet_id": "mixed",
                    "source_handle": "c",
                    "media": [
                        "https://pbs.twimg.com/media/photo.jpg",
                        "https://pbs.twimg.com/amplify_video_thumb/1/img/poster.jpg",
                    ],
                },
                {"tweet_id": "txt", "source_handle": "c", "text": "solo texto"},
            ]
        )
        self.assertEqual([tweet["tweet_id"] for tweet in self.store.list_tweets(media_kind="images")], ["img"])
        self.assertEqual(
            {tweet["tweet_id"] for tweet in self.store.list_tweets(media_kind="videos")},
            {"vid", "mixed"},
        )
        mixed = self.store.get_tweet("mixed")
        self.assertEqual(mixed["media_types"], ["image", "video"])
        self.assertEqual(self.store.count_by_media(), {"all": 4, "images": 1, "videos": 2})
        self.assertTrue(self.store.get_tweet("vid")["has_media"])


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

    def test_video_markup_is_kept_even_without_an_image_tag(self):
        rss = """<?xml version="1.0"?>
        <rss><channel><item>
          <title>Vídeo</title>
          <description><![CDATA[<p>FORTNITEMARES VUELVE</p><video poster="https://pbs.twimg.com/ext_tw_video_thumb/1/pu/img/a.jpg"></video>]]></description>
          <guid>2105562614461776336</guid>
          <link>https://nitter.example/cuenta/status/2105562614461776336</link>
        </item></channel></rss>"""
        record = timeline_providers.parse_nitter_rss(rss, source_handle="c")[0]
        self.assertTrue(record.has_video)
        self.assertIn("ext_tw_video_thumb", record.media[0])

    def test_video_word_in_text_does_not_promote_an_image(self):
        rss = """<?xml version="1.0"?>
        <rss><channel><item>
          <title>Foto con texto sobre video_thumb</title>
          <description><![CDATA[<p>Una foto cuyo texto menciona tw_video</p><img src="https://pbs.twimg.com/media/photo.jpg" />]]></description>
          <guid>2105562614461776337</guid>
          <link>https://nitter.example/cuenta/status/2105562614461776337</link>
        </item></channel></rss>"""
        record = timeline_providers.parse_nitter_rss(rss, source_handle="cuenta")[0]
        self.assertFalse(record.has_video)


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
    def test_purge_removes_old_publications_and_their_files(self):
        media_dir = self.work / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        self.store.upsert_tweets(
            [self._tweet("1", "una noticia vieja", posted="2020-01-01T00:00:00+00:00")]
        )
        image = media_dir / "1" / "01.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"x" * 32)

        outcome = self.store.purge_older_than(48, media_dir=media_dir)

        self.assertEqual(outcome["purged"], 1)
        self.assertFalse(image.exists())
        self.assertFalse((media_dir / "1").exists())

    def test_purge_never_removes_what_is_marked_ready(self):
        """Lo marcado como listo es la **única** excepción a la limpieza.

        Es la razón de existir de la marca: la retención borra por edad sin
        mirar el estado, así que sin esta excepción se perdería justo lo que se
        quería conservar.
        """
        media_dir = self.work / "media"
        self.store.upsert_tweets(
            [
                self._tweet("1", "vieja sin marcar", posted="2020-01-01T00:00:00+00:00"),
                self._tweet("2", "vieja marcada", posted="2020-01-01T00:00:00+00:00"),
            ]
        )
        self.store.set_tweet_status("2", store_module.STATUS_READY)

        outcome = self.store.purge_older_than(48, media_dir=media_dir)

        self.assertEqual(outcome["purged"], 1, "solo debía borrarse la no marcada")
        quedan = [t["tweet_id"] for t in self.store.list_tweets(limit=10)]
        self.assertEqual(quedan, ["2"])
        self.assertEqual(self.store.get_tweet("2")["status"], store_module.STATUS_READY)

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


if __name__ == "__main__":
    unittest.main()
