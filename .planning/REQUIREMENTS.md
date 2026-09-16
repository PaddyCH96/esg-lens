# Requirements: ESG Lens — Portfolio Sustainability Analyzer

**Defined:** 2026-09-02
**Core Value:** Every score is fully auditable and reproducible — you can see the exact documents,
weights and formula behind any number, and the system says `insufficient_data` rather than inventing one.

**Provenance:** the ingest set contains **zero PRD-class documents**. These requirements are derived from
SPEC-classified contracts (`api_design.md`, `data_model.md`, `scoring_methodology.md`, `architecture.md`)
and the explicit "Definition of done" blocks in `handoff_to_backend.md` §1. Each has exactly one
acceptance source, so there are **no competing acceptance variants**. The 13 intel requirements in
`.planning/intel/requirements.md` are decomposed below into checkable items; the `source` line on each
category names the intel requirement it expands.

---

## v1 Requirements

### Scaffold & Configuration
*Expands `REQ-scaffold-config` — source: `handoff_to_backend.md` Phase 0; `architecture.md` §4, §7*

- [ ] **SCAF-01**: Package layout per `architecture.md` §4, with `pyproject.toml` and a `.gitignore`
      covering `data/`, `.env`, `__pycache__`
- [ ] **SCAF-02**: `config.py` exposes a `pydantic-settings` `Settings` plus a `ScoringConfig` loader that
      reads `config/scoring.yaml` and exposes `version` and `config_hash` (sha256 of the resolved dict)
- [ ] **SCAF-03**: Three config files populated from the spec tables — `scoring.yaml` (§2 category/pillar
      and sector weights, §5 source tiers), `sources.yaml`, `controversy_lexicon.yaml` (§4 severity tiers)
- [ ] **SCAF-04**: structlog JSON logging configured in exactly one place
- [ ] **SCAF-05**: No magic numbers — every weight, threshold and half-life reads from config, not from a
      `.py` file

### Persistence & Schema
*Expands `REQ-persistence-schema` — source: `data_model.md` (full DDL)*

- [ ] **DATA-01**: `db/schema.sql` transcribed verbatim from `data_model.md`, creating all ten tables
      (`companies`, `company_aliases`, `raw_documents`, `esg_signals`, `esg_scores`,
      `score_contributions`, `jobs`, `job_items`, `collection_runs`, `schema_migrations`) plus the
      `v_latest_scores` view
- [ ] **DATA-02**: Connection factory sets `PRAGMA foreign_keys=ON` **per connection** and
      `journal_mode=WAL` once; `scripts/init_db.py` applies the schema
- [ ] **DATA-03**: Load-bearing constraints enforced — `raw_documents.source` CHECK ∈
      `gdelt|edgar|yfinance|newsapi`; `doc_type` CHECK ∈ `news|filing_section|press_release`;
      `esg_signals` UNIQUE `(document_id, model_version)`; `controversy_severity` BETWEEN 0 AND 3;
      `esg_scores.status` CHECK ∈ `ok|insufficient_data|failed`; `jobs.status` CHECK includes `cancelled`;
      `esg_scores.job_id` ON DELETE SET NULL
- [ ] **DATA-04**: `esg_scores` is append-only — score history is a feature, not a side effect

### Collectors
*Expands `REQ-collectors` — source: `handoff_to_backend.md` Phase 1; `architecture.md` §5.1; `research_notes.md` §2*

- [x] **COLL-01**: Shared `httpx` client with a configurable User-Agent (EDGAR mandates a contact email),
      per-host token-bucket rate limiting from `config/sources.yaml` (EDGAR 10/s, GDELT 0.2/s,
      news.google.com 0.5/s, feeds.finance.yahoo.com 1.0/s), `tenacity` exponential backoff on
      429/5xx, and a `hishel` disk cache at 24h TTL bypassed when `force_refresh=True`
      *(Amended 2026-09-14: GDELT was 1/s; its own 429 body states one request per 5 seconds.)*
- [x] **COLL-02**: `Collector` ABC with `fetch(ticker, since) -> list[RawDocument]`. Collectors **never
      raise** — they catch, log, write a `collection_runs` row, and return `[]`
