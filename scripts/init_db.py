#!/usr/bin/env python3
"""Create data/esg_lens.db from src/esg_lens/db/schema.sql."""

import sys
from pathlib import Path

# Ensure src is importable
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from esg_lens.db.engine import init_db
from esg_lens.logging import configure_logging

if __name__ == "__main__":
    configure_logging()
    path = init_db()
    print(f"✓ Database initialised at {path}")

    # Quick sanity: list tables (via get_connection so pragmas are correct)
    from esg_lens.db.engine import get_connection as _gc

    conn = _gc(path)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    print(f"  Tables: {', '.join(tables)}")
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='view'")
    views = [r[0] for r in cur.fetchall()]
    if views:
        print(f"  Views: {', '.join(views)}")
    # Check pragmas (must use get_connection — foreign_keys is per-connection)
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
    print(f"  PRAGMA foreign_keys={fk}, journal_mode={jm}")
    conn.close()
