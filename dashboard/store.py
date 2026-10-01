"""Persistencia del dashboard (SQLite, biblioteca estándar).

Se abre una conexión por operación: el tráfico es bajo y así el acceso es
seguro desde el hilo del servidor y desde el hilo de sondeo sin compartir
cursores. WAL permite leer mientras se escribe.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from . import config
from .clock import CLOCK

#: Estados del ciclo de vida de una publicación.
STATUS_NEW = "nuevo"
STATUS_SELECTED = "seleccionado"
STATUS_ANALYZED = "analizado"
STATUS_CARD_READY = "tarjeta_lista"
STATUS_SENT = "enviado"
STATUS_DISCARDED = "descartado"
STATUS_FAILED = "fallido"

ALL_STATUSES = (
    STATUS_NEW,
    STATUS_SELECTED,
    STATUS_ANALYZED,
    STATUS_CARD_READY,
    STATUS_SENT,
    STATUS_DISCARDED,
    STATUS_FAILED,
)

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
    analysis_json  TEXT
);

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
    def upsert_tweets(self, tweets: Iterable[dict]) -> dict:
        """Inserta publicaciones nuevas evitando duplicados.

        Devuelve `{"inserted": [...], "duplicates": n}`.
        """
        inserted: list[dict] = []
        duplicates = 0
        now = utcnow()
        with self._cursor() as connection:
            for tweet in tweets:
                tweet_id = str(tweet.get("tweet_id") or "").strip()
                if not tweet_id:
                    continue
                media = tweet.get("media") or []
                cursor = connection.execute(
                    """
                    INSERT INTO tweets (
                        tweet_id, source_handle, author_handle, text, url,
                        posted_at, relative_time, media_json, fetched_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tweet_id) DO NOTHING
                    """,
                    (
                        tweet_id,
                        normalise_handle(tweet.get("source_handle", "")),
                        normalise_handle(tweet.get("author_handle", "")),
                        tweet.get("text"),
                        tweet.get("url"),
                        tweet.get("posted_at"),
                        tweet.get("relative_time"),
                        json.dumps(list(media), ensure_ascii=False),
                        now,
                        STATUS_NEW,
                    ),
                )
                if cursor.rowcount:
                    inserted.append({"tweet_id": tweet_id, **tweet})
                else:
                    duplicates += 1
        return {"inserted": inserted, "duplicates": duplicates}

    def list_tweets(
        self,
        *,
        status: str | None = None,
        source_handle: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if status and status != "todos":
            clauses.append("status = ?")
            params.append(status)
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
