"""Run one analysis at a time in the local API process."""

import asyncio
import json
import structlog
from structlog.contextvars import bound_contextvars
from contextlib import closing, suppress
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from esg_lens.collectors.edgar import EdgarCollector
from esg_lens.collectors.gdelt import GdeltCollector
from esg_lens.collectors.rss import GoogleNewsCollector, SecLitigationCollector
from esg_lens.collectors.yfinance_meta import YFinanceMetadataProvider
from esg_lens.config import get_scoring_config, settings
from esg_lens.db.engine import get_connection
from esg_lens.db.repositories import CompanyRepo, DocumentRepo
from esg_lens.db.scoring import store_score

log = structlog.get_logger(__name__)
COLLECTORS = {"gdelt": GdeltCollector, "edgar": EdgarCollector,
              "google_rss": GoogleNewsCollector, "sec_litigation": SecLitigationCollector}
DEFAULT_SOURCES = ("google_rss", "edgar", "yfinance")
AVAILABLE_SOURCES = (*COLLECTORS, "yfinance")


def utcnow():
    return datetime.now(timezone.utc)


def enqueue(conn, tickers, force_refresh):
    sweep(conn)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued', 'running')").fetchone()[0]
        if active >= settings.MAX_CONCURRENT_JOBS:
            return None
        job_id = str(uuid4())
        created = utcnow().isoformat()
        conn.execute(
            "INSERT INTO jobs(id,tickers_json,n_total,force_refresh,created_at,heartbeat_at) VALUES(?,?,?,?,?,?)",
            (job_id, json.dumps(tickers), len(tickers), force_refresh, created, created),
        )
        conn.executemany("INSERT INTO job_items(job_id,ticker) VALUES(?,?)",
                         [(job_id, ticker) for ticker in tickers])
        return job_id, created


def sweep(conn):
    now = utcnow()
    stale = (now - timedelta(hours=1)).isoformat()
    cutoff = (now - timedelta(days=settings.RETENTION_DAYS)).isoformat()
    with conn:
        conn.execute(
            """UPDATE job_items SET status='failed', error='Job interrupted', finished_at=?
               WHERE status IN ('pending','running') AND job_id IN (
                 SELECT id FROM jobs WHERE status IN ('queued','running')
                 AND julianday(coalesce(heartbeat_at, created_at)) < julianday(?)
               )""", (now.isoformat(), stale),
        )
        conn.execute(
            """UPDATE jobs SET status='failed', finished_at=?, error='Job interrupted',
               current_stage=NULL, current_ticker=NULL,
               n_failed=(SELECT count(*) FROM job_items WHERE job_id=jobs.id AND status='failed')
               WHERE status IN ('queued','running')
               AND julianday(coalesce(heartbeat_at, created_at)) < julianday(?)""",
            (now.isoformat(), stale),
        )
        conn.execute("DELETE FROM jobs WHERE julianday(finished_at) < julianday(?)", (cutoff,))


def stage(conn, job_id, ticker, value):
    with conn:
        conn.execute("UPDATE jobs SET current_stage=?, current_ticker=?, heartbeat_at=? WHERE id=?",
                     (value, ticker, utcnow().isoformat(), job_id))
        conn.execute("UPDATE job_items SET stage=? WHERE job_id=? AND ticker=?", (value, job_id, ticker))


async def collect(conn, ticker, since, sources, job_id, force_refresh):
    errors = []
    if "yfinance" in sources:
        try:
            metadata = await asyncio.wait_for(
                YFinanceMetadataProvider().fetch_yfinance_metadata(ticker, conn, force_refresh=force_refresh),
                timeout=settings.COLLECTOR_TIMEOUT_SECONDS,
            )
            if metadata is None:
                errors.append("yfinance failed")
        except Exception:
            log.exception("metadata_collection_failed", ticker=ticker)
            errors.append("yfinance failed")
    if not CompanyRepo(conn).get(ticker):
        CompanyRepo(conn).upsert(ticker, ticker, fetched_at=utcnow().isoformat())
    for source, factory in COLLECTORS.items():
        if source not in sources:
            continue
        collector = factory()
        documents = await collector.safe_fetch(conn, ticker, since, job_id=job_id, force_refresh=force_refresh)
        repo = DocumentRepo(conn)
        inserted = sum(repo.insert(**asdict(doc)) is not None for doc in documents)
        collector.update_run_new_count(conn, inserted)
        run = conn.execute("SELECT status FROM collection_runs WHERE id=?", (collector.last_run_id,)).fetchone()
        if not run or run["status"] == "failed":
            errors.append(f"{source} failed")
    return errors