- [x] **COLL-03**: **RSS news collector** (primary news source) over Google News search RSS
      (queryable, `when:Nd`) and Yahoo per-ticker RSS (entity-guaranteed), parsing the publisher
      domain from `<source url>` (**not** `<link>`, which is a Google redirect), stripping the
      `" - Publisher"` title suffix, parsing RFC-822 `pubDate`, and computing `content_hash`.
      *(Amended 2026-09-14. Originally specified a GDELT DOC 2.1 collector. GDELT's DOC API refused
      this IP for 8+ days while its static host served normally; see
      `.planning/phases/01c-rss-primary/01c-DECISION.md`. The GDELT collector is retained and
      functional behind `ESG_ENABLE_GDELT=1`, but is no longer the primary news source. Measured on
      the same two tickers: GDELT 0 documents in 3m23s, RSS 227 documents in 17s.)*
- [x] **COLL-04**: EDGAR collector resolving ticker→CIK via cached `company_tickers.json` (no scraping),
      pulling recent 10-K/8-K/DEF 14A, section-splitting the 10-K and keeping **only Item 1 and Item 1A**,
      one `raw_documents` row per section
- [~] **COLL-05**: yfinance metadata provider populating `companies` and seeding `company_aliases`
      (legal name, common name, `Inc.`/`Corp.`/`plc` variants); any `sustainability` score is written to
      `external_esg_score` **only**, never used as a scoring input
      *(PARTIAL 2026-09-14: metadata and alias seeding work and are verified. The isolation clause is
      vacuously satisfied — `Ticker.sustainability` returns HTTP 404 for both test tickers, so there
      is no score to isolate. Isolation confirmed by inspection only. Re-verify if Yahoo restores
      the endpoint.)*
- [x] **COLL-06**: NewsAPI collector implemented but shipped with `enabled: false` in config
- [x] **COLL-07**: Deduplication on `content_hash` — re-running a backfill adds zero rows

### NLP Pipeline
*Expands `REQ-nlp-pipeline` — source: `handoff_to_backend.md` Phase 2; `architecture.md` §5.2; `scoring_methodology.md` §1*

- [ ] **NLP-01**: Lazy, process-wide model singletons loaded **once** at FastAPI lifespan startup; no
      model load inside any loop
- [ ] **NLP-02**: Cleaning stage — whitespace/boilerplate normalisation, spaCy sentence split,
      near-duplicate detection
- [x] **NLP-03**: Entity gate using spaCy `en_core_web_sm` NER — accept when an `ORG` entity fuzzy-matches
      an alias or an alias appears as an exact substring of the title; reject with
      `exclusion_reason='entity_mismatch'`
      *(Amended 2026-09-14: the gate is applied PER SOURCE (D-2-03) — `edgar` and any feed whose
      `entity_gate` key in `config/rss_feeds.yaml` is `skip` bypass it, because the provider
      asserts identity. The fuzzy floor is `nlp.entity_match_ratio` = 0.85 via
      `difflib.SequenceMatcher`. A ticker with no usable aliases FAILS OPEN with a
      `gate_skipped_no_aliases` marker and a warning, never silently. See 02-02-SUMMARY.md.)*
- [x] **NLP-04**: ESG classification with `finbert-esg-9-categories`, batched at `batch_size=16`,
      returning `(category, probability)`; `Non-ESG` or `prob < tau (0.60)` is excluded
      *(Amended 2026-09-14: `tau` is now per-doc-type — `news` 0.45, `filing_section` 0.60,
      `press_release` 0.45, resolved from `config/scoring.yaml`. See D-2-01.)*
- [x] **NLP-05**: Sentiment with `ProsusAI/finbert`, batched, returning `P(pos) − P(neg)` — used as one
      feature, never as ESG polarity on its own
- [x] **NLP-06**: Controversy severity 0–3 via spaCy `PhraseMatcher` over lemmas from the YAML lexicon,
      with the negation guard (`cleared of`, `dismissed`, `settled without`, `dropped`) demoting by 1;
      *(Amended 2026-09-14: bare `dropped` removed — it lemmatises to `drop` and negated
      "Stock Drop" headlines; replaced by the constrained multi-token forms in
      `config/controversy_lexicon.yaml` v`lex@2026-09b`. See 02-RESEARCH.md Pitfall 2.)*
      **matched terms are always stored** as the audit trail
- [x] **NLP-07**: Pipeline writes one `esg_signals` row per document including all four persisted weights,
      and skips documents that already have a row for the current `model_version`
      *(Amended 2026-09-14: FIVE persisted weights, not four — `weight_source`, `weight_recency`,
      `weight_category`, `weight_evidence`, `weight_total`, matching `src/esg_lens/db/schema.sql`.
      The "four" was a miscount at authoring time; the schema has carried five since Phase 0.)*
- [x] **NLP-08**: Documents failing any gate are stored with `included=false` and an `exclusion_reason` —
      never silently dropped

