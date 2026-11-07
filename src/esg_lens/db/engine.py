"""SQLite connection factory — WAL + per-connection foreign_keys.

Spec: data_model.md § Design notes — PRAGMA foreign_keys is per-connection
and defaults to OFF. Must be set on EVERY connection.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator

from esg_lens.config import settings

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with required pragmas.

    - journal_mode=WAL is set once per DB file (persists)
    - foreign_keys=ON is set per connection (does not persist)
    """
    path = Path(db_path) if db_path else Path(settings.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Per-connection pragmas (must be on every connection)
    conn.execute("PRAGMA foreign_keys = ON;")
    # WAL is persistent but we set it here too — first open wins
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def _allow_rss_sources(conn: sqlite3.Connection, schema: str) -> None:
    """Expand the source constraint while preserving document IDs and references."""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            table = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='raw_documents'"
            ).fetchone()
            if table is None or "'google_rss'" in table["sql"]:
                return
            ddl = schema.split("CREATE TABLE IF NOT EXISTS raw_documents (", 1)[1].split(");", 1)[0]
            # The column set is unchanged. Name columns so copy order is independent of DDL order.
            columns = ", ".join(row["name"] for row in conn.execute("PRAGMA table_info(raw_documents)"))
            conn.execute("CREATE TABLE raw_documents_rss (" + ddl + ")")
            conn.execute(f"INSERT INTO raw_documents_rss ({columns}) SELECT {columns} FROM raw_documents")
            sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='raw_documents'").fetchone()
            conn.execute("DROP TABLE raw_documents")
            conn.execute("ALTER TABLE raw_documents_rss RENAME TO raw_documents")
            if sequence:
                conn.execute(
                    "UPDATE sqlite_sequence SET seq=max(seq, ?) WHERE name='raw_documents'", (sequence["seq"],)
                )
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise sqlite3.IntegrityError("RSS schema migration broke foreign keys")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def init_db(db_path: str | Path | None = None) -> Path:
    """Apply schema.sql to create all tables + view. Idempotent."""
    path = Path(db_path) if db_path else Path(settings.DB_PATH)
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")

    conn = get_connection(path)
    try:
        _allow_rss_sources(conn, schema_sql)
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()
    return path


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency — yields a connection and closes it after request."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
