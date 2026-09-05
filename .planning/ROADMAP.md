# Roadmap: ESG Lens — Portfolio Sustainability Analyzer

## Overview

The build runs bottom-up along the data's own path: first a scaffold that makes config and schema
authoritative, then collectors that fill `raw_documents` from free sources, then an NLP pipeline that
turns documents into weighted `esg_signals`, then a pure-function scoring engine that turns signals into
auditable `esg_scores`, then the API and job runner that make all of it reachable, and finally the
validation work that lets the repository be published honestly. Each phase ends in a state that can be
checked mechanically, so a free-model implementer can be told "done" or "not done" without judgement.

**Phase numbering follows `docs/handoff_to_backend.md` §1 (Phase 0–5)** rather than restarting at 1.
That numbering is load-bearing: it is the scheme used in commit messages (`feat(collectors): ... [P1]`),
in every "Definition of done" block, and in `docs/opencode_model_routing.md`'s model-routing table. The
older hand-written roadmap's "Phase 6 — Dashboard" is *not* carried forward as a phase; a dashboard is
explicitly a separate repository and out of scope for v1 (D-016, C-010), so it appears under
**Post-v1 / Out of Scope** below instead of masquerading as unfinished v1 work.

**Session sizing.** Each phase is sized to fit one OpenCode session on a free model. Phase 4 is the one
exception — it carries seven intel requirements — so it is explicitly decomposed into five plans, each of
which is the one-session unit. The `Plans` lists below preserve that property.

## Phases

**Phase Numbering:**
- Integer phases (0, 1, 2, ...): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [ ] **Phase 0: Scaffold** - Config loader, three populated YAML files, verbatim schema, JSON logging
- [ ] **Phase 1: Collectors** - GDELT + EDGAR + yfinance behind one cached, rate-limited, never-raising HTTP layer
- [ ] **Phase 2: NLP Pipeline** - Entity gate → ESG classify → sentiment → controversy lexicon → `esg_signals`
- [ ] **Phase 3: Scoring Engine** - Pure-function weighting, aggregation, penalty, gate, composite, confidence
- [ ] **Phase 4: API & Jobs** - FastAPI routes, RFC-7807 errors, background job state machine, startup sweeps
- [ ] **Phase 5: Validation & Publication** - Sensitivity analysis, benchmark correlation, published limitations

## Phase Details

### Phase 0: Scaffold
**Goal**: The repository is a working, configurable, schema-backed Python package — every weight lives in
versioned YAML and every table exists before a single line of domain logic is written.
**Depends on**: Nothing (first phase)
**Requirements**: SCAF-01, SCAF-02, SCAF-03, SCAF-04, SCAF-05, DATA-01, DATA-02, DATA-03, DATA-04
**Success Criteria** (what must be TRUE):
  1. `python scripts/init_db.py` creates `data/esg_lens.db` containing all ten tables and the
     `v_latest_scores` view, with `PRAGMA foreign_keys` ON per connection and WAL journal mode set
  2. `pytest` runs green on the (empty) suite
  3. Loading `ScoringConfig` returns a `version` and a stable `config_hash`, and the resolved sector,
     category and source-tier weight sets each sum to 1.00
  4. A grep for hardcoded weights and thresholds in `src/` finds none — they all resolve from
     `config/scoring.yaml`, `config/sources.yaml` and `config/controversy_lexicon.yaml`
  5. `db/schema.sql` diffs clean against the DDL in `docs/data_model.md`
**Plans**: 2 plans

Plans:
- [ ] 00-01: Package layout, `pyproject.toml`, CPU-only torch pin, `pydantic-settings` Settings +
      `ScoringConfig` loader with `config_hash`, three populated YAML configs, structlog JSON setup
- [ ] 00-02: `schema.sql` transcribed verbatim, connection factory with per-connection
      `PRAGMA foreign_keys=ON` and one-time WAL, `scripts/init_db.py`, schema round-trip test