### Scoring Engine
*Expands `REQ-scoring-engine` — source: `scoring_methodology.md` §5–§8; `handoff_to_backend.md` Phase 3*

- [ ] **SCOR-01**: `scoring/` contains **pure functions only** — no DB, no network, no I/O
- [ ] **SCOR-02**: Two distinct weights implemented and both persisted per signal:
      `w_ev = w_src·w_rec·w_conf` (gates sufficiency) and `w = w_ev·w_cat` (drives aggregation).
      Gating on `w` is a known bug and must not recur
- [ ] **SCOR-03**: Pillar aggregation per §6.1 — `raw_P = Σ w·pol / Σ w`, `base_P = 50 + 50·raw_P`, with
      the controversy penalty applied **separately**: `pen_P = min(40, Σ k[ctrl]·w_rec·w_src)` for
      `k = {1:1.0, 2:4.0, 3:12.0}`, then `S_P = clamp(base_P − pen_P, 0, 100)`
- [ ] **SCOR-04**: Sufficiency gate — `Σ w_ev < min_evidence (1.0)` yields a null pillar score and
      `insufficient_data`, never a fabricated 50
- [ ] **SCOR-05**: Composite over present pillars using sector weights renormalised to 1.0, plus the
      confidence formula `clamp(0.4·min(1, n_docs/30) + 0.3·min(1, Σw_ev/10) + 0.3·pillar_coverage, 0, 1)`
- [ ] **SCOR-06**: `sector_percentile` computed, and null when fewer than 5 scored peers exist
- [ ] **SCOR-07**: `score_contributions` rows written for the top-k signals by `|contribution|`, where
      `contribution = 50·(w·pol) / Σ(w over the pillar)`; contributions sum to `base_P − 50` and the
      controversy penalty is **never** distributed across them (it is reported on
      `esg_scores.{e,s,g}_penalty`); `rank = 1` is the largest `|contribution|`
- [ ] **SCOR-08**: Documents older than `max_age_days` (default 730) are excluded entirely

### API — Analysis & Jobs
*Expands `REQ-api-portfolio-analyze`, `REQ-api-job-poll` — source: `api_design.md` §1–§2; `architecture.md` §3, §5.4*

- [ ] **API-01**: `POST /api/v1/portfolio/analyze` returns **202 immediately** and never blocks on NLP
- [ ] **API-02**: Request validation — `tickers` 1–25, deduped and uppercased against
      `^[A-Z0-9.\-]{1,10}$`; `force_refresh` default false; `lookback_days` default 365 in range 30–730;
      `sources` a subset of the `raw_documents.source` CHECK
- [ ] **API-03**: `GET /api/v1/portfolio/{job_id}` returns status ∈
      `queued|running|done|partial|failed|cancelled`, a progress block, per-ticker items, and on
      completion a `summary` whose `portfolio_score` is the equal-weighted mean over `status="ok"` scores only

### API — Company Reads
*Expands `REQ-api-company-score`, `REQ-api-supporting` — source: `api_design.md` §3–§4*

- [ ] **API-04**: `GET /api/v1/company/{ticker}/score` returns the **latest stored** score and is strictly
      read-only — it never triggers collection or NLP. Params `include_evidence` (default true),
      `evidence_limit` 0–20 (default 5), `as_of`
- [ ] **API-05**: The score response carries composite, status, confidence, per-pillar
      score/penalty/n_signals/status, renormalised `pillar_weights` with the explanatory note, the
      evidence block with `top_contributors`, the `benchmark` block (marked explicitly as not an input),
      methodology version + `config_hash`, and `is_stale`
- [ ] **API-06**: Supporting reads — `GET /healthz` (503 while models load),
      `GET /company/{ticker}/history?limit=30`, `GET /methodology` (resolved weights + version)
- [ ] **API-07**: `GET /company/{ticker}/documents` joins `raw_documents` → `esg_signals`, filters on the
      signal row whose `model_version` matches the one behind the ticker's latest `esg_scores` row, and
      returns documents with no signal at that version only when no filter is supplied
      (`pillar` and `included` are `esg_signals` columns, not `raw_documents` columns)
- [ ] **API-08**: `DELETE /portfolio/{job_id}` is **not implemented** in v1

### Error Contract
*Expands `REQ-error-contract` — source: `api_design.md` Conventions; `architecture.md` §7*

- [ ] **ERR-01**: All errors are RFC-7807 problem objects
- [ ] **ERR-02**: Typed exception hierarchy (`CollectorError`, `NlpError`, `ScoringError`) mapped by
      FastAPI exception handlers; no business logic in routes
