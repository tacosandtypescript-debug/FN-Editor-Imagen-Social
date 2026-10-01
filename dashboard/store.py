"""Persistencia del dashboard (SQLite, biblioteca estándar).

Se abre una conexión por operación: el tráfico es bajo y así el acceso es
seguro desde el hilo del servidor y desde el hilo de sondeo sin compartir
cursores. WAL permite leer mientras se escribe.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from . import config
from .clock import CLOCK

#: Estados del ciclo de vida de una publicación.
STATUS_NEW = "nuevo"
STATUS_SELECTED = "seleccionado"
STATUS_ANALYZED = "analizado"
STATUS_PROCESSING = "procesando"
STATUS_CARD_READY = "tarjeta_lista"
STATUS_SENT = "enviado"
STATUS_DISCARDED = "descartado"
STATUS_FAILED = "fallido"
STATUS_DUPLICATE = "duplicado"

ALL_STATUSES = (
    STATUS_NEW,
    STATUS_SELECTED,
    STATUS_ANALYZED,
    STATUS_PROCESSING,
    STATUS_CARD_READY,
    STATUS_SENT,
    STATUS_DISCARDED,
    STATUS_FAILED,
    STATUS_DUPLICATE,
)

#: Estados que cuentan como «ya procesada» de cara a la interfaz.
PROCESSED_STATUSES = (STATUS_CARD_READY, STATUS_SENT)

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    handle          TEXT PRIMARY KEY COLLATE NOCASE,
    active          INTEGER NOT NULL DEFAULT 1,
    added_at        TEXT NOT NULL,
    last_checked_at TEXT,
    last_error      TEXT,
    last_post_id    TEXT
);

CREATE TABLE IF NOT EXISTS tweets (
    tweet_id       TEXT PRIMARY KEY,
    source_handle  TEXT NOT NULL,
    author_handle  TEXT,
    text           TEXT,
    url            TEXT,
    posted_at      TEXT,
    relative_time  TEXT,
    media_json     TEXT NOT NULL DEFAULT '[]',
    fetched_at     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'nuevo',
    status_detail  TEXT,
    analysis_json  TEXT,
    content_hash   TEXT,
    processed_at   TEXT,
    duplicate_of   TEXT
);

--: Registro permanente de lo ya visto. Es minúsculo (identificadores y una
--: marca de tiempo) y **no se purga nunca**, porque es lo que impide que una
--: publicación vuelva a entrar como nueva después de limpiar la bandeja.
CREATE TABLE IF NOT EXISTS seen_tweets (
    tweet_id      TEXT PRIMARY KEY,
    seen_at       TEXT NOT NULL,
    source_handle TEXT,
    author_handle TEXT,
    content_hash  TEXT
);

CREATE INDEX IF NOT EXISTS idx_seen_hash ON seen_tweets(content_hash);
CREATE INDEX IF NOT EXISTS idx_seen_at ON seen_tweets(seen_at);

CREATE INDEX IF NOT EXISTS idx_tweets_status ON tweets(status);
CREATE INDEX IF NOT EXISTS idx_tweets_source ON tweets(source_handle);
CREATE INDEX IF NOT EXISTS idx_tweets_posted ON tweets(posted_at DESC);

CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tweet_id    TEXT NOT NULL,
    version     INTEGER NOT NULL,
    params_json TEXT NOT NULL,
    output_path TEXT,
    meta_json   TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cards_tweet ON cards(tweet_id);

CREATE TABLE IF NOT EXISTS deliveries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id    INTEGER NOT NULL,
    target     TEXT NOT NULL,
    status     TEXT NOT NULL,
    response   TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    level    TEXT NOT NULL DEFAULT 'info',
    message  TEXT NOT NULL,
    tweet_id TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> str:
    """Marca de tiempo actual, con la hora verificada.

    No se usa `datetime.now` directamente a propósito: el reloj del sistema
    puede estar desviado, y de estas marcas depende el orden de la bandeja
    cuando una publicación no trae fecha de origen.
    """
    return CLOCK.now().replace(microsecond=0).isoformat()


class Store:
    """Acceso a datos del dashboard."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else config.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _cursor(self):
        """Conexión de usar y cerrar.

        Usar la conexión como context manager solo confirma la transacción, no
        la cierra: sin este `finally` se filtran descriptores y el archivo
        queda bloqueado (en Windows impide incluso borrarlo).
        """
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialise(self) -> None:
        with self._cursor() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)

    def _migrate(self, connection) -> None:
        """Añade columnas nuevas a bases de datos ya existentes."""
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(tweets)")}
        for name, kind in (
            ("content_hash", "TEXT"),
            ("processed_at", "TEXT"),
            ("duplicate_of", "TEXT"),
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE tweets ADD COLUMN {name} {kind}")

        # El registro de vistos se rellena con lo que ya hubiera en la bandeja:
        # así, al purgar por primera vez, nada vuelve a entrar como nuevo.
        connection.execute(
            "INSERT OR IGNORE INTO seen_tweets "
            "(tweet_id, seen_at, source_handle, author_handle, content_hash) "
            "SELECT tweet_id, COALESCE(fetched_at, posted_at, ?), source_handle, "
            "author_handle, content_hash FROM tweets",
            (utcnow(),),
        )

        # Y se calculan las huellas que falten, para que la detección de la
        # misma noticia republicada cubra también lo que ya estaba guardado.
        pending = connection.execute(
            "SELECT tweet_id, text, media_json, author_handle, source_handle FROM tweets "
            "WHERE content_hash IS NULL OR content_hash = ''"
        ).fetchall()
        for row in pending:
            try:
                media = json.loads(row["media_json"] or "[]")
            except json.JSONDecodeError:
                media = []
            digest = publication_hash(
                row["text"], media, row["author_handle"] or row["source_handle"] or ""
            )
            if not digest:
                continue
            connection.execute(
                "UPDATE tweets SET content_hash = ? WHERE tweet_id = ?",
                (digest, row["tweet_id"]),
            )
            connection.execute(
                "UPDATE seen_tweets SET content_hash = ? WHERE tweet_id = ?",
                (digest, row["tweet_id"]),
            )

    # --- eventos / bitácora -------------------------------------------
    def log(self, message: str, level: str = "info", tweet_id: str | None = None) -> None:
        with self._cursor() as connection:
            connection.execute(
                "INSERT INTO events (ts, level, message, tweet_id) VALUES (?, ?, ?, ?)",
                (utcnow(), level, message, tweet_id),
            )

    def recent_events(self, limit: int = 60) -> list[dict]:
        with self._cursor() as connection:
            rows = connection.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    # --- ajustes -------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._cursor() as connection:
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def all_settings(self) -> dict[str, str]:
        with self._cursor() as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    # --- cuentas -------------------------------------------------------
    def add_account(self, handle: str) -> dict:
        clean = normalise_handle(handle)
        if not clean:
            raise ValueError("el usuario de X no puede estar vacío")
        with self._cursor() as connection:
            connection.execute(
                "INSERT INTO accounts (handle, active, added_at) VALUES (?, 1, ?) "
                "ON CONFLICT(handle) DO UPDATE SET active = 1",
                (clean, utcnow()),
            )
        return self.get_account(clean)  # type: ignore[return-value]

    def remove_account(self, handle: str) -> bool:
        clean = normalise_handle(handle)
        with self._cursor() as connection:
            cursor = connection.execute("DELETE FROM accounts WHERE handle = ?", (clean,))
        return cursor.rowcount > 0

    def set_account_active(self, handle: str, active: bool) -> None:
        with self._cursor() as connection:
            connection.execute(
                "UPDATE accounts SET active = ? WHERE handle = ?",
                (1 if active else 0, normalise_handle(handle)),
            )

    def get_account(self, handle: str) -> dict | None:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE handle = ?", (normalise_handle(handle),)
            ).fetchone()
        return dict(row) if row else None

    def list_accounts(self, active_only: bool = False) -> list[dict]:
        query = "SELECT * FROM accounts"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY handle COLLATE NOCASE"
        with self._cursor() as connection:
            rows = connection.execute(query).fetchall()
        return [dict(row) for row in rows]

    def mark_account_checked(
        self,
        handle: str,
        *,
        error: str | None = None,
        last_post_id: str | None = None,
    ) -> None:
        with self._cursor() as connection:
            connection.execute(
                "UPDATE accounts SET last_checked_at = ?, last_error = ?, "
                "last_post_id = COALESCE(?, last_post_id) WHERE handle = ?",
                (utcnow(), error, last_post_id, normalise_handle(handle)),
            )

    # --- publicaciones -------------------------------------------------
    def upsert_tweets(self, tweets: Iterable[dict], duplicate_window_days: int = 7) -> dict:
        """Inserta publicaciones nuevas evitando repetidas.

        Dos barreras distintas:

        1. **Identificador.** Si el `tweet_id` ya se vio alguna vez —aunque su
           fila se haya purgado de la bandeja— no vuelve a entrar. Esto es lo
           que hace seguro borrar publicaciones antiguas.
        2. **Contenido.** Si otra publicación de la misma cuenta tiene el mismo
           texto y el mismo primer medio vistos hace poco, se guarda marcada
           como duplicada en lugar de colarse como nueva.

        Devuelve `{"inserted", "duplicates", "content_duplicates"}`.
        """
        inserted: list[dict] = []
        duplicates = 0
        content_duplicates: list[dict] = []
        now = utcnow()
        cutoff = (
            CLOCK.now() - timedelta(days=max(0, int(duplicate_window_days)))
        ).replace(microsecond=0).isoformat()

        with self._cursor() as connection:
            for tweet in tweets:
                tweet_id = str(tweet.get("tweet_id") or "").strip()
                if not tweet_id:
                    continue

                media = list(tweet.get("media") or [])
                source_handle = normalise_handle(tweet.get("source_handle", ""))
                author_handle = normalise_handle(
                    tweet.get("author_handle") or tweet.get("source_handle") or ""
                )
                digest = publication_hash(tweet.get("text"), media, author_handle)

                already_seen = connection.execute(
                    "SELECT tweet_id FROM seen_tweets WHERE tweet_id = ?", (tweet_id,)
                ).fetchone()
                if already_seen:
                    duplicates += 1
                    continue

                duplicate_of = None
                if digest and duplicate_window_days > 0:
                    match = connection.execute(
                        "SELECT tweet_id FROM seen_tweets "
                        "WHERE content_hash = ? AND author_handle = ? AND seen_at >= ? "
                        "ORDER BY seen_at DESC LIMIT 1",
                        (digest, author_handle, cutoff),
                    ).fetchone()
                    if match:
                        duplicate_of = match["tweet_id"]

                status = STATUS_DUPLICATE if duplicate_of else STATUS_NEW
                connection.execute(
                    """
                    INSERT INTO tweets (
                        tweet_id, source_handle, author_handle, text, url,
                        posted_at, relative_time, media_json, fetched_at, status,
                        content_hash, duplicate_of, status_detail
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tweet_id) DO NOTHING
                    """,
                    (
                        tweet_id,
                        source_handle,
                        author_handle,
                        tweet.get("text"),
                        tweet.get("url"),
                        tweet.get("posted_at"),
                        tweet.get("relative_time"),
                        json.dumps(media, ensure_ascii=False),
                        now,
                        status,
                        digest,
                        duplicate_of,
                        f"contenido ya visto en {duplicate_of}" if duplicate_of else None,
                    ),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO seen_tweets "
                    "(tweet_id, seen_at, source_handle, author_handle, content_hash) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (tweet_id, now, source_handle, author_handle, digest),
                )

                if duplicate_of:
                    content_duplicates.append(
                        {"tweet_id": tweet_id, "duplicate_of": duplicate_of}
                    )
                else:
                    inserted.append({"tweet_id": tweet_id, **tweet})

        return {
            "inserted": inserted,
            "duplicates": duplicates,
            "content_duplicates": content_duplicates,
        }

    def mark_processed(self, tweet_id: str, when: str | None = None) -> None:
        """Marca una publicación como ya procesada (no se pisa la fecha previa)."""
        with self._cursor() as connection:
            connection.execute(
                "UPDATE tweets SET processed_at = COALESCE(processed_at, ?) WHERE tweet_id = ?",
                (when or utcnow(), str(tweet_id)),
            )

    def seen_stats(self) -> dict:
        """Cuántas publicaciones se recuerdan y desde cuándo."""
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total, MIN(seen_at) AS oldest FROM seen_tweets"
            ).fetchone()
        return {"total_seen": int(row["total"] or 0), "oldest_seen_at": row["oldest"]}

    def is_seen(self, tweet_id: str) -> bool:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT 1 FROM seen_tweets WHERE tweet_id = ?", (str(tweet_id),)
            ).fetchone()
        return row is not None

    def filter_unseen(self, tweet_ids: Iterable[str]) -> list[str]:
        """De los identificadores dados, los que nunca se han visto.

        Se consulta el registro permanente y no la bandeja: así, tras una
        limpieza, no se vuelve a enriquecer lo que ya se conoce.
        """
        wanted = [str(value) for value in tweet_ids if str(value).strip()]
        if not wanted:
            return []
        unseen: list[str] = []
        with self._cursor() as connection:
            for chunk_start in range(0, len(wanted), 400):
                chunk = wanted[chunk_start : chunk_start + 400]
                placeholders = ",".join("?" * len(chunk))
                rows = connection.execute(
                    f"SELECT tweet_id FROM seen_tweets WHERE tweet_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                known = {row["tweet_id"] for row in rows}
                unseen.extend(value for value in chunk if value not in known)
        return unseen

    # --- limpieza ------------------------------------------------------
    def purge_older_than(
        self,
        hours: int,
        media_dir: Path | None = None,
    ) -> dict:
        """Borra de la bandeja lo anterior a `hours`, con sus archivos.

        Solo se borra lo **visible**: el registro de `seen_tweets` se conserva,
        que es lo que impide que esas publicaciones vuelvan a aparecer como
        nuevas en la siguiente búsqueda.
        """
        cutoff = (CLOCK.now() - timedelta(hours=max(1, int(hours)))).replace(
            microsecond=0
        ).isoformat()

        with self._cursor() as connection:
            rows = connection.execute(
                "SELECT tweet_id FROM tweets WHERE COALESCE(posted_at, fetched_at) < ?",
                (cutoff,),
            ).fetchall()
        tweet_ids = [row["tweet_id"] for row in rows]
        if not tweet_ids:
            return {
                "purged": 0,
                "cards_removed": 0,
                "deliveries_removed": 0,
                "files_removed": 0,
                "cutoff": cutoff,
                "seen_kept": self.seen_stats()["total_seen"],
            }

        placeholders = ",".join("?" * len(tweet_ids))
        files_removed = 0
        with self._cursor() as connection:
            cards = connection.execute(
                f"SELECT id, output_path FROM cards WHERE tweet_id IN ({placeholders})",
                tweet_ids,
            ).fetchall()
            card_paths = [Path(row["output_path"]) for row in cards if row["output_path"]]
            card_ids = [row["id"] for row in cards]

            deliveries_removed = 0
            if card_ids:
                card_placeholders = ",".join("?" * len(card_ids))
                cursor = connection.execute(
                    f"DELETE FROM deliveries WHERE card_id IN ({card_placeholders})",
                    card_ids,
                )
                deliveries_removed = cursor.rowcount or 0
            cursor = connection.execute(
                f"DELETE FROM cards WHERE tweet_id IN ({placeholders})", tweet_ids
            )
            cards_removed = cursor.rowcount or 0
            connection.execute(
                f"DELETE FROM tweets WHERE tweet_id IN ({placeholders})", tweet_ids
            )

        for path in card_paths:
            try:
                if path.is_file():
                    path.unlink()
                    files_removed += 1
            except OSError:
                continue

        root = Path(media_dir) if media_dir else config.MEDIA_DIR
        for tweet_id in tweet_ids:
            directory = root / tweet_id
            if directory.is_dir():
                files_removed += sum(1 for item in directory.rglob("*") if item.is_file())
                shutil.rmtree(directory, ignore_errors=True)

        return {
            "purged": len(tweet_ids),
            "cards_removed": cards_removed,
            "deliveries_removed": deliveries_removed,
            "files_removed": files_removed,
            "cutoff": cutoff,
            "seen_kept": self.seen_stats()["total_seen"],
        }

    def list_tweets(
        self,
        *,
        status: str | None = None,
        source_handle: str | None = None,
        pending_only: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if status and status != "todos":
            clauses.append("status = ?")
            params.append(status)
        if pending_only:
            # Lo ya procesado y lo duplicado deja de estorbar en la bandeja.
            marks = ",".join("?" * len(PROCESSED_STATUSES + (STATUS_DUPLICATE,)))
            clauses.append(f"status NOT IN ({marks})")
            params.extend([*PROCESSED_STATUSES, STATUS_DUPLICATE])
        if source_handle:
            clauses.append("source_handle = ?")
            params.append(normalise_handle(source_handle))
        query = "SELECT * FROM tweets"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY COALESCE(posted_at, fetched_at) DESC, rowid DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        with self._cursor() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_decode_tweet(dict(row)) for row in rows]

    def get_tweet(self, tweet_id: str) -> dict | None:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT * FROM tweets WHERE tweet_id = ?", (str(tweet_id),)
            ).fetchone()
        return _decode_tweet(dict(row)) if row else None

    def update_tweet(self, tweet_id: str, **fields: Any) -> None:
        allowed = {
            "text",
            "posted_at",
            "relative_time",
            "media_json",
            "status",
            "status_detail",
            "analysis_json",
            "author_handle",
            "url",
        }
        assignments = {key: value for key, value in fields.items() if key in allowed}
        if not assignments:
            return
        if isinstance(assignments.get("media_json"), (list, tuple)):
            assignments["media_json"] = json.dumps(list(assignments["media_json"]), ensure_ascii=False)
        if isinstance(assignments.get("analysis_json"), dict):
            assignments["analysis_json"] = json.dumps(
                assignments["analysis_json"], ensure_ascii=False
            )
        columns = ", ".join(f"{key} = ?" for key in assignments)
        params = list(assignments.values()) + [str(tweet_id)]
        with self._cursor() as connection:
            connection.execute(f"UPDATE tweets SET {columns} WHERE tweet_id = ?", params)

    def set_tweet_status(self, tweet_id: str, status: str, detail: str | None = None) -> None:
        if status not in ALL_STATUSES:
            raise ValueError(f"estado desconocido: {status}")
        self.update_tweet(tweet_id, status=status, status_detail=detail)

    def count_by_status(self) -> dict[str, int]:
        with self._cursor() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM tweets GROUP BY status"
            ).fetchall()
        counts = {status: 0 for status in ALL_STATUSES}
        for row in rows:
            counts[row["status"]] = row["total"]
        return counts

    # --- tarjetas ------------------------------------------------------
    def create_card(
        self,
        tweet_id: str,
        params: dict,
        *,
        output_path: str | None = None,
        meta: dict | None = None,
    ) -> dict:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS last FROM cards WHERE tweet_id = ?",
                (str(tweet_id),),
            ).fetchone()
            version = int(row["last"]) + 1
            cursor = connection.execute(
                "INSERT INTO cards (tweet_id, version, params_json, output_path, meta_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(tweet_id),
                    version,
                    json.dumps(params, ensure_ascii=False),
                    output_path,
                    json.dumps(meta, ensure_ascii=False) if meta else None,
                    utcnow(),
                ),
            )
            card_id = cursor.lastrowid
        return self.get_card(int(card_id))  # type: ignore[return-value]

    def update_card(
        self,
        card_id: int,
        *,
        output_path: str | None = None,
        meta: dict | None = None,
        params: dict | None = None,
    ) -> None:
        sets: list[str] = []
        params_list: list[Any] = []
        if output_path is not None:
            sets.append("output_path = ?")
            params_list.append(output_path)
        if meta is not None:
            sets.append("meta_json = ?")
            params_list.append(json.dumps(meta, ensure_ascii=False))
        if params is not None:
            sets.append("params_json = ?")
            params_list.append(json.dumps(params, ensure_ascii=False))
        if not sets:
            return
        params_list.append(int(card_id))
        with self._cursor() as connection:
            connection.execute(f"UPDATE cards SET {', '.join(sets)} WHERE id = ?", params_list)

    def get_card(self, card_id: int) -> dict | None:
        with self._cursor() as connection:
            row = connection.execute("SELECT * FROM cards WHERE id = ?", (int(card_id),)).fetchone()
        return _decode_card(dict(row)) if row else None

    def latest_card(self, tweet_id: str) -> dict | None:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT * FROM cards WHERE tweet_id = ? ORDER BY version DESC LIMIT 1",
                (str(tweet_id),),
            ).fetchone()
        return _decode_card(dict(row)) if row else None

    def list_cards(self, tweet_id: str | None = None) -> list[dict]:
        with self._cursor() as connection:
            if tweet_id:
                rows = connection.execute(
                    "SELECT * FROM cards WHERE tweet_id = ? ORDER BY version DESC",
                    (str(tweet_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM cards ORDER BY id DESC LIMIT 200"
                ).fetchall()
        return [_decode_card(dict(row)) for row in rows]

    def latest_card_ids(self) -> dict[str, int]:
        """Mapa `tweet_id -> id` de la tarjeta más reciente de cada publicación."""
        with self._cursor() as connection:
            rows = connection.execute(
                "SELECT tweet_id, MAX(id) AS card_id FROM cards GROUP BY tweet_id"
            ).fetchall()
        return {row["tweet_id"]: int(row["card_id"]) for row in rows}

    # --- entregas ------------------------------------------------------
    def record_delivery(
        self, card_id: int, target: str, status: str, response: str | None = None
    ) -> dict:
        with self._cursor() as connection:
            cursor = connection.execute(
                "INSERT INTO deliveries (card_id, target, status, response, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(card_id), target, status, response, utcnow()),
            )
            delivery_id = cursor.lastrowid
        return {
            "id": delivery_id,
            "card_id": int(card_id),
            "target": target,
            "status": status,
            "response": response,
        }

    def list_deliveries(self, card_id: int | None = None, limit: int = 100) -> list[dict]:
        with self._cursor() as connection:
            if card_id:
                rows = connection.execute(
                    "SELECT * FROM deliveries WHERE card_id = ? ORDER BY id DESC LIMIT ?",
                    (int(card_id), int(limit)),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM deliveries ORDER BY id DESC LIMIT ?", (int(limit),)
                ).fetchall()
        return [dict(row) for row in rows]


#: Texto mínimo (sin medios) para fiarse de la huella de contenido. Por debajo
#: de esto, dos publicaciones con el mismo texto corto («GG», «🚨», «NUEVO»)
#: serían casi siempre cosas distintas, así que no se comparan por contenido.
MIN_TEXT_FOR_HASH = 40


def publication_hash(text, media, author: str) -> str:
    """Huella del contenido de una publicación.

    Sirve para detectar la misma noticia republicada con otro identificador.
    Se normaliza el texto (sin enlaces, sin hashtags, sin espacios de más) y se
    incluye el autor: así dos cuentas distintas que escriban lo mismo no se
    confunden entre ellas, pero la misma cuenta repitiendo su publicación sí.

    Devuelve cadena vacía cuando la huella no es fiable, y entonces solo se
    aplica la deduplicación por identificador.
    """
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    normalized = re.sub(r"https?://\S+", "", normalized)
    normalized = re.sub(r"#\w+", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    media_key = ""
    if media:
        first = str(media[0])
        media_key = Path(urlsplit(first).path).stem or first

    # Sin medios, un texto corto no basta para afirmar que es la misma noticia.
    if not media_key and len(normalized) < MIN_TEXT_FOR_HASH:
        return ""
    if not normalized and not media_key:
        return ""

    payload = f"{(author or '').lower()}|{normalized[:300]}|{media_key}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:32]


# ----------------------------------------------------------------------
#: Dominios que se reconocen como perfil de X.
X_HOST_PATTERN = re.compile(r"^(?:www\.|mobile\.)?(?:x|twitter)\.com(?:/|$)", re.IGNORECASE)


def normalise_handle(handle: str) -> str:
    """Normaliza `@Usuario`, URLs de perfil o texto suelto a un usuario limpio.

    Acepta `@cuenta`, `cuenta`, `x.com/cuenta` o una URL completa de post, y
    descarta esquema, host, query y fragmento.
    """
    value = str(handle or "").strip()
    if not value:
        return ""
    if "://" in value:
        path = urlsplit(value).path
    elif X_HOST_PATTERN.match(value):
        path = urlsplit("https://" + value).path
    elif any(char in value for char in "/?#"):
        path = urlsplit(value).path
    else:
        path = value

    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return ""
    # El primer segmento de la ruta es siempre el usuario: en
    # /usuario/status/123 el último sería el identificador del post.
    candidate = segments[0].lstrip("@").strip()
    return "".join(char for char in candidate if char.isalnum() or char == "_")


def _decode_tweet(row: dict) -> dict:
    try:
        row["media"] = json.loads(row.get("media_json") or "[]")
    except json.JSONDecodeError:
        row["media"] = []
    row["has_media"] = bool(row["media"])
    row["is_processed"] = bool(row.get("processed_at")) or row.get("status") in PROCESSED_STATUSES
    row["is_duplicate"] = row.get("status") == STATUS_DUPLICATE
    raw_analysis = row.get("analysis_json")
    if raw_analysis:
        try:
            row["analysis"] = json.loads(raw_analysis)
        except json.JSONDecodeError:
            row["analysis"] = None
    else:
        row["analysis"] = None
    return row


def _decode_card(row: dict) -> dict:
    for key in ("params_json", "meta_json"):
        raw = row.get(key)
        target = key.replace("_json", "")
        if raw:
            try:
                row[target] = json.loads(raw)
            except json.JSONDecodeError:
                row[target] = None
        else:
            row[target] = None
    return row