### Phase 1: Collectors
**Goal**: Free public data flows into `raw_documents` reliably and repeatably — rate-limited, cached,
deduplicated, and never able to crash the process.
**Depends on**: Phase 0
**Requirements**: COLL-01, COLL-02, COLL-03, COLL-04, COLL-05, COLL-06, COLL-07
**Success Criteria** (what must be TRUE):
  1. `scripts/backfill.py --tickers AAPL,XOM` populates `raw_documents` with GDELT news and EDGAR
     filing sections, and populates `companies` plus `company_aliases` from yfinance metadata
  2. Re-running the same backfill adds **zero** new rows — `content_hash` deduplication works
  3. A forced collector failure produces a `collection_runs` row and an empty list, never an exception
     escaping to the caller
  4. Integration tests pass against recorded fixtures with `respx`, making no live network calls
  5. Every outbound EDGAR request carries a User-Agent containing a contact email, and observed request
     rates stay within 10/s for EDGAR and 1/s for GDELT
  6. Any yfinance sustainability score lands in `external_esg_score` and nowhere else
**Plans**: 3 plans

Plans:
- [ ] 01-01: Shared `httpx` client — configurable UA, per-host token buckets, `tenacity` backoff on
      429/5xx, `hishel` 24h disk cache with `force_refresh` bypass; `Collector` ABC with the
      never-raise contract and `collection_runs` logging
- [ ] 01-02: GDELT DOC 2.1 collector (alias OR-group + ESG keyword bundle, domain normalisation,
      `seendate` parsing, `content_hash`) and the flagged-off NewsAPI collector
- [ ] 01-03: EDGAR collector (cached `company_tickers.json` → CIK, 10-K/8-K/DEF 14A, 10-K section split
      keeping Item 1 and Item 1A only), yfinance metadata + alias seeding, `scripts/backfill.py`

### Phase 2: NLP Pipeline
**Goal**: Every collected document becomes a weighted, auditable `esg_signals` row — or an explicitly
excluded one with a stated reason — fast enough to run on a laptop CPU.
**Depends on**: Phase 1
**Requirements**: NLP-01, NLP-02, NLP-03, NLP-04, NLP-05, NLP-06, NLP-07, NLP-08
**Success Criteria** (what must be TRUE):
  1. 50 fixture headlines produce `esg_signals` rows in **under 10 seconds** on CPU
  2. Models load exactly once per process — a test asserts the loader is not called inside the batch loop
  3. Each gate is unit-tested with stub models, asserting wiring rather than model accuracy: entity
     mismatch, `Non-ESG`, and `prob < 0.60` each produce the correct `exclusion_reason`
  4. Rejected documents are still persisted with `included=false` and an `exclusion_reason` — nothing is
     silently dropped
  5. Every controversy hit stores its matched lexicon terms, and a negated phrase ("cleared of ...")
     scores exactly one severity tier lower
  6. Re-running the pipeline over already-processed documents at the same `model_version` is a no-op
**Plans**: 3 plans

Plans:
- [ ] 02-01: `nlp/registry.py` lazy process-wide singletons wired to FastAPI lifespan; `nlp/clean.py`
      normalisation, spaCy sentence split, near-duplicate detection
- [ ] 02-02: `nlp/entity.py` entity gate (NER + alias fuzzy/substring), `nlp/classify.py` batched
      FinBERT-ESG with `tau=0.60`, `nlp/sentiment.py` batched `P(pos) − P(neg)`
- [ ] 02-03: `nlp/controversy.py` PhraseMatcher over lemmas with the negation guard and matched-term
      storage; `nlp/pipeline.py` writing `esg_signals` with all four weights and `model_version` skip logic

### Phase 3: Scoring Engine
**Goal**: Signals become defensible pillar and composite scores that reproduce the published worked
example exactly — and honestly refuse to score when the evidence is too thin.
**Depends on**: Phase 2
**Requirements**: SCOR-01, SCOR-02, SCOR-03, SCOR-04, SCOR-05, SCOR-06, SCOR-07, SCOR-08
**Success Criteria** (what must be TRUE):
  1. **The methodology acceptance gate**: a unit test reproduces the `scoring_methodology.md` §8 worked
     example — `S_E = 20.7`, S and G `insufficient_data`, `composite = 20.7`, `confidence = 0.19` — within
     0.1 points (0.01 for confidence). This test is written *before* the implementation
  2. Sufficiency is gated on `Σ w_ev`, never on `Σ w`; a test pins the §8 G pillar at
     `Σ w_ev = 0.625 < 1.0` → null, and would fail if the gate used `w`
  3. A pillar with no qualifying evidence returns null with status `insufficient_data` — a test asserts
     that 50 is never emitted for absent data
  4. `score_contributions` for a pillar sum to `base_P − 50`, and the controversy penalty appears only on
     `esg_scores.{e,s,g}_penalty`, never spread across contributions
  5. `scoring/` imports nothing that performs I/O — the whole module is pure functions
  6. `sector_percentile` is null when fewer than 5 scored peers exist
