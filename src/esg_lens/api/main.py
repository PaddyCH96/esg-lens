"""Local FastAPI entry point for ESG Lens."""

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager, closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from esg_lens.config import PROJECT_ROOT, get_scoring_config, settings
from esg_lens.db.engine import get_connection, get_db, init_db
from esg_lens.logging import configure_logging
from esg_lens.jobs.runner import DEFAULT_SOURCES, enqueue, run_job, sweep
from esg_lens.nlp.pipeline import SignalPipeline, signal_version
from esg_lens.nlp.registry import model_registry

log = logging.getLogger(__name__)
PREFIX = "/api/v1"
TICKER = re.compile(r"[A-Z0-9.\-]{1,10}")
DB = Annotated[object, Depends(get_db)]


def normalize_ticker(value):
    value = value.strip().upper()
    if not TICKER.fullmatch(value):
        raise ValueError(f"Invalid ticker: {value}")
    return value


class AnalyzeRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=25)
    force_refresh: bool = False
    lookback_days: int = Field(default=365, ge=30, le=730)
    sources: list[str] = Field(default_factory=lambda: list(DEFAULT_SOURCES), min_length=1)

    @field_validator("tickers")
    @classmethod
    def validate_tickers(cls, values):
        return list(dict.fromkeys(normalize_ticker(value) for value in values))

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, values):
        if any(value not in DEFAULT_SOURCES for value in values):
            raise ValueError("Enabled sources: " + ", ".join(DEFAULT_SOURCES))
        return list(dict.fromkeys(values))


def problem(request, status, detail, headers=None):
    titles = {400: "Malformed request", 404: "Resource not found", 409: "Job limit reached",
              422: "Validation failed", 500: "Internal error", 503: "Models unavailable"}
    payload = {"type": "about:blank", "title": titles.get(status, "Request failed"),
               "status": status, "instance": request.url.path}
    if isinstance(detail, dict):
        payload.update(detail)
    else:
        payload["detail"] = detail
    return JSONResponse(payload, status_code=status, media_type="application/problem+json", headers=headers)


def score_row(conn, ticker, as_of=None):
    try:
        ticker = normalize_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = conn.execute(
        "SELECT s.*, snap.payload FROM esg_scores s LEFT JOIN score_snapshots snap ON snap.score_id=s.id "
        "WHERE s.ticker=? AND (? IS NULL OR date(s.computed_at)<=?) "
        "ORDER BY julianday(s.computed_at) DESC,s.id DESC LIMIT 1",
        (ticker, as_of, as_of),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"No score for {ticker}. Submit a portfolio analysis first.")
    return row