- [ ] **ERR-03**: Status codes 200/202/400/404/409/422/429/500/503 used per the spec table — including
      422 on empty/oversized/malformed ticker lists, 409 with `retry_after_seconds` at
      `max_concurrent_jobs`, 404 on unknown job or never-analysed ticker, and **200 with a null composite**
      for `insufficient_data`
- [ ] **ERR-04**: OpenAPI auto-generated and rendering the full schema at `/docs` and `/openapi.json`

### Jobs Runner
*Expands `REQ-jobs-runner` — source: `architecture.md` §5.4; `handoff_to_backend.md` Phase 4*

- [ ] **JOBS-01**: BackgroundTasks state machine per `architecture.md` §3, with heartbeat updates
- [ ] **JOBS-02**: Module-level `asyncio.Semaphore(1)` around NLP — one job at a time
- [ ] **JOBS-03**: Two startup sweeps — fail jobs stale >1h, and delete jobs older than `retention_days`
      (30), cascading `job_items` while score history survives via `ON DELETE SET NULL`

### Observability & Input Safety
*Expands `REQ-observability-security` — source: `architecture.md` §7*

- [ ] **OBS-01**: structlog JSON to stdout with `job_id` and `ticker` bound to the context
- [ ] **OBS-02**: Ticker regex validation and the 25-ticker portfolio cap enforced at the boundary;
      no secrets in logs
- [ ] **OBS-03**: All SQL exclusively parameterised
- [ ] **OBS-04**: No live network calls anywhere in the test suite — recorded fixtures plus `respx`

### Validation & Transparency
*Expands `REQ-validation-sensitivity` — source: `handoff_to_backend.md` Phase 5; `scoring_methodology.md` §9; `research_notes.md` §4*

- [ ] **VAL-01**: `scripts/sensitivity.py` perturbs each weight ±25% and reports rank stability
- [ ] **VAL-02**: Composites correlated against `external_esg_score` for ~50 tickers, with the number
      **published in the README whatever it turns out to be**
- [ ] **VAL-03**: README limitations section filled in from `research_notes.md` §4 — disclosure bias,
      greenwashing in self-reported filings, rating lag, and the "signal aggregator, not a rating" framing

---

## v2 Requirements

Deferred. Tracked, not in the current roadmap.

### Post-v1 NLP

- **PV1-01**: ClimateBERT `commitment` + `specificity` heads combined into a greenwashing modifier for
  the E pillar (high commitment + low specificity + self-reported source ⇒ discount)
- **PV1-02**: ONNX Runtime inference as a CPU throughput optimisation
- **PV1-03**: Zero-shot fallback enabled for low-confidence documents (`bart-large-mnli`, or the lighter
  `MoritzLaurer/deberta-v3-base-mnli`), still off the hot path

### Post-v1 API & Ops

- **PV1-04**: `DELETE /api/v1/portfolio/{job_id}` job cancellation (`cancelled` already in the CHECK)
- **PV1-05**: A yfinance / `Ticker.news` headline collector — the `yfinance` source enum value exists but
  has no document collector in v1, so selecting it returns zero documents
- **PV1-06**: RSS feeds as a supplementary source — added to the `raw_documents.source` CHECK only
  alongside an actual collector
- **PV1-07**: Dashboard front-end, in a separate repository

---

## Out of Scope

| Feature | Reason |
|---------|--------|
| Frontend / dashboard in this repo | The API is the deliverable; absolute ground rule (D-016). A dashboard is a separate repo |
| Auth / multi-tenancy | Single user on localhost in v1; a header key is addable later without redesign |
| Docker / Kubernetes | Target runtime is a single `uvicorn` process |
| Celery / Redis | One job at a time; BackgroundTasks is sufficient (D-012) |
| Postgres | SQLite is adequate below ~10^5 documents (D-011) |
| SQLAlchemy or any ORM | Raw SQL + repositories; revisit past ~10 tables |
| Full-text article scraping | Legal and reliability cost outweighs the marginal accuracy gain (D-001) |
| Non-English / non-US coverage | Models and source tiering are tuned for US-listed English text |
| Model fine-tuning | No ground-truth ESG dataset exists to fit against |
| Consuming an external ESG score as an input | Would destroy the independence of the signal; stored as a benchmark only (D-007) |
| Real-time streaming | Batch jobs are sufficient for a news-delta refresh cadence |
| Portfolio weighting by position size | Not core to the scoring value proposition |
| Backtesting against returns | Requires market data infrastructure well beyond v1 |

