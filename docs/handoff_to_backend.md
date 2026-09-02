# Handoff to Backend Developer (OpenCode)

**Read first, in this order:** `architecture.md` → `data_model.md` → `scoring_methodology.md`
→ `api_design.md`. This document is the build order.

---

## 0. Ground rules

1. **Do not build a frontend.** No React, no templates, no HTML, no CSS, no static files.
   The API is the deliverable. A dashboard is a separate repo later.
2. **Do not invent scoring logic.** `scoring_methodology.md` is the specification. If a formula
   there is wrong or ambiguous, *raise it* — do not silently improve it.
3. **Do not change the DB schema** without updating `docs/data_model.md` in the same commit.
4. **Config over constants.** Every weight, threshold and half-life reads from
   `config/scoring.yaml`. A magic number in a `.py` file is a bug.
5. **No live network calls in tests.** Record fixtures; use `respx` to mock `httpx`.
6. **Commit atomically**, one logical change per commit, with the phase number in the message
   (e.g. `feat(collectors): add GDELT client [P1]`).

---

## 1. Build order

### Phase 0 — Scaffold  *(do this first, do not skip)*
- `pyproject.toml`, package layout per `architecture.md` §4, `.gitignore` (`data/`, `.env`, `__pycache__`).
- `src/esg_lens/config.py` — `pydantic-settings` `Settings` + a `ScoringConfig` loader that
  reads `config/scoring.yaml` and exposes `version` and `config_hash` (sha256 of the resolved dict).
- `config/scoring.yaml`, `config/sources.yaml`, `config/controversy_lexicon.yaml` populated with
  the tables from `scoring_methodology.md` §2, §4, §5.
- `src/esg_lens/db/schema.sql` — copy the DDL from `data_model.md` verbatim.
- `db/engine.py` — connection factory that sets `PRAGMA foreign_keys=ON` **per connection** and
  `journal_mode=WAL` once; `scripts/init_db.py` applies the schema.
- `structlog` JSON logging configured in one place.
- **Definition of done:** `python scripts/init_db.py` creates `data/esg_lens.db` with all tables,
  and `pytest` runs green on an empty suite.

### Phase 1 — Collectors
- `collectors/http.py` first: one shared `httpx.Client` with a configurable `User-Agent`
  (**EDGAR mandates a contact email in it**), per-host token-bucket rate limiting
  (EDGAR 10/s, GDELT 1/s), `tenacity` retry with exponential backoff on 429/5xx, and a
  `hishel` disk cache with a 24 h TTL, bypassed when `force_refresh=True`.
- `collectors/base.py` — `Collector` ABC, `fetch(ticker, since) -> list[RawDocument]`.
  **Collectors never raise**: catch, log, record a `collection_runs` row, return `[]`.
- `yfinance_meta.py` — company metadata → `companies` + seed `company_aliases`
  (legal name, common name, and simple variants: drop `Inc.`/`Corp.`/`plc`).
  If `Ticker.sustainability` returns a score, write it to `external_esg_score` **only**.
- `gdelt.py` — DOC 2.1 API, `mode=artlist&format=json`. Query = alias OR-group + ESG keyword
  bundle. Normalise `domain`, parse `seendate`, compute `content_hash`.
- `edgar.py` — `company_tickers.json` → CIK (cache it); submissions API → recent 10-K/8-K/DEF 14A;
  fetch the primary document; **section-split the 10-K into Items and keep only Item 1 and
  Item 1A**; each section becomes one `raw_documents` row with `filing_section` set.
  Chunk long sections to ≤512 tokens' worth of sentences downstream, not here.
- `newsapi.py` — implement, but default `enabled: false` in config.
- **Definition of done:** `scripts/backfill.py --tickers AAPL,XOM` populates `raw_documents`;
  re-running it adds zero new rows (dedup works); integration tests pass against fixtures.

### Phase 2 — NLP pipeline
- `nlp/registry.py` — lazy, process-wide singletons. Models load **once**, at FastAPI `lifespan`
  startup. Loading a model inside a loop is the failure mode to avoid.
- `nlp/clean.py` — whitespace/boilerplate normalisation, spaCy sentence split, near-duplicate
  detection.
- `nlp/entity.py` — `EntityGate`: spaCy `en_core_web_sm` NER; accept if any `ORG` entity fuzzy-matches
  an alias, or an alias appears as an exact substring of the title. Reject → `exclusion_reason='entity_mismatch'`.
- `nlp/classify.py` — `yiyanghkust/finbert-esg-9-categories`, **batched** (`batch_size=16`).
  Return `(category, probability)`. `Non-ESG` or `prob < tau` → excluded.
