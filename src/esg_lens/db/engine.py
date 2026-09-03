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


def init_db(db_path: str | Path | None = None) -> Path:
    """Apply schema.sql to create all tables + view. Idempotent."""
    path = Path(db_path) if db_path else Path(settings.DB_PATH)
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")

    conn = get_connection(path)
    try:
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
