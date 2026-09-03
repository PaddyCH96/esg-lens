"""Test fixtures — in-memory SQLite + config overrides."""

import sqlite3
import tempfile
from pathlib import Path

import pytest
from hishel import AsyncSqliteStorage


@pytest.fixture()
def hishel_temp_storage(tmp_path):
    """Temporary hishel AsyncSqliteStorage with 24h TTL for unit tests."""
    storage = AsyncSqliteStorage(database_path=tmp_path / "http.sqlite", default_ttl=24 * 3600)
    return storage


@pytest.fixture()
def db_conn():
    """In-memory DB with schema applied and required pragmas."""
    from esg_lens.db.engine import get_connection

    # Use temp file so WAL/foreign_keys pragmas are testable
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = Path(tf.name)

    # Import here to avoid circular
    from pathlib import Path as _P

    schema_path = _P(__file__).resolve().parents[1] / "src" / "esg_lens" / "db" / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.executescript(schema_sql)
    conn.commit()
    yield conn
    conn.close()
    db_path.unlink(missing_ok=True)
    # WAL files
    for suffix in ("-wal", "-shm"):
        (Path(str(db_path) + suffix)).unlink(missing_ok=True)
