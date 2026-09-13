#!/usr/bin/env python
"""Backfill `raw_json._rss_feed` onto pre-existing RSS documents (D-2-03).

WHY
---
The entity gate's trust level is a per-feed property declared in `config/rss_feeds.yaml`
(`google_news` -> apply, `yahoo_ticker` -> skip). Rows collected before `collectors/rss.py`
recorded a feed key have nothing to key on, so the gate would have to guess.

THE MAPPING, and why it is sound
--------------------------------
Only Google News requests carried a pillar, because only they are built per pillar; the Yahoo
per-ticker request has none. So for rows written by the old collector:

    raw_json IS NULL                    -> yahoo_ticker
    raw_json contains "_rss_pillar"     -> google_news

Anything else is left ALONE and logged as `rss_provenance_unknown` with its id. Guessing at an
unrecognised shape is how a silent mislabel enters an audit trail.

Re-runnable: rows that already carry `_rss_feed` are skipped, so a second run updates 0 rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from esg_lens.config import settings  # noqa: E402

log = structlog.get_logger(__name__)

GOOGLE = "google_news"
YAHOO = "yahoo_ticker"


def classify(raw_json: str | None) -> str | None:
    """Return the feed key for a legacy row, or None if the shape is unrecognised."""
    if raw_json is None or not str(raw_json).strip():
        return YAHOO
    try:
        parsed = json.loads(raw_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    if "_rss_feed" in parsed:
        return None  # already migrated — caller skips it
    if "_rss_pillar" in parsed:
        return GOOGLE
    return None


def migrate(conn: sqlite3.Connection) -> dict[str, int]:
    """Backfill in place. Parameterised SQL only (T-02-02). Returns a counts summary."""
    counts = {"scanned": 0, "updated": 0, "already": 0, "unknown": 0}
    rows = conn.execute(
        "SELECT id, raw_json FROM raw_documents WHERE source = ?", ("rss",)
    ).fetchall()
    for row in rows:
        doc_id, raw_json = row[0], row[1]
        counts["scanned"] += 1

        if raw_json:
            try:
                existing = json.loads(raw_json)
            except (TypeError, ValueError):
                existing = None
            if isinstance(existing, dict) and "_rss_feed" in existing:
                counts["already"] += 1
                continue
        else:
            existing = None

        feed_key = classify(raw_json)
        if feed_key is None:
            counts["unknown"] += 1
            log.warning("rss_provenance_unknown", document_id=doc_id, raw_json=str(raw_json)[:160])
            continue

        payload = {
            "_rss_pillar": (existing or {}).get("_rss_pillar"),
            "_rss_feed": feed_key,
        }
        conn.execute(
            "UPDATE raw_documents SET raw_json = ? WHERE id = ?",
            (json.dumps(payload), doc_id),
        )
        counts["updated"] += 1

    conn.commit()
    log.info("rss_feed_provenance_migrated", **counts)
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=settings.DB_PATH)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        counts = migrate(conn)
    finally:
        conn.close()
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
