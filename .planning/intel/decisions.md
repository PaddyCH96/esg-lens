# Decisions — ESG Lens

No formally structured ADRs exist in the ingest set (0 docs classified ADR). The entries below
are **decision candidates** extracted from SPEC- and DOC-classified sources. None are LOCKED.
Downstream (`gsd-roadmapper`) should treat these as strong defaults, not immutable constraints.

---

## D-001 — Score on titles + GDELT metadata only (no full-text scraping) in v1
- source: docs/research_notes.md §2.3
- status: candidate (stated as "v1 decision")
- Full-text fetching deferred: legal + reliability cost, marginal accuracy gain.
- Reinforced by: docs/handoff_to_backend.md §2 (assumption 4), §3 (out of scope); docs/architecture.md §8.

## D-002 — Adopt `yiyanghkust/finbert-esg-9-categories` as the primary ESG classifier
- source: docs/research_notes.md §3.1, §3.6
- status: candidate (bolded "Decision:")
- Its `Non-ESG` class IS the relevance gate (gate 2 in scoring_methodology.md §1).
- Treat softmax as a ranking, not a calibrated probability (small training set).

## D-003 — Adopt `ProsusAI/finbert` for sentiment, but only as one feature
- source: docs/research_notes.md §3.2
- status: candidate
- Financial sentiment is NOT ESG polarity. ESG direction comes from combining it with the
  controversy lexicon (scoring_methodology.md §3).

## D-004 — Zero-shot `bart-large-mnli` off the hot path, disabled by default
- source: docs/research_notes.md §3.4; docs/handoff_to_backend.md §4
- status: candidate
- Config gate `nlp.zero_shot.enabled = false`. ~1–3 s/doc vs ~40 ms for FinBERT.
- Permitted uses: offline silver-label generation; low-confidence fallback.

## D-005 — Adopt spaCy `en_core_web_sm`; do not use `_trf`
- source: docs/research_notes.md §3.5, §3.6
- status: candidate
- Roles: entity disambiguation, sentence split, PhraseMatcher controversy lexicon.

## D-006 — Controversy detection is lexicon-based, not model-based
- source: docs/research_notes.md §3.5; docs/scoring_methodology.md §4
- status: candidate
- Rationale: auditability is the product premise. Lexicon lives in versioned YAML, not code.

## D-007 — Never consume an external ESG score as an input
- source: docs/research_notes.md §2.1; docs/api_design.md §3 (`benchmark` block);
  docs/data_model.md (`external_esg_score` column); docs/handoff_to_backend.md Phase 1
- status: candidate (consistently asserted across four docs)
- yfinance/Sustainalytics score stored as validation benchmark only, in a separate column.

## D-008 — Two distinct weights: `w_ev` gates sufficiency, `w` drives aggregation
- source: docs/scoring_methodology.md §5, §6.1; docs/data_model.md (esg_signals, esg_scores);
  docs/handoff_to_backend.md Phase 3, §4
- status: candidate (methodology-critical; treat as effectively locked)
- `w_ev = w_src·w_rec·w_conf`; `w = w_ev·w_cat`. Both persisted per signal row.
- Gating on `w` is explicitly called out as a prior bug.

## D-009 — `insufficient_data` is a required first-class state, never a fabricated 50
- source: docs/scoring_methodology.md §6.1; docs/api_design.md §3; docs/data_model.md (esg_scores.status)
- status: candidate (hard rule in source text)

## D-010 — Sector-dependent pillar weights (SASB materiality principle)
- source: docs/scoring_methodology.md §2
- status: candidate
- Composite scores are NOT cross-sector comparable; `sector_percentile` is the comparison field.

## D-011 — SQLite + raw SQL + repositories; no ORM, no Postgres in v1
- source: docs/architecture.md §6 (technology choice table with rejected alternatives)
- status: candidate (ADR-shaped reasoning, no ADR structure)
- Rejected: Postgres (ops burden), SQLAlchemy ORM (revisit past ~10 tables).

## D-012 — FastAPI + BackgroundTasks for async jobs; no Celery/Redis in v1
- source: docs/architecture.md §5.4, §6; docs/handoff_to_backend.md §3
- status: candidate
- Job interface deliberately narrow: `enqueue`, `get`, `update`. **No `cancel` in v1.**
- Concurrency: module-level `asyncio.Semaphore(1)` around NLP.

## D-013 — `DELETE /api/v1/portfolio/{job_id}` deferred past v1
- source: docs/api_design.md §4; docs/architecture.md §5.4; docs/handoff_to_backend.md Phase 4
- status: candidate (consistent across three docs)
- `cancelled` retained in the `jobs.status` CHECK so adding it later needs no migration.

## D-014 — Job retention: 30 days, enforced by a startup sweep
- source: docs/architecture.md §5.4 (owner); docs/api_design.md §2; docs/handoff_to_backend.md Phase 4
- status: candidate
- Cascades `job_items`; `esg_scores.job_id` is `ON DELETE SET NULL` so score history survives.

## D-015 — Config over constants; all weights in versioned YAML
- source: docs/handoff_to_backend.md §0.4; docs/architecture.md §5.3, §6, §7
- status: candidate
- `config_hash` (sha256 of resolved config) + `methodology_version` stamped on every score row.

## D-016 — Backend only; no frontend in this repo
- source: docs/handoff_to_backend.md §0.1, §3; docs/architecture.md §8
- status: candidate (stated as an absolute ground rule)

## D-017 — NewsAPI implemented but feature-flagged off; GDELT carries v1
- source: docs/research_notes.md §2.4, §2.5; docs/architecture.md §2; docs/handoff_to_backend.md Phase 1
- status: candidate
- NewsAPI free-tier licence prohibits commercial use and delays articles ~24h.

## D-018 — ClimateBERT greenwashing signals: adopt post-v1, not in the v1 critical path
- source: docs/research_notes.md §3.3, §3.6
- status: candidate (unambiguous — labelled "post-v1", explicitly disclaimed as NOT a
  handoff_to_backend.md phase number)
- `commitment` + `specificity` heads together form the greenwashing modifier for the E pillar.
- Companion post-v1 item: ONNX Runtime inference (docs/architecture.md §6), same labelling.
