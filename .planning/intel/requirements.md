# Requirements — ESG Lens

**Note on provenance:** zero PRD-classified docs in the ingest set. Requirements below are
derived from SPEC-classified contracts (api_design.md, scoring_methodology.md, data_model.md)
and the DOC-classified build plan (handoff_to_backend.md). Acceptance criteria are taken from
explicit "Definition of done" blocks where they exist, and from contract text otherwise.

Because there is only one source per requirement, there are **no competing acceptance variants**.

---

## REQ-scaffold-config
- source: docs/handoff_to_backend.md Phase 0; docs/architecture.md §4, §7
- Package layout, `pydantic-settings` Settings + `ScoringConfig` loader exposing `version` and
  `config_hash`; three populated YAML config files; `schema.sql` copied verbatim from data_model.md;
  connection factory setting `PRAGMA foreign_keys=ON` per connection and `journal_mode=WAL` once;
  structlog JSON logging.
- Acceptance: `python scripts/init_db.py` creates `data/esg_lens.db` with all tables; `pytest` green.

## REQ-collectors
- source: docs/handoff_to_backend.md Phase 1; docs/architecture.md §5.1; docs/research_notes.md §2
- Shared `httpx` client with configurable User-Agent (EDGAR mandates contact email), per-host
  token-bucket rate limiting (EDGAR 10/s, GDELT 1/s), tenacity backoff on 429/5xx, hishel disk
  cache 24h TTL bypassed on `force_refresh=True`. `Collector` ABC: `fetch(ticker, since) -> list[RawDocument]`.
- Collectors NEVER raise: catch, log, write a `collection_runs` row, return `[]`.
- GDELT DOC 2.1 `mode=artlist&format=json`; EDGAR `company_tickers.json` → CIK, 10-K/8-K/DEF 14A,
  section-split 10-K keeping Item 1 and Item 1A only; yfinance metadata → companies + aliases.
- Acceptance: `scripts/backfill.py --tickers AAPL,XOM` populates `raw_documents`; re-running adds
  zero rows (dedup on `content_hash`); integration tests pass against recorded fixtures.

## REQ-nlp-pipeline
- source: docs/handoff_to_backend.md Phase 2; docs/architecture.md §5.2; docs/scoring_methodology.md §1
- Lazy process-wide model singletons loaded once at FastAPI lifespan startup; batched inference
  (`batch_size=16`); entity gate (spaCy NER + alias fuzzy/substring match); FinBERT-ESG classify
  with `tau = 0.60`; FinBERT sentiment `P(pos) − P(neg)`; PhraseMatcher controversy severity 0–3
  with negation guard, always storing matched terms; skip docs already having a row for the
  current `model_version`.
- Documents failing gates are stored with `included=false` + `exclusion_reason`, never dropped.
- Acceptance: 50 fixture headlines → signals in <10 s on CPU; gates unit-tested with stub models.

## REQ-scoring-engine
- source: docs/scoring_methodology.md §5–§8; docs/handoff_to_backend.md Phase 3
- Pure functions only, no I/O in `scoring/`. Implements w_ev/w split, pillar aggregation with
  separate controversy penalty (k = {1:1.0, 2:4.0, 3:12.0}, pen_cap 40), sufficiency gate
  `Σ w_ev < min_evidence (1.0)` → null + `insufficient_data`, composite over present pillars with
  renormalised sector weights, confidence formula, sector percentile (null below 5 scored peers).
- Writes `score_contributions` rows for top-k signals by `|contribution|`, where
  `contribution = 50 * (w * pol) / Σ(w over the pillar)`. Contributions must sum to `base_P - 50`;
  the controversy penalty is reported separately on `esg_scores.{e,s,g}_penalty`, never spread
  across contributions.
- Acceptance (THE methodology acceptance gate, write test before implementation): reproduces the
  §8 worked example — `S_E = 20.7`, S and G `insufficient_data`, `composite = 20.7`,
  `confidence = 0.19` — within 0.1 points (0.01 for confidence).