- `nlp/sentiment.py` — `ProsusAI/finbert`, batched. Return `P(pos) - P(neg)`.
- `nlp/controversy.py` — spaCy `PhraseMatcher` over lemmas from `controversy_lexicon.yaml`,
  plus the negation guard. Return `(severity, matched_terms)` — **always store the matched terms**;
  they are the audit trail.
- `nlp/pipeline.py` — document → `esg_signals` row, including all four persisted weights.
  Skip documents already having a row for the current `model_version`.
- **Definition of done:** given 50 fixture headlines, produces signals in <10 s on CPU, and the
  gates are unit-tested with stub models (test wiring, not model accuracy).

### Phase 3 — Scoring engine
- **Pure functions only.** No DB, no network, no I/O inside `scoring/`.
- `weights.py`, `aggregate.py`, `composite.py` implementing `scoring_methodology.md` §5–6.
- Write `score_contributions` rows for the top-k signals by `|contribution|`.
- **Two weights, not one.** `w_ev = w_src·w_rec·w_conf` gates sufficiency; `w = w_ev·w_cat`
  drives aggregation. Persist both. Gating on `w` is wrong — see `scoring_methodology.md` §6.1.
- **Definition of done:** a unit test reproduces the worked example in
  `scoring_methodology.md` §8 — `S_E = 20.7`, G and S `insufficient_data`, `composite = 20.7`,
  `confidence = 0.19` — to within 0.1 points (0.01 for confidence). This test is the acceptance
  gate for the whole methodology — write it before the implementation.

### Phase 4 — API + jobs
- `jobs/runner.py` — the state machine from `architecture.md` §3, `asyncio.Semaphore(1)` around
  NLP, heartbeat updates, and a startup sweep failing jobs stale >1 h.
- `api/main.py` — lifespan: run migrations, warm models, sweep stale jobs.
- Routes exactly per `api_design.md`. RFC-7807 exception handlers. **No business logic in routes.**
- Startup does two sweeps: fail jobs stale >1 h, and delete jobs older than `retention_days` (30).
- **Do not implement `DELETE /portfolio/{job_id}`** — deferred past v1, see `api_design.md` §4.
- **Definition of done:** `POST /portfolio/analyze` → poll → `GET /company/{ticker}/score`
  works end-to-end for `["AAPL","XOM"]`, and `/docs` renders the full schema.

### Phase 5 — Validation
- `scripts/sensitivity.py` — perturb each weight ±25%, report rank stability.
- Correlate composites against `external_esg_score` for ~50 tickers; **publish the number in
  the README whatever it is.**
- Fill in the README limitations section from `research_notes.md` §4.

---

## 2. Assumptions baked into these documents
Challenge any of these that turn out to be wrong — do not code around them silently.

1. Single user, localhost, no auth needed in v1.
2. Portfolio ≤ 25 tickers; ~10 s of NLP per ticker on CPU is acceptable.
3. US-listed, English-language companies only.
4. Headline-level news analysis (no article-body scraping) is sufficient for v1.
5. SQLite is adequate; total corpus stays under ~10⁵ documents.
6. GDELT remains free and key-less; EDGAR remains free with a UA header.
7. Yahoo Finance via `yfinance` is best-effort and may return nothing — the system degrades.
8. Weights are expert priors; there is no ground-truth dataset to fit them to.
9. Python 3.11+, CPU-only torch.

---

## 3. Explicitly out of scope for v1
Frontend · auth/multi-tenancy · Docker/K8s · Celery/Redis · Postgres · real-time streaming ·
full-text article scraping · non-English/non-US coverage · model fine-tuning · portfolio
weighting by position size · backtesting against returns.

---

## 4. Traps worth flagging up front
- **Loading transformers per document.** Load once at startup. This is a 100× difference.
- **Installing torch without the CPU index URL.** Pulls ~2.5 GB of CUDA nobody will use.
- **Skipping the EDGAR `User-Agent`.** SEC will block the IP.
- **Treating FinBERT sentiment as ESG polarity.** See `research_notes.md` §3.2 — this
  invalidates the whole score if you get it wrong.
- **Gating sufficiency on `w` instead of `w_ev`.** Category weight belongs in the aggregation,
  not in the question of whether enough evidence exists. This was a real bug in an earlier draft
  of the methodology.
- **Averaging away controversies.** The separate penalty term in §6.1 exists precisely because
  a weighted mean lets PR volume bury one severe incident.
- **Emitting 50 for no data.** `insufficient_data` is a required state, not an edge case.
- **`PRAGMA foreign_keys` is per-connection** in SQLite and defaults to OFF.
- **`bart-large-mnli` on the hot path.** Off by default. It is ~30× slower than FinBERT.