**Plans**: 3 plans

Plans:
- [ ] 03-01: **Write the §8 acceptance test first**, plus hand-computed unit tests for `pol`, `w_rec`,
      `w_conf`, `w_ev` and `w` — this fixes the contract before any implementation exists
- [ ] 03-02: `weights.py` and `aggregate.py` — the two-weight split, `raw_P`/`base_P`, the separate
      controversy penalty with `pen_cap 40`, the `min_evidence` sufficiency gate, `max_age_days` exclusion
- [ ] 03-03: `composite.py` — sector-weight renormalisation over present pillars, confidence formula,
      sector percentile, and `score_contributions` ranking by `|contribution|`

**Not blocked.** W-010 (the §3 prose/formula drift) was fixed before this roadmap was generated;
the prose reads "scaled by 0.6 — a 40% damping". The acceptance fixture is pinned at `S_E = 20.7`,
`composite = 20.7`, `confidence = 0.19`, with G and S `insufficient_data`. Write that test first.

### Phase 4: API & Jobs
**Goal**: The whole pipeline is reachable over a documented HTTP contract, with long-running analysis
safely backgrounded and every score reachable read-only with its evidence attached.
**Depends on**: Phase 3
**Requirements**: API-01, API-02, API-03, API-04, API-05, API-06, API-07, API-08, ERR-01, ERR-02, ERR-03, ERR-04, JOBS-01, JOBS-02, JOBS-03, OBS-01, OBS-02, OBS-03, OBS-04
**Success Criteria** (what must be TRUE):
  1. End-to-end: `POST /api/v1/portfolio/analyze` with `["AAPL","XOM"]` returns 202 immediately, polling
     `GET /api/v1/portfolio/{job_id}` shows progress through to `done`, and
     `GET /api/v1/company/AAPL/score` then returns a stored score with its evidence
  2. `/docs` and `/openapi.json` render the complete schema for every route
  3. A ticker with insufficient evidence returns **200 with a null composite** and its evidence intact —
     never a 404, never a fabricated number; 404 is reserved for tickers never analysed
  4. Error paths return RFC-7807 problem objects: 422 on empty / >25 / malformed ticker lists, 409 with
     `retry_after_seconds` at `max_concurrent_jobs`, 404 on unknown job id
  5. `GET /company/{ticker}/score` performs no collection and no NLP — a test asserts zero collector and
     model invocations during the request
  6. Restarting the app fails jobs stale over an hour and deletes jobs older than 30 days, while the
     corresponding `esg_scores` rows survive with `job_id` set to NULL
  7. Logs are JSON with `job_id` and `ticker` bound, and the full test suite runs with no live network
**Plans**: 5 plans

Plans:
- [ ] 04-01: `jobs/runner.py` — the `architecture.md` §3 state machine, `asyncio.Semaphore(1)` around NLP,
      heartbeat updates, `enqueue`/`get`/`update` only (no `cancel`)
- [ ] 04-02: `api/main.py` lifespan (migrations, model warm-up, both startup sweeps), structlog context
      binding, `GET /healthz` returning 503 while models load
- [ ] 04-03: RFC-7807 problem-object handlers over the typed exception hierarchy, request models with the
      ticker regex and 25-ticker cap, the full status-code table
- [ ] 04-04: `POST /portfolio/analyze` (202) and `GET /portfolio/{job_id}` with progress, per-ticker items
      and the `status="ok"`-only portfolio summary
- [ ] 04-05: `GET /company/{ticker}/score` with the full evidence, benchmark and methodology blocks, plus
      `/history`, `/documents` (model-version-matched join) and `/methodology`

