#!/usr/bin/env python3
"""Backfill CLI — orchestrates yfinance → GDELT → EDGAR with content_hash dedup and INSERT OR IGNORE."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
from pathlib import Path
import sys

# Ensure src is importable when running as python -m scripts.backfill or python scripts/backfill.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

import structlog

from esg_lens.collectors.edgar import EdgarCollector
from esg_lens.collectors.gdelt import GdeltCollector
from esg_lens.collectors.yfinance_meta import YFinanceMetadataProvider
from esg_lens.db.engine import get_connection, init_db
from esg_lens.db.repositories import CompanyRepo, DocumentRepo

log = structlog.get_logger(__name__)

TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")


async def backfill_tickers(
    tickers: list[str],
    conn: sqlite3.Connection | None = None,
    since: str | None = None,
    force_refresh: bool = False,
    job_id: str | None = None,
) -> dict[str, int]:
    r"""Orchestrate backfill for given tickers using provided connection or new one.

    For each ticker uppercased validated against ^[A-Z0-9.\-]{1,10}$ and capped at 25,
    calls get_connection, ensures companies row exists via yfinance provider first,
    then loops collectors GdeltCollector EdgarCollector via safe_fetch with conn ticker since job_id force_refresh,
    collects list[RawDocument], for each doc computes content_hash and inserts via DocumentRepo.insert
    using INSERT OR IGNORE on (ticker, content_hash) mapping fields ticker source doc_type external_id url domain title body language en published_at filing_type filing_section content_hash collected_at raw_json,
    tracks n_fetched and n_new via cursor rowcount; supports idempotency by content_hash UNIQUE.
    """
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True
        # Ensure schema exists
        try:
            init_db()
        except Exception:
            pass

    total_fetched = 0
    total_new = 0

    # Validate and cap at 25 tickers per plan
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    if len(tickers) > 25:
        log.warning("backfill_ticker_cap_exceeded", requested=len(tickers), cap=25)
        tickers = tickers[:25]

    for ticker in tickers:
        if not TICKER_RE.match(ticker):
            log.error("invalid_ticker", ticker=ticker)
            continue

        # Ensure companies row exists via yfinance provider first
        yprovider = YFinanceMetadataProvider()
        try:
            await yprovider.fetch_yfinance_metadata(ticker, conn, force_refresh=force_refresh)
        except Exception as e:
            log.error("yfinance_backfill_failed", ticker=ticker, error=str(e))

        # Fallback minimal companies row if still missing (FK for raw_documents)
        cur = conn.execute("SELECT ticker FROM companies WHERE ticker = ?", (ticker,))
        if cur.fetchone() is None:
            try:
                repo = CompanyRepo(conn)
                import datetime

                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                repo.upsert(ticker, ticker, fetched_at=now, updated_at=now)
                log.info("backfill_fallback_company_created", ticker=ticker)
            except Exception as e:
                log.error("fallback_company_failed", ticker=ticker, error=str(e))
                continue

        # Loop collectors GdeltCollector EdgarCollector via safe_fetch
        collectors = [GdeltCollector(), EdgarCollector()]
        for collector in collectors:
            try:
                docs = await collector.safe_fetch(conn, ticker, since, job_id=job_id, force_refresh=force_refresh)
            except Exception as e:
                log.error("collector_safe_fetch_failed", ticker=ticker, source=collector.source, error=str(e))
                docs = []

            n_fetched = len(docs)
            total_fetched += n_fetched
            n_new = 0

            for doc in docs:
                # content_hash already computed via base.content_hash; use DocumentRepo INSERT OR IGNORE on (ticker, content_hash)
                doc_dict = {
                    "ticker": doc.ticker,
                    "source": doc.source,
                    "doc_type": doc.doc_type,
                    "external_id": doc.external_id,
                    "url": doc.url,
                    "domain": doc.domain,
                    "title": doc.title,
                    "body": doc.body,
                    "language": "en",
                    "published_at": doc.published_at,
                    "filing_type": doc.filing_type,
                    "filing_section": doc.filing_section,
                    "content_hash": doc.content_hash,
                    "collected_at": doc.collected_at,
                    "raw_json": doc.raw_json,
                }
                # Ensure content_hash exists (fallback)
                if not doc_dict["content_hash"]:
                    from esg_lens.collectors.base import content_hash

                    doc_dict["content_hash"] = content_hash(doc.title, doc.external_id, doc.url)

                try:
                    repo = DocumentRepo(conn)
                    inserted = repo.insert(**doc_dict)
                    if inserted is not None:
                        n_new += 1
                    total_new += 1 if inserted is not None else 0
                except Exception as e:
                    log.warning("document_insert_failed", ticker=ticker, error=str(e))

            # Write collection_runs already handled by safe_fetch but ensure backfill counts n_new correctly; log
            log.info(
                "backfill_ticker_done",
                ticker=ticker,
                source=collector.source,
                n_fetched=n_fetched,
                n_new=n_new,
                job_id=job_id,
            )

    if own_conn:
        try:
            conn.close()
        except Exception:
            pass

    return {"n_fetched": total_fetched, "n_new": total_new}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill ESG Lens collectors with dedup")
    parser.add_argument(
        "--tickers",
        required=True,
        help="Comma-separated tickers e.g. --tickers AAPL,XOM",
    )
    parser.add_argument("--since", default=None, help="Optional ISO date YYYY-MM-DD")
    parser.add_argument("--force-refresh", action="store_true", help="Bypass hishel cache")
    parser.add_argument("--job-id", default=None, help="Optional job_id for collection_runs")
    parser.add_argument("--db-path", default=None, help="Optional DB path override")
    return parser.parse_args(argv)


async def _async_main(args: argparse.Namespace) -> None:
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    # Override DB_PATH if provided
    conn = None
    if args.db_path:
        from esg_lens.db.engine import get_connection as _gc

        conn = _gc(args.db_path)
        # ensure schema
        try:
            init_db(args.db_path)
        except Exception:
            pass

    result = await backfill_tickers(
        tickers,
        conn=conn,
        since=args.since,
        force_refresh=args.force_refresh,
        job_id=args.job_id,
    )
    log.info("backfill_complete", **result)
    print(f"Backfill complete: fetched={result['n_fetched']} new={result['n_new']} tickers={tickers}")
    if conn:
        try:
            conn.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    # Configure logging if not already
    try:
        from esg_lens.logging import configure_logging

        configure_logging()
    except Exception:
        pass

    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
