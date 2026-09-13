#!/usr/bin/env python
"""Seed companies.name and company_aliases from the cached SEC company_tickers.json.

    PYTHONPATH=src .venv/bin/python scripts/seed_aliases.py [--db data/esg_lens.db]

Idempotent: re-running inserts nothing. It does NOT touch raw_documents, so it is safe to run
against the calibrated corpus — unlike scripts/backfill.py, which must not be re-run.
"""

from __future__ import annotations

import argparse

from esg_lens.db.engine import get_connection
from esg_lens.nlp.alias_seed import seed_from_edgar_cache


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="SQLite path (default: settings.DB_PATH)")
    parser.add_argument("--cache", default=None, help="company_tickers.json path")
    args = parser.parse_args()

    conn = get_connection(args.db)
    try:
        result = seed_from_edgar_cache(conn, cache_path=args.cache)
        print(f"names_set={result['names_set']} aliases_inserted={result['aliases_inserted']}")
        for row in conn.execute(
            "SELECT c.ticker, c.name, COUNT(a.id) AS n "
            "FROM companies c LEFT JOIN company_aliases a ON a.ticker = c.ticker "
            "GROUP BY c.ticker ORDER BY c.ticker"
        ):
            print(f"  {row['ticker']:<6} {row['name']:<28} aliases={row['n']}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