### Phase 5: Validation & Publication
**Goal**: The repository can be published honestly — the weights are shown to be stable, the score is
compared against an independent benchmark, and the limitations are stated in the README rather than buried.
**Depends on**: Phase 4
**Requirements**: VAL-01, VAL-02, VAL-03
**Success Criteria** (what must be TRUE):
  1. `scripts/sensitivity.py` perturbs each weight by ±25% and reports rank stability, with the result
     present in the README
  2. The correlation between ESG Lens composites and `external_esg_score` across ~50 tickers is published
     in the README **whatever the number is**
  3. The README limitations section states disclosure bias, greenwashing in self-reported filings and
     rating lag, and frames the product as a transparent signal aggregator rather than an ESG rating
  4. Confidence is rendered next to every score in every documented example, and no example renders a
     null score as 0
**Plans**: 2 plans

Plans:
- [ ] 05-01: `scripts/sensitivity.py` — ±25% per-weight perturbation and rank-stability reporting
- [ ] 05-02: ~50-ticker benchmark correlation run, README results section and limitations section

## Post-v1 / Out of Scope

Deliberately *not* v1 phases. Recorded here so they are not mistaken for unfinished work.

| Item | Why not v1 | Source |
|---|---|---|
| Dashboard front-end | Separate repository; "do not build a frontend" is an absolute ground rule | D-016, handoff §0.1 |
| ClimateBERT `commitment`/`specificity` greenwashing modifier | Covers only a slice of E; explicitly labelled post-v1 | D-018, research_notes §3.3 |
| ONNX Runtime inference | A documented post-v1 optimisation, not a v1 scoring-engine concern | architecture §6 |
| Job cancellation (`DELETE /portfolio/{job_id}`) | Deferred; `cancelled` already in the CHECK so no migration is needed | D-013 |
| yfinance / `Ticker.news` headline collector | The enum value is reserved but has no v1 collector — selecting it returns zero documents | INGEST-CONFLICTS I-011 |
| RSS collector | Add to the `source` CHECK only alongside a real collector | INGEST-CONFLICTS I-009 |

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| GDELT entity matching too noisy ("Apple" the fruit) | High | High | Entity gate with spaCy NER + alias table; measure the false-positive rate during Phase 2 |
| yfinance breaks (unofficial, undocumented endpoint) | High | Medium | `MetadataProvider` interface, 7d cache, graceful `None` degradation; metadata is off the critical path |
| Prose/formula drift silently changes a scoring constant | Low (W-010 fixed) | High | The §8 fixture pins every constant; the Phase 3 test is written before the implementation |
| CPU NLP too slow for 25 tickers | Medium | Medium | Batched inference, async jobs, `bart-large-mnli` off by default; ONNX as a post-v1 escape hatch |
| Weights produce nonsense scores | Medium | High | §8 worked-example test as the acceptance gate, plus Phase 5 sensitivity analysis |
| Free model quotas exhausted mid-build | Medium | Low | Multiple configured providers + Ollama fallback; spend the best model on Phase 3 and Phase 4's runner |
| SEC blocks the IP | Low | High | Mandatory User-Agent with contact email; 10 req/s token bucket |

## Progress

**Execution Order:**
Phases execute in numeric order: 0 → 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 0. Scaffold | 2/2 | ✅ Complete | 2026-09-03 (`d8902dc`) |
| 1. Collectors | 3/3 | 🟡 Executed — UAT 1/7 | code 2026-09-03 (`854236c`) |
| 2. NLP Pipeline | 0/3 | Not started | - |
| 3. Scoring Engine | 0/3 | Not started (acceptance test written) | - |
| 4. API & Jobs | 0/5 | Not started | - |
| 5. Validation & Publication | 0/2 | Not started | - |

**Overall: 5/18 plans (28%).** Verified against the codebase on 2026-09-05.

Notes:
- Phase 0 was executed outside the GSD plan flow, so there is no `.planning/phases/00-scaffold/`
  directory and no PLAN/SUMMARY artifacts for it. The code and tests exist and pass.
- Phase 1 is **code complete but not verified**. 6 of 7 UAT tests are pending in
  `.planning/phases/01-collectors/01-UAT.md`. Do not mark the phase done until they close.
- Phase 3's acceptance fixture (`tests/unit/test_scoring_fixture.py`) is written and skipping.
  It activates automatically the moment `src/esg_lens/scoring/` becomes importable, which is the
  intended TDD gate from `docs/handoff_to_backend.md` Phase 3.