async def heartbeat(conn, job_id):
    while True:
        await asyncio.sleep(15)
        with conn:
            conn.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", (utcnow().isoformat(), job_id))


async def run_job(pipeline, lock, job_id, tickers, lookback, sources, force_refresh):
    async with lock:
        with closing(get_connection()) as conn:
            with conn:
                conn.execute("UPDATE jobs SET status='running', started_at=?, heartbeat_at=? WHERE id=?",
                             (utcnow().isoformat(), utcnow().isoformat(), job_id))
            pulse = asyncio.create_task(heartbeat(conn, job_id))
            try:
                for ticker in tickers:
                    with bound_contextvars(job_id=job_id, ticker=ticker):
                        log.info("ticker_analysis_started")
                        await run_item(conn, pipeline, job_id, ticker, lookback, sources, force_refresh)
                row = conn.execute("SELECT n_completed,n_failed FROM jobs WHERE id=?", (job_id,)).fetchone()
                warnings = conn.execute(
                    "SELECT 1 FROM job_items WHERE job_id=? AND status='done' AND error IS NOT NULL",
                    (job_id,),
                ).fetchone()
                status = ("failed" if not row["n_completed"] else
                          "partial" if row["n_failed"] or warnings else "done")
                with conn:
                    conn.execute("UPDATE jobs SET status=?,finished_at=?,current_ticker=NULL,current_stage=NULL WHERE id=?",
                                 (status, utcnow().isoformat(), job_id))
            finally:
                pulse.cancel()
                with suppress(asyncio.CancelledError):
                    await pulse


def unique_documents(documents):
    unique = {}
    for document in documents:
        title = " ".join((document["title"] or "").casefold().split())
        if document["source"] in ("gdelt", "google_rss") and title and document["domain"]:
            key = (title, document["domain"])
            if key in unique and document["source"] == "google_rss":
                continue
        else:
            key = document["id"]
        unique[key] = document
    return list(unique.values())


async def run_item(conn, pipeline, job_id, ticker, lookback, sources, force_refresh):
    with conn:
        conn.execute("UPDATE job_items SET status='running',started_at=? WHERE job_id=? AND ticker=?",
                     (utcnow().isoformat(), job_id, ticker))
    cfg = get_scoring_config()
    end = utcnow()
    start = end - timedelta(days=min(lookback, cfg.max_age_days))
    documents, score_id, status, error = [], None, "failed", None
    try:
        stage(conn, job_id, ticker, "collecting")
        warnings = await collect(conn, ticker, start.date().isoformat(), sources, job_id, force_refresh)
        marks = ",".join("?" for _ in sources)
        documents = conn.execute(
            f"SELECT * FROM raw_documents WHERE ticker=? AND source IN ({marks}) "
            "AND julianday(published_at) BETWEEN julianday(?) AND julianday(?) ORDER BY id",
            (ticker, *sources, start.isoformat(), end.isoformat()),
        ).fetchall()
        documents = unique_documents(documents)
        if not documents and all(f"{source} failed" in warnings for source in sources):
            raise RuntimeError("All requested sources failed")
        aliases = [row[0] for row in conn.execute("SELECT alias FROM company_aliases WHERE ticker=?", (ticker,))]
        aliases.append(conn.execute("SELECT name FROM companies WHERE ticker=?", (ticker,)).fetchone()[0])
        stage(conn, job_id, ticker, "nlp")
        # Separate connections keep inference off the event loop and away from heartbeat writes.
        def process_signals():
            with closing(get_connection()) as worker_conn:
                pipeline.persist(worker_conn, documents, aliases, end)

        await asyncio.to_thread(process_signals)
        stage(conn, job_id, ticker, "scoring")
        score_id = store_score(conn, ticker, documents, start, end, cfg, job_id, pipeline.sources)
        status, error = "done", ", ".join(warnings) or None
    except Exception:
        log.exception("ticker_analysis_failed", ticker=ticker)
        error = "Analysis failed. See server logs."
    with conn:
        conn.execute("UPDATE job_items SET status=?, error=?, score_id=?, n_documents=?, finished_at=? "
                     "WHERE job_id=? AND ticker=?",
                     (status, error, score_id, len(documents), utcnow().isoformat(), job_id, ticker))
        conn.execute("UPDATE jobs SET n_completed=n_completed+?, n_failed=n_failed+? WHERE id=?",
                     (status == "done", status == "failed", job_id))