---

## Traceability

Phase numbering follows `docs/handoff_to_backend.md` §1 (Phase 0–5), which is the authoritative build
order and the numbering used in commit messages (`[P0]`..`[P5]`).

| Requirement | Phase | Status |
|-------------|-------|--------|
| SCAF-01 | Phase 0 | Pending |
| SCAF-02 | Phase 0 | Pending |
| SCAF-03 | Phase 0 | Pending |
| SCAF-04 | Phase 0 | Pending |
| SCAF-05 | Phase 0 | Pending |
| DATA-01 | Phase 0 | Pending |
| DATA-02 | Phase 0 | Pending |
| DATA-03 | Phase 0 | Pending |
| DATA-04 | Phase 0 | Pending |
| COLL-01 | Phase 1 | Complete (2026-09-14) |
| COLL-02 | Phase 1 | Complete (2026-09-14) |
| COLL-03 | Phase 1 / 1c | Complete — **amended to RSS**, see 01c-DECISION.md |
| COLL-04 | Phase 1 | Complete (2026-09-14) |
| COLL-05 | Phase 1 | **Partial** — isolation clause vacuous (yfinance 404) |
| COLL-06 | Phase 1 | Complete (2026-09-14) |
| COLL-07 | Phase 1 | Complete (2026-09-14) |
| NLP-01 | Phase 2 | Pending |
| NLP-02 | Phase 2 | Pending |
| NLP-03 | Phase 2 | Complete (02-02) |
| NLP-04 | Phase 2 | Complete (02-02) |
| NLP-05 | Phase 2 | Complete (02-02) |
| NLP-06 | Phase 2 | Complete (02-03) |
| NLP-07 | Phase 2 | Complete (02-03) |
| NLP-08 | Phase 2 | Complete (02-03) |
| SCOR-01 | Phase 3 | Pending |
| SCOR-02 | Phase 3 | Pending |
| SCOR-03 | Phase 3 | Pending |
| SCOR-04 | Phase 3 | Pending |
| SCOR-05 | Phase 3 | Pending |
| SCOR-06 | Phase 3 | Pending |
| SCOR-07 | Phase 3 | Pending |
| SCOR-08 | Phase 3 | Pending |
| API-01 | Phase 4 | Pending |
| API-02 | Phase 4 | Pending |
| API-03 | Phase 4 | Pending |
| API-04 | Phase 4 | Pending |
| API-05 | Phase 4 | Pending |
| API-06 | Phase 4 | Pending |
| API-07 | Phase 4 | Pending |
| API-08 | Phase 4 | Pending |
| ERR-01 | Phase 4 | Pending |
| ERR-02 | Phase 4 | Pending |
| ERR-03 | Phase 4 | Pending |
| ERR-04 | Phase 4 | Pending |
| JOBS-01 | Phase 4 | Pending |
| JOBS-02 | Phase 4 | Pending |
| JOBS-03 | Phase 4 | Pending |
| OBS-01 | Phase 4 | Pending |
| OBS-02 | Phase 4 | Pending |
| OBS-03 | Phase 4 | Pending |
| OBS-04 | Phase 4 | Pending |
| VAL-01 | Phase 5 | Pending |
| VAL-02 | Phase 5 | Pending |
| VAL-03 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 54 total
- Mapped to phases: 54
- Unmapped: 0 ✓

**Intel requirement coverage** (all 13 from `.planning/intel/requirements.md`):

| Intel requirement | Phase | Expanded as |
|---|---|---|
| REQ-scaffold-config | 0 | SCAF-01..05 |
| REQ-persistence-schema | 0 | DATA-01..04 |
| REQ-collectors | 1 | COLL-01..07 |
| REQ-nlp-pipeline | 2 | NLP-01..08 |
| REQ-scoring-engine | 3 | SCOR-01..08 |
| REQ-api-portfolio-analyze | 4 | API-01, API-02 |
| REQ-api-job-poll | 4 | API-03 |
| REQ-api-company-score | 4 | API-04, API-05 |
| REQ-api-supporting | 4 | API-06, API-07, API-08 |
| REQ-error-contract | 4 | ERR-01..04 |
| REQ-jobs-runner | 4 | JOBS-01..03 |
| REQ-observability-security | 4 | OBS-01..04 |
| REQ-validation-sensitivity | 5 | VAL-01..03 |

13/13 intel requirements mapped ✓

---
*Requirements defined: 2026-09-02*
*Last updated: 2026-09-02 after canonical generation from the 7-document ingest*
