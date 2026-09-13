#!/usr/bin/env python
"""Add `esg_signals.duplicate_of` to an existing database. Idempotent (02-03, D-2-02).

`data/esg_lens.db` holds 338 rows that every threshold in Phase 2 is calibrated against, and
`scripts/backfill.py` must NEVER be re-run to pick up a schema change. So the column arrives by
migration, and the migration is safe to run twice:

    PYTHONPATH=src .venv/bin/python scripts/migrate_duplicate_of.py

`ALTER TABLE ... ADD COLUMN` on SQLite is O(1) metadata-only and preserves every existing row.
SQLite does not enforce a foreign key added this way against rows that predate it, which is fine:
`duplicate_of` is NULL on all of them and only the pipeline ever writes it.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# `-- ` comment kept identical to schema.sql so the two declarations stay greppably in sync.
ADD_COLUMN_SQL = "ALTER TABLE esg_signals ADD COLUMN duplicate_of INTEGER REFERENCES raw_documents(id)"


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def migrate(db_path: str | Path) -> bool:
    """Add the column if it is absent. Returns True when it applied, False when it was a no-op."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        if has_column(conn, "esg_signals", "duplicate_of"):
            return False
        conn.execute(ADD_COLUMN_SQL)
        conn.commit()
        return True
    finally:
        conn.close()


def main() -> int:
    from esg_lens.config import settings

    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(settings.DB_PATH)
    if not db_path.exists():
        print(f"no database at {db_path}")
        return 1
    applied = migrate(db_path)
    print(f"{'added' if applied else 'already present'}: esg_signals.duplicate_of ({db_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
