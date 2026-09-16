"""The NLP orchestrator: every `raw_documents` row becomes exactly ONE `esg_signals` row (NLP-07, NLP-08).

NOTHING IS SILENTLY DROPPED
---------------------------
A document that fails a gate is not skipped — it is persisted with `included=0` and a stated
`exclusion_reason`. Phase 3's coverage reporting and the "explain" endpoint are only meaningful if
the set of documents considered is recoverable from the database. An excluded row is the audit
trail; a missing row is an unanswerable question.

THE RUN IS STAGED OVER THE WHOLE BATCH, NEVER A PER-DOCUMENT LOOP AROUND INFERENCE
----------------------------------------------------------------------------------
    1. load and group by (ticker, doc_type)
    2. skip anything that already has a row at this `model_version` — ONE query, diffed in memory
    3. age gate — pure arithmetic; no reason to spend model time on a document that cannot contribute
    4. near-duplicate clustering over the AGE-SURVIVING set only, so a duplicate never costs inference
    5. entity gate for the surviving documents
    6. batched ESG classification
    7. batched sentiment, then controversy
    8. weights via `esg_lens.scoring.weights`, then one INSERT per document

`handoff_to_backend.md` §4 names loading a transformer inside a loop as a 100x error. Stages 6-7
hand the whole admitted set to the pipeline at once; `registry.py` holds the singletons.

GATE PRECEDENCE IS EXPLICIT, NOT INCIDENTAL
-------------------------------------------
`GATE_PRECEDENCE` below is the declared order, and `_exclude` refuses to overwrite a reason once
set. The stages happen to run in that order today; the tuple is what stops a later reordering of
the stages silently changing which reason a doubly-excluded document records.

The age gate running BEFORE dedup is load-bearing, not an optimisation. An age-excluded document is
removed from the working set entirely, so `clean.pick_survivor` chooses among age-surviving members
only and `duplicate_of` always names a row that was actually retained. Run dedup first and a cluster
whose preferred survivor is ancient leaves its duplicates pointing at an excluded row.

`exclusion_reason='unsupported_language'` HAS NO PRODUCER in v1. English-only is research
assumption 3; inventing a language detector to fill a CHECK value would be a capability claim with
nothing behind it. It is left deliberately unused.

THE PHASE 2 / PHASE 3 LINE
--------------------------
`polarity` is NOT written here and stays NULL. Phase 3 computes it from `sentiment_score` and
`controversy_severity`, both of which this module persists. A pipeline that writes `polarity` is
doing Phase 3's work. The dependency direction is one-way: this module imports
`esg_lens.scoring.weights`; `scoring` never imports `esg_lens.nlp`.

NEVER-RAISE, mirroring `collectors/base.py`. A failure inside any stage logs with `ticker` and
`document_id` bound and yields an excluded row; no exception escapes `process_documents`.
All SQL is parameterised (T-02-12) and all JSON goes through `json.dumps` (T-02-13).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

import structlog

from esg_lens.config import get_scoring_config, load_yaml
from esg_lens.nlp import clean, entity

log = structlog.get_logger(__name__)

# THE DECLARED ORDER. Cheapest and most certain first. A document that trips several gates records
# the FIRST reason in this tuple, whatever order the stages happen to run in.
GATE_PRECEDENCE = (
    "too_old",
    "duplicate",
    "entity_mismatch",
    "not_esg_relevant",
    "low_confidence",
)

REASON_TOO_OLD = "too_old"
REASON_DUPLICATE = "duplicate"
# A stage that raised. The CHECK constraint has no 'error' value, and `not_esg_relevant` would
# assert the model judged the document irrelevant when it never ran — the same choice, for the
# same reason, that 02-02's classify.py made. The NULL relevance is what separates the two.
REASON_STAGE_FAILED = "low_confidence"

_TIMESTAMP_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


def _field(doc: Any, name: str, default: Any = None) -> Any:
    """Read a field from a Mapping, a sqlite3.Row or a plain object, indifferently."""
    if isinstance(doc, Mapping):
        return doc.get(name, default)
    try:
        return doc[name]
    except (TypeError, IndexError, KeyError):
        return getattr(doc, name, default)


@dataclass
class SignalRecord:
    """One document's decision in flight. Becomes exactly one `esg_signals` row."""

    doc: Any
    document_id: Any
    ticker: str
    doc_type: str
    included: bool = True
    exclusion_reason: str | None = None
    duplicate_of: Any = None
    pillar: str | None = None
    category: str | None = None
    relevance: float | None = None
    sentiment_score: float | None = None
    sentiment_label: str | None = None
    controversy_severity: int = 0
    controversy_terms: list[str] = field(default_factory=list)
    weight_source: float | None = None
    weight_recency: float | None = None
    weight_category: float | None = None
    weight_evidence: float | None = None
    weight_total: float | None = None
    entities_json: str | None = None

    @property
    def active(self) -> bool:
        """Still in the working set — no gate has excluded it yet."""
        return self.exclusion_reason is None


