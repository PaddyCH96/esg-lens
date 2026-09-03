"""Collector ABC, RawDocument dataclass, content_hash helper, collection_runs writer."""

from __future__ import annotations

import abc
import hashlib
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from esg_lens.collectors.http import get_http_client

log = structlog.get_logger(__name__)


def content_hash(title: str | None, external_id: str | None, url: str | None = None) -> str:
    """Compute content_hash per data_model.md: sha256(lower(stripped title) | coalesce(external_id, url, ''))"""
    t = (title or "").strip().lower()
    key = external_id or url or ""
    return hashlib.sha256(f"{t}|{key}".encode()).hexdigest()


@dataclass
class RawDocument:
    ticker: str
    source: str  # CHECK gdelt|edgar|yfinance|newsapi
    doc_type: str  # CHECK news|filing_section|press_release
    content_hash: str
    title: str | None = None
    body: str | None = None
    url: str | None = None
    domain: str | None = None
    external_id: str | None = None
    published_at: str | None = None  # ISO-8601 UTC TEXT
    filing_type: str | None = None  # 10-K 8-K DEF 14A
    filing_section: str | None = None  # Item 1 Item 1A
    raw_json: str | None = None
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        # Validate CHECK constraints
        if self.source not in ("gdelt", "edgar", "yfinance", "newsapi"):
            raise ValueError(f"source must be gdelt|edgar|yfinance|newsapi, got {self.source!r}")
        if self.doc_type not in ("news", "filing_section", "press_release"):
            raise ValueError(f"doc_type must be news|filing_section|press_release, got {self.doc_type!r}")
        if self.filing_type is not None and self.filing_type not in ("10-K", "8-K", "DEF 14A"):
            raise ValueError(f"filing_type must be 10-K|8-K|DEF 14A, got {self.filing_type!r}")
        if self.filing_section is not None and self.filing_section not in ("Item 1", "Item 1A"):
            raise ValueError(f"filing_section must be Item 1|Item 1A, got {self.filing_section!r}")
        # Auto-compute content_hash if empty or placeholder
        if not self.content_hash:
            self.content_hash = content_hash(self.title, self.external_id, self.url)


class Collector(abc.ABC):
    """Abstract collector — never raises, writes collection_runs via safe_fetch."""

    source: str

    @abc.abstractmethod
    async def fetch(
        self,
        ticker: str,
        since: str | None = None,
        *,
        job_id: str | None = None,
        force_refresh: bool = False,
    ) -> list[RawDocument]:
        ...

    async def safe_fetch(
        self,
        conn: sqlite3.Connection,
        ticker: str,
        since: str | None = None,
        *,
        job_id: str | None = None,
        force_refresh: bool = False,
    ) -> list[RawDocument]:
        t0 = time.monotonic()
        try:
            docs = await self.fetch(ticker, since, job_id=job_id, force_refresh=force_refresh)
            duration_ms = int((time.monotonic() - t0) * 1000)
            self._write_run(conn, ticker, job_id, "ok", len(docs), len(docs), None, duration_ms, since, None)
            return docs
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            log.error("collector_failed", ticker=ticker, source=self.source, error=str(e))
            err = str(e)[:1000]
            self._write_run(conn, ticker, job_id, "failed", 0, 0, err, duration_ms, since, None)
            return []

    def _write_run(
        self,
        conn: sqlite3.Connection,
        ticker: str,
        job_id: str | None,
        status: str,
        n_fetched: int,
        n_new: int,
        error: str | None,
        duration_ms: int,
        window_start: str | None = None,
        window_end: str | None = None,
    ) -> None:
        try:
            conn.execute(
                "INSERT INTO collection_runs (ticker, source, job_id, status, n_fetched, n_new, window_start, window_end, error, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ticker, self.source, job_id, status, n_fetched, n_new, window_start, window_end, error, duration_ms),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # FK on job_id — retry without job_id so never-raise holds even if caller passed unknown job_id
            try:
                conn.execute(
                    "INSERT INTO collection_runs (ticker, source, job_id, status, n_fetched, n_new, window_start, window_end, error, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (ticker, self.source, None, status, n_fetched, n_new, window_start, window_end, error, duration_ms),
                )
                conn.commit()
            except Exception:
                # Last resort: ensure commit not left in transaction
                try:
                    conn.rollback()
                except Exception:
                    pass
                log.error("collection_runs_write_failed", ticker=ticker, source=self.source, status=status)

    @property
    def http(self):
        return get_http_client()