## REQ-api-portfolio-analyze
- source: docs/api_design.md §1
- `POST /api/v1/portfolio/analyze`, returns 202 immediately, never blocks on NLP.
- `tickers` 1–25 deduped/uppercased against `^[A-Z0-9.\-]{1,10}$`; `force_refresh` default false;
  `lookback_days` default 365 range 30–730; `sources` subset of the `raw_documents.source` CHECK.
- Acceptance: 422 on empty/>25/malformed; 409 with `retry_after_seconds` at `max_concurrent_jobs`.

## REQ-api-job-poll
- source: docs/api_design.md §2; docs/architecture.md §3, §5.4
- `GET /api/v1/portfolio/{job_id}` returns status ∈ queued|running|done|partial|failed|cancelled,
  progress block, per-ticker items, and on completion a `summary` with equal-weighted
  `portfolio_score` over `status="ok"` scores only.
- Acceptance: 404 on unknown job; `partial` state exercised; jobs retained 30 days.

## REQ-api-company-score
- source: docs/api_design.md §3
- `GET /api/v1/company/{ticker}/score` returns the LATEST STORED score; read-only, never triggers
  collection or NLP. Params `include_evidence` (default true), `evidence_limit` 0–20 (default 5), `as_of`.
- Response carries composite, status, confidence, per-pillar score/penalty/n_signals/status,
  renormalised `pillar_weights` + note, evidence block with `top_contributors`, `benchmark`
  (external score, explicitly not an input), methodology version + config_hash, `is_stale`.
- Acceptance: `status: insufficient_data` returns 200 with null composite and evidence intact;
  404 only when the ticker was never analysed.

## REQ-api-supporting
- source: docs/api_design.md §4
- `GET /healthz` (503 while models load), `GET /company/{ticker}/history?limit=30`,
  `GET /company/{ticker}/documents` (joins raw_documents → esg_signals, filters on the signal row
  whose `model_version` matches the latest `esg_scores` row; unfiltered requests also return
  documents with no signal at that version), `GET /methodology` (resolved weights + version).
- `DELETE /portfolio/{job_id}` is explicitly NOT implemented in v1.

## REQ-error-contract
- source: docs/api_design.md Conventions; docs/architecture.md §7
- RFC-7807 problem objects for all errors; typed exception hierarchy (`CollectorError`, `NlpError`,
  `ScoringError`) mapped by FastAPI exception handlers. Status table: 200/202/400/404/409/422/429/500/503.
- Acceptance: OpenAPI auto-generated at `/docs` and `/openapi.json` renders the full schema.

## REQ-persistence-schema
- source: docs/data_model.md (full DDL)
- Tables: companies, company_aliases, raw_documents, esg_signals, esg_scores, score_contributions,
  jobs, job_items, collection_runs, schema_migrations, plus `v_latest_scores`.
- `esg_scores` append-only (history is a feature); `esg_signals` unique on
  `(document_id, model_version)`; `raw_documents.source` CHECK ∈ gdelt|edgar|yfinance|newsapi.
- Acceptance: schema.sql matches data_model.md verbatim; schema changes require the doc updated in
  the same commit.

## REQ-jobs-runner
- source: docs/architecture.md §5.4; docs/handoff_to_backend.md Phase 4
- BackgroundTasks state machine, `asyncio.Semaphore(1)` around NLP, heartbeat updates, and two
  startup sweeps: fail jobs stale >1h, delete jobs older than `retention_days` (30).
- Acceptance: end-to-end `POST /portfolio/analyze` → poll → `GET /company/{ticker}/score` for
  `["AAPL","XOM"]`.

## REQ-validation-sensitivity
- source: docs/handoff_to_backend.md Phase 5; docs/scoring_methodology.md §9; docs/research_notes.md §4
- `scripts/sensitivity.py` perturbs each weight ±25% and reports rank stability.
- Correlate composites against `external_esg_score` for ~50 tickers and publish the number in the
  README whatever it is; fill the README limitations section from research_notes.md §4.
- Acceptance: sensitivity results and correlation both present in the README before publishing.

## REQ-observability-security
- source: docs/architecture.md §7
- structlog JSON to stdout with `job_id`/`ticker` bound; ticker regex validation; portfolio cap 25;
  no secrets in logs; SQL exclusively parameterised; no live network calls in tests (respx).