@dataclass
class ProcessResult:
    processed: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    included: int = 0
    excluded: int = 0


def _exclude(record: SignalRecord, reason: str) -> None:
    """Record `reason` unless a higher-precedence one is already set. Never overwrites."""
    if record.exclusion_reason is not None:
        return
    if reason not in GATE_PRECEDENCE:
        raise ValueError(f"{reason!r} is not a declared exclusion reason: {GATE_PRECEDENCE}")
    record.exclusion_reason = reason
    record.included = False


# ---------------------------------------------------------------------------
# Stage 3 — age
# ---------------------------------------------------------------------------
def parse_timestamp(value: Any) -> datetime | None:
    """Parse the timestamp shapes the collectors actually store. None when unparseable."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+0000")
    for fmt in _TIMESTAMP_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    return None


def age_days(published_at: Any, now: datetime | None = None) -> float | None:
    """Whole days since publication, floored at 0. None when the timestamp is unusable."""
    parsed = parse_timestamp(published_at)
    if parsed is None:
        return None
    reference = now or datetime.now(timezone.utc)
    return max(0.0, (reference - parsed).total_seconds() / 86400.0)


def _apply_age_gate(records: Sequence[SignalRecord], now: datetime | None = None) -> None:
    max_age = float(get_scoring_config().max_age_days)
    for record in records:
        published = _field(record.doc, "published_at")
        age = age_days(published, now=now)
        if age is None:
            # Treated as age 0, NOT as too_old. A collector that lost a date must not silently
            # delete the document from the evidence base — but it must say so.
            log.warning(
                "published_at_missing",
                ticker=record.ticker,
                document_id=record.document_id,
                published_at=published,
            )
            age = 0.0
        record._age_days = age  # type: ignore[attr-defined]
        if age > max_age:
            _exclude(record, REASON_TOO_OLD)


# ---------------------------------------------------------------------------
# Stage 4 — near-duplicate collapse (D-2-02)
# ---------------------------------------------------------------------------
def _apply_dedup(records: Sequence[SignalRecord]) -> None:
    """Cluster the AGE-SURVIVING documents and mark every non-survivor `duplicate`.

    Only the retained copy proceeds to the entity gate and to inference, so a cluster of twelve
    syndicated re-posts costs one classification, not twelve.
    """
    survivors_in = [r for r in records if r.active]
    if len(survivors_in) < 2:
        return

    by_id = {r.document_id: r for r in survivors_in}
    roots = clean.cluster_near_duplicates([r.doc for r in survivors_in])

    clusters: dict[Any, list[Any]] = {}
    for doc_id, root in roots.items():
        clusters.setdefault(root, []).append(doc_id)

    for members in clusters.values():
        if len(members) < 2:
            continue
        survivor_id = clean.pick_survivor([by_id[m].doc for m in members])
        for member in members:
            if member == survivor_id:
                continue
            duplicate = by_id[member]
            _exclude(duplicate, REASON_DUPLICATE)
            duplicate.duplicate_of = survivor_id


# ---------------------------------------------------------------------------
# Stage 5 — entity gate (NLP-04)
# ---------------------------------------------------------------------------
def _apply_entity_gate(conn: sqlite3.Connection, records: Sequence[SignalRecord]) -> None:
    feeds_cfg = load_yaml("rss_feeds")
    alias_cache: dict[str, list[str]] = {}

    for record in records:
        if not record.active:
            continue
        try:
            if record.ticker not in alias_cache:
                # Once per ticker, never per document.
                alias_cache[record.ticker] = entity.load_aliases(conn, record.ticker)
            decision = entity.gate_decision(record.doc, alias_cache[record.ticker], feeds_cfg)
        except Exception as exc:  # never-raise
            log.error(
                "entity_gate_failed",
                ticker=record.ticker,
                document_id=record.document_id,
                error=str(exc),
            )
            _exclude(record, REASON_STAGE_FAILED)
            continue

        record.entities_json = decision.entities_json
        if not decision.admit:
            _exclude(record, decision.reason or "entity_mismatch")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
INSERT_SQL = """
INSERT OR IGNORE INTO esg_signals (
    document_id, ticker, included, exclusion_reason, duplicate_of,
    pillar, category, relevance,
    sentiment_score, sentiment_label, controversy_severity, controversy_terms,
    weight_source, weight_recency, weight_category, weight_evidence, weight_total,
    entities_json, model_version
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
# `polarity` is absent from that column list on purpose — Phase 3 owns it and it stays NULL.


def _persist(
    conn: sqlite3.Connection, records: Sequence[SignalRecord], model_version: str
) -> int:
    """One parameterised INSERT per document, all inside one transaction (T-02-12)."""
    inserted = 0
    for record in records:
        conn.execute(
            INSERT_SQL,
            (
                record.document_id,
                record.ticker,
                1 if record.included else 0,
                record.exclusion_reason,
                record.duplicate_of,
                record.pillar,
                record.category,
                record.relevance,
                record.sentiment_score,
                record.sentiment_label,
                int(record.controversy_severity or 0),
                json.dumps(record.controversy_terms) if record.controversy_terms else None,
                record.weight_source,
                record.weight_recency,
                record.weight_category,
                record.weight_evidence,
                record.weight_total,
                record.entities_json,
                model_version,
            ),
        )
        inserted += 1
        log.info(
            "document_processed",
            ticker=record.ticker,
            document_id=record.document_id,
            doc_type=record.doc_type,
            included=record.included,
            exclusion_reason=record.exclusion_reason,
            duplicate_of=record.duplicate_of,
            model_version=model_version,
        )
    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def resolve_model_version(model_version: str | None = None) -> str:
    """`config/scoring.yaml`'s `scoring.model_version`.

    It embeds the lexicon version (`lex@2026-09b`). That is a deliberate cache key: a lexicon edit
    changes the string, so the stage-2 no-op stops matching and every document is reprocessed
    (T-02-16). Rows at the old version survive untouched.
    """
    if model_version:
        return model_version
    return str(get_scoring_config().scoring["model_version"])


def _already_processed(conn: sqlite3.Connection, model_version: str) -> set:
    rows = conn.execute(
        "SELECT document_id FROM esg_signals WHERE model_version = ?", (model_version,)
    ).fetchall()
    return {row[0] for row in rows}


def process_documents(
    docs: Iterable[Any],
    conn: sqlite3.Connection,
    *,
    model_version: str | None = None,
    now: datetime | None = None,
) -> ProcessResult:
    """Turn every document in `docs` into exactly one `esg_signals` row. Never raises."""
    version = resolve_model_version(model_version)
    documents = list(docs)
    result = ProcessResult()

    existing = _already_processed(conn, version)
    records = [
        SignalRecord(
            doc=doc,
            document_id=_field(doc, "id"),
            ticker=_field(doc, "ticker") or "",
            doc_type=_field(doc, "doc_type") or "news",
        )
        for doc in documents
        if _field(doc, "id") not in existing
    ]
    result.skipped_existing = len(documents) - len(records)
    result.processed = len(records)
    if not records:
        log.info("nlp_pipeline_noop", model_version=version, documents=len(documents))
        return result

    for stage_name, stage in (
        ("age", lambda: _apply_age_gate(records, now=now)),
        ("dedup", lambda: _apply_dedup(records)),
        ("entity", lambda: _apply_entity_gate(conn, records)),
        ("analyse", lambda: _apply_analysis(conn, records)),
    ):
        try:
            stage()
        except Exception as exc:  # never-raise: a broken stage must not lose the whole batch
            log.error("nlp_stage_failed", stage=stage_name, model_version=version, error=str(exc))
            for record in records:
                if record.active:
                    _exclude(record, REASON_STAGE_FAILED)

    result.inserted = _persist(conn, records, version)
    result.included = sum(1 for r in records if r.included)
    result.excluded = result.processed - result.included
    log.info(
        "nlp_pipeline_complete",
        model_version=version,
        processed=result.processed,
        included=result.included,
        excluded=result.excluded,
        skipped_existing=result.skipped_existing,
    )
    return result


def _apply_analysis(conn: sqlite3.Connection, records: Sequence[SignalRecord]) -> None:
    """Stages 6-8: classification, sentiment, controversy and weights.

    Added by Task 3b. It EXTENDS the single INSERT above rather than adding a second write path.
    """
    return None