def create_app(models=model_registry, pipeline_factory=SignalPipeline):
    @asynccontextmanager
    async def lifespan(app):
        configure_logging(settings.LOG_LEVEL)
        init_db()
        with closing(get_connection()) as conn:
            sweep(conn)
        app.state.pipeline = None
        app.state.job_lock = asyncio.Lock()

        def warm():
            models.warm()
            return pipeline_factory(models, get_scoring_config())

        async def load_models():
            try:
                app.state.pipeline = await asyncio.to_thread(warm)
            except Exception:
                log.exception("Could not load NLP models")

        loading = asyncio.create_task(load_models())
        try:
            yield
        finally:
            await loading

    app = FastAPI(title="ESG Lens", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return problem(request, exc.status_code, exc.detail, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = exc.errors()
        status = 400 if any(error["type"] == "json_invalid" for error in errors) else 422
        return problem(request, status, "; ".join(error["msg"] for error in errors))

    @app.exception_handler(Exception)
    async def server_error(request, exc):
        log.error("Unhandled request error", exc_info=exc)
        return problem(request, 500, "See server logs.")

    @app.get(PREFIX + "/healthz")
    def health(request: Request, conn: DB):
        conn.execute("SELECT 1").fetchone()
        loaded = request.app.state.pipeline is not None
        if not loaded:
            raise HTTPException(503, {"detail": "NLP models are not ready.", "db": "ok",
                                      "models_loaded": False, "version": app.version})
        return {"status": "ok", "db": "ok", "models_loaded": True, "version": app.version}

    @app.post(PREFIX + "/portfolio/analyze", status_code=202)
    def analyze(body: AnalyzeRequest, request: Request, tasks: BackgroundTasks, conn: DB):
        pipeline = request.app.state.pipeline
        if pipeline is None:
            raise HTTPException(503, "NLP models are not ready.")
        if len(body.tickers) > settings.MAX_TICKERS_PER_JOB:
            raise HTTPException(422, f"At most {settings.MAX_TICKERS_PER_JOB} tickers are allowed.")
        queued = enqueue(conn, body.tickers, body.force_refresh)
        if not queued:
            raise HTTPException(409, {"detail": "An analysis is already active.", "retry_after_seconds": 5},
                                headers={"Retry-After": "5"})
        job_id, created = queued
        tasks.add_task(run_job, pipeline, request.app.state.job_lock, job_id, body.tickers,
                       body.lookback_days, body.sources, body.force_refresh)
        return {"job_id": job_id, "status": "queued", "tickers": body.tickers,
                "n_total": len(body.tickers), "created_at": created,
                "estimated_seconds": len(body.tickers) * 15,
                "links": {"status": f"{PREFIX}/portfolio/{job_id}"}}

    @app.get(PREFIX + "/portfolio/{job_id}")
    def job_status(job_id: str, conn: DB):
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, "Unknown job.")
        items = [dict(row) for row in conn.execute(
            "SELECT i.ticker,i.status,i.n_documents,s.composite_score AS score,i.error "
            "FROM job_items i LEFT JOIN esg_scores s ON s.id=i.score_id WHERE i.job_id=? ORDER BY i.id",
            (job_id,),
        )]
        done, failed, total = job["n_completed"], job["n_failed"], job["n_total"]
        payload = {"job_id": job_id, "status": job["status"], "items": items, "error": job["error"],
                   "created_at": job["created_at"], "started_at": job["started_at"],
                   "finished_at": job["finished_at"],
                   "progress": {"completed": done, "failed": failed, "total": total,
                                "percent": round((done + failed) * 100 / total, 1),
                                "current_ticker": job["current_ticker"], "current_stage": job["current_stage"]}}
        if job["finished_at"]:
            scores = [item["score"] for item in items if item["score"] is not None]
            payload["summary"] = {"portfolio_score": sum(scores) / len(scores) if scores else None,
                                  "weighting": "equal", "scored": len(scores), "unscored": total - len(scores)}
            payload["links"] = {"scores": [f"{PREFIX}/company/{item['ticker']}/score" for item in items
                                           if item["status"] == "done"]}
        return payload

    @app.get(PREFIX + "/company/{ticker}/score")
    def company_score(ticker: str, conn: DB, include_evidence: bool = True,
                      evidence_limit: Annotated[int, Query(ge=0, le=20)] = 5, as_of: date | None = None):
        row = score_row(conn, ticker, as_of.isoformat() if as_of else None)
        company = conn.execute("SELECT * FROM companies WHERE ticker=?", (row["ticker"],)).fetchone()
        snapshot = json.loads(row["payload"] or "{}")
        pillars = snapshot.get("pillars") or {
            p: {"score": row[f"{p.lower()}_score"], "penalty": row[f"{p.lower()}_penalty"],
                "n_signals": None, "status": "ok" if row[f"{p.lower()}_score"] is not None else "insufficient_data"}
            for p in "ESG"
        }
        evidence = {"n_documents_collected": row["n_documents"], "n_documents_esg_relevant": row["n_signals"],
                    "window": {"start": row["window_start"], "end": row["window_end"]},
                    "sources": snapshot.get("sources", {})}
        if include_evidence:
            evidence["top_contributors"] = snapshot.get("top_contributors", [])[:evidence_limit]
        computed = datetime.fromisoformat(row["computed_at"])
        if computed.tzinfo is None:
            computed = computed.replace(tzinfo=timezone.utc)
        return {"ticker": row["ticker"],
                "company": {k: company[k] for k in ("name", "sector", "industry", "country", "market_cap")},
                "score": {"composite": row["composite_score"], "status": row["status"], "confidence": row["confidence"],
                          "sector_percentile": row["sector_percentile"], "pillars": pillars,
                          "pillar_weights": snapshot.get("pillar_weights"),
                          "pillar_weights_note": "Weights are renormalised over pillars with sufficient evidence."},
                "evidence": evidence,
                "benchmark": {"external_esg_score": company["external_esg_score"],
                              "external_esg_provider": company["external_esg_provider"],
                              "note": "External scores are for comparison only."},
                "methodology": {"version": row["methodology_version"], "config_hash": row["config_hash"],
                                "docs_url": f"{PREFIX}/methodology"},
                "computed_at": row["computed_at"],
                "is_stale": (datetime.now(timezone.utc) - computed > timedelta(days=settings.RETENTION_DAYS)
                             or row["config_hash"] != get_scoring_config().config_hash)}

    @app.get(PREFIX + "/company/{ticker}/history")
    def company_history(ticker: str, conn: DB, limit: Annotated[int, Query(ge=1, le=100)] = 30):
        latest = score_row(conn, ticker)
        rows = conn.execute(
            "SELECT computed_at,composite_score,e_score,s_score,g_score,confidence,status,methodology_version,config_hash "
            "FROM esg_scores WHERE ticker=? ORDER BY julianday(computed_at) DESC,id DESC LIMIT ?",
            (latest["ticker"], limit),
        )
        return {"ticker": latest["ticker"], "items": [dict(row) for row in rows]}

    @app.get(PREFIX + "/company/{ticker}/documents")
    def company_documents(ticker: str, conn: DB, pillar: Annotated[str | None, Query(pattern="^[ESG]$")] = None,
                          included: bool | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 50,
                          offset: Annotated[int, Query(ge=0)] = 0):
        latest = score_row(conn, ticker)
        snapshot = json.loads(latest["payload"] or "{}")
        version = snapshot.get("model_version") or signal_version(get_scoring_config())
        rows = conn.execute(
            """SELECT d.id,d.title,d.url,d.source,d.domain,d.published_at,
                      s.id AS signal_id,s.pillar,s.category,s.included,s.exclusion_reason
               FROM raw_documents d LEFT JOIN esg_signals s ON s.document_id=d.id AND s.model_version=?
               WHERE d.ticker=? AND (? IS NULL OR s.pillar=?) AND (? IS NULL OR s.included=?)
               ORDER BY julianday(d.published_at) DESC,d.id DESC LIMIT ? OFFSET ?""",
            (version, latest["ticker"], pillar, pillar, included, included, limit, offset),
        )
        return {"ticker": latest["ticker"], "limit": limit, "offset": offset, "items": [dict(row) for row in rows]}

    @app.get(PREFIX + "/methodology")
    def methodology():
        cfg = get_scoring_config()
        return {"version": cfg.version, "config_hash": cfg.config_hash, "weights": cfg.raw,
                "docs_url": f"{PREFIX}/methodology/notes"}

    @app.get(PREFIX + "/methodology/notes", response_class=PlainTextResponse)
    def methodology_notes():
        return Path(PROJECT_ROOT, "docs", "scoring_methodology.md").read_text(encoding="utf-8")

    return app


app = create_app()
