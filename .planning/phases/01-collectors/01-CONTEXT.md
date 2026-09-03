# Phase 1: Collectors - Context

**Gathered:** 2026-09-03
**Status:** Ready for planning

## Phase Boundary

Phase 1 makes free public data flow into `raw_documents` reliably and repeatably: GDELT DOC 2.1 news (titles + metadata), SEC EDGAR filings (10-K Item 1 + Item 1A, 8-K, DEF 14A as filing_section rows), and yfinance metadata + `company_aliases` seeding. Success is `scripts/backfill.py --tickers AAPL,XOM` populates `raw_documents`, re-running adds zero rows (content_hash dedup), failures are logged to `collection_runs` and never raise, EDGAR carries a UA with contact email at ≤10/s and GDELT ≤1/s, yfinance sustainability scores land only in `external_esg_score`.

Out of scope for this phase: NLP pipeline, scoring engine, API/jobs, frontend/dashboard, full article-body scraping, NewsAPI enabled use, model fine-tuning.

## Implementation Decisions

### Query construction — GDELT alias OR-group + ESG bundle
- **D-01: Broad bundle chosen.** Use categories + controversy lexicon tiers 1–3 as the ESG keyword bundle (~30 terms: Climate Change / Natural Capital / Pollution & Waste / Human Capital / Product Liability / Community Relations / Corporate Governance / Business Ethics & Values plus triggers like oil spill, bribery, fraud, child labor, fatality, class action, criminal probe, fine, penalty, lawsuit, recall, strike, investigation, data breach, layoffs, criticized, alleged, scrutiny, protest, complaint, downgrade, etc.). Rationale: maximize recall for incident/controversy-based scoring; category-only misses severe controversies that dominate pillar penalties.
- **D-02: Filter short/ambiguous aliases.** Drop aliases ≤4 chars or on stoplist; otherwise use quoted phrases for multi-word aliases. Short names like "Apple", "Meta", "Shell" are noisy on GDELT; filtering at query time reduces fruit/generic hits before the spaCy entity gate. All raw aliases remain in `company_aliases` table — filtering applies only to query construction.
- **D-03: Quoted phrases + chunk if needed.** Quote multi-word terms ("oil spill", "Apple Inc", "child labor"). If constructed query exceeds ~400 chars (GDELT's undocumented ~500-char limit), split into 2 sequential queries chunked on the bundle and merge results (dedup downstream on `content_hash`). Log a warning when truncation/chunking occurs; never silently truncate without log.

### Claude's Discretion
All other collector scope is governed by `docs/handoff_to_backend.md: Phase 1` and `REQUIREMENTS.md: COLL-01..07` and is left to standard approaches:
- Shared `httpx` client details: per-host token buckets (EDGAR 10/s, GDELT 1/s), `tenacity` exponential backoff on 429/5xx, `hishel` disk cache 24h TTL with `force_refresh` bypass, configurable `User-Agent` containing contact email.
- GDELT DOC 2.1 `mode=artlist&format=json` specifics: `seendate` parsing, domain normalization (lowercase, strip www.), `content_hash = sha256(lower(normalized_title) | external_id/url)`, `hishel` cache key = request URL.
- EDGAR details: cached `company_tickers.json` → CIK (7d TTL), submissions API → recent 10-K/8-K/DEF 14A, 10-K section split keeping only Item 1 and Item 1A, each section one `raw_documents` row with `filing_section` set, chunking for NLP deferred to Phase 2.
- yfinance metadata: `Ticker.info` + `Ticker.sustainability` handling with graceful `None` degradation; alias variants (legal, common, brand, drop Inc./Corp./plc) seeded into `company_aliases`; sustainability score written to `external_esg_score` only, never used as scoring input.
- NewsAPI: implemented but `enabled: false` in `config/sources.yaml`.
- Failure contract: collectors never raise — catch, log via `structlog`, write `collection_runs` row, return `[]`.
- Deduplication purely on `content_hash` UNIQUE (ticker, content_hash).

No folded todos (todo.match-phase 1 = 0).

## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Collectors & build order (authoritative)
- `docs/handoff_to_backend.md: §1 Phase 1` — ground rules, build order, Definition of done (6 criteria), traps (UA, w vs w_ev, pen_cap), assumptions
- `docs/architecture.md: §5.1` — Collector ABC, HttpClient contract, never-raise rule, source enum sync
- `docs/data_model.md` — DDL for `companies`, `company_aliases`, `raw_documents` (CHECK source/doc_type, UNIQUE content_hash), `collection_runs`, `jobs`/`job_items` FK semantics

### Scoring / weighting (for what collectors must persist for)
- `docs/scoring_methodology.md: §2` — category→pillar map + w_cat, pillar_weights per sector
- `docs/scoring_methodology.md: §5` — w_src tier map, w_rec half-lives (90d news / 365d filing), max_age_days 730, confidence tau 0.60
- `docs/scoring_methodology.md: §4` — controversy lexicon tiers 0–3 + negation guard (feeds GDELT bundle per D-01)

### Data-source & model evaluation
- `docs/research_notes.md: §2` — GDELT DOC 2.1 free/key-less/15-min/noisy need for entity gate, EDGAR as best free G source, yfinance unofficial scraper wrapped via MetadataProvider, NewsAPI free-tier delay/prohibition
- `docs/api_design.md: §1` — `sources` enum must stay byte-identical to `raw_documents.source` CHECK; ticker regex `^[A-Z0-9.\-]{1,10}$`
- `docs/opencode_model_routing.md: §2` — Phase 1 is yellow (strong free Zen or Gemini), share HttpClient first, real API quirks

### Existing code (Phase 0)
- `src/esg_lens/config.py` — Settings (USER_AGENT must contain @) + ScoringConfig (version/config_hash), sources.yaml/lexicon loaders
- `src/esg_lens/db/engine.py` — WAL + per-connection foreign_keys=ON, `init_db()` idempotent
- `src/esg_lens/collectors/http.py` (partial) — current simple per-host lock to be replaced by token bucket + hishel + tenacity
- `src/esg_lens/collectors/gdelt.py`, `edgar.py`, `yfinance_meta.py` (partials) — early stubs to reuse, not authoritative over docs
- `config/sources.yaml`, `config/scoring.yaml`, `config/controversy_lexicon.yaml` — versioned weights/tiers feeding collectors

## Existing Code Insights

### Reusable Assets
- `src/esg_lens/db/engine.py:get_connection()` — already sets WAL + foreign_keys, used by backfill and tests (`tests/conftest.py:db_conn` pattern)
- `src/esg_lens/config.py:settings` / `ScoringConfig` — provides USER_AGENT, CACHE_DIR, TORCH_THREADS, RETENTION_DAYS; scoring hash stamping pattern for future collection_runs metadata
- `src/esg_lens/db/repositories.py:CompanyRepo`/`DocumentRepo` — minimal raw-SQL helpers to extend for upsert/insert-or-ignore on content_hash
- `data/esg_lens.db` + `src/esg_lens/db/schema.sql` — 10 tables + view verified verbatim, ready for collectors integration tests

### Established Patterns
- Raw SQL + repositories (no ORM) per D-011 — keep SQL parameterised, DDL is docs/data_model.md verbatim
- Config over constants per D-015 — HttpClient reads rate limits/caching/TTLs from `config/sources.yaml`, not hard-coded
- JSON logging via `src/esg_lens/logging.py` (structlog) — collectors log with `ticker`/`source`/`job_id` context, used for collection_runs error field

### Integration Points
- `raw_documents` UNIQUE(ticker, content_hash) → Phase 2 NLP reads via `document_id` + `model_version` UNIQUE, Phase 3 scoring reads `esg_signals.weight_evidence`/`weight_total`
- `companies` + `company_aliases` (ticker→alias, alias→ticker) → GDELT query expansion and spaCy entity gate (Phase 2)
- `collection_runs` (ticker, source, job_id, status, n_fetched/n_new, window, error, duration_ms) → observability, never-raise proof, job runner progress

## Specific Ideas

- User explicitly chose broad ESG bundle (D-01) because controversy penalties dominate pillar scores — wants incident recall over precision at collection time.
- Short alias filtering at query time (D-02) — user is aware of "Apple the fruit" noise; prefers filtering before GDELT call, keeping entity gate as second line rather than sole filter.
- Chunking strategy (D-03) preferred over silent truncation — wants auditability when query would exceed GDELT limit, expects log warning.

## Deferred Ideas

None — discussion stayed within Phase 1 scope. All deferred items remain in `ROADMAP.md: Post-v1 / Out of Scope` (dashboard separate repo, ClimateBERT, ONNX, DELETE cancellation, yfinance news + RSS collectors).

---

*Phase: 1-Collectors*
*Context gathered: 2026-09-03 via discuss-phase*
