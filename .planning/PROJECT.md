# ESG Lens — Portfolio Sustainability Analyzer

## What This Is

A backend service that scores US-listed public companies on Environmental, Social and Governance
signals using only free data sources and open-source NLP models, and exposes the results over a
REST API. It is an open-source portfolio piece demonstrating data engineering, applied NLP and API
design end-to-end — built to be read as much as to be run, so the methodology is transparent by
construction: every score can be traced back to the individual documents and weights that produced it.

It is honestly framed as **a transparent, reproducible ESG signal aggregator**, not "an ESG rating".

## Core Value

Every score is fully auditable and reproducible — you can see the exact documents, weights and
formula behind any number, and the system says `insufficient_data` rather than inventing one.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(None yet — ship to validate)

### Active

Full detail with IDs and acceptance criteria in `.planning/REQUIREMENTS.md`.

- [ ] Scaffold: config-over-constants loader, versioned YAML weights, `config_hash` stamping
- [ ] Persistence: the ten-table SQLite schema, transcribed verbatim from `docs/data_model.md`
- [ ] Collectors: GDELT + EDGAR + yfinance behind one cached, rate-limited, never-raising HTTP layer
- [ ] NLP pipeline: entity gate → ESG classify → sentiment → controversy lexicon → `esg_signals`
- [ ] Scoring engine: pure-function `w_ev`/`w` weighting, pillar aggregation, separate controversy
      penalty, sufficiency gate, composite, confidence, sector percentile
- [ ] API: portfolio analyze (202 + job), job poll, company score with evidence, supporting reads
- [ ] Jobs: BackgroundTasks state machine, single-NLP semaphore, startup sweeps
- [ ] Error contract: RFC-7807 problem objects across a typed exception hierarchy
- [ ] Observability & input safety: structlog JSON, ticker regex, portfolio cap, parameterised SQL
- [ ] Validation: ±25% weight sensitivity, benchmark correlation, published limitations

### Out of Scope

Sources: `docs/handoff_to_backend.md` §3, `docs/architecture.md` §8 (constraint C-010).

- **Frontend / dashboard** — separate repo later; the API is the deliverable (D-016, absolute ground rule)
- **Auth / multi-tenancy** — single user on localhost in v1; a header key is addable later without redesign
- **Docker / K8s** — target runtime is `uvicorn src.esg_lens.api.main:app` and nothing else
- **Celery / Redis** — one job at a time; FastAPI BackgroundTasks is sufficient (D-012)
- **Postgres** — SQLite is adequate below ~10^5 documents (D-011)
- **Full-text article scraping** — legal and reliability cost outweighs the marginal accuracy gain (D-001)
- **Non-English / non-US coverage** — models and source tiering are tuned for US-listed English text
- **Model fine-tuning** — no ground-truth ESG dataset exists to fit against
- **Real-time streaming, portfolio weighting by position size, backtesting against returns** — not core
- **`DELETE /api/v1/portfolio/{job_id}`** — deferred past v1; `cancelled` stays in the `jobs.status`
  CHECK so adding it later needs no migration (D-013)
- **ClimateBERT greenwashing heads; ONNX Runtime inference** — explicitly post-v1, not a v1 phase (D-018)

## Context

**Why the honest framing matters.** Commercial ESG raters (MSCI, Sustainalytics, Refinitiv, S&P)
correlate only ~0.4–0.6 with each other — versus ~0.99 for credit ratings (Berg, Kölbel & Rigobon,
*Aggregate Confusion*, MIT). The consequence is adopted as a design constraint: do not claim a "true"
ESG score; ship a transparent one with every input auditable. Known biases are disclosed publicly:
disclosure bias (large caps disclose more and therefore score higher — mitigated by exposing coverage
and confidence), greenwashing in self-reported filings, and rating lag (news-driven refresh is the
actual differentiator).

**Approach family.** Of the three families — disclosure-based, incident/controversy-based, and
quantitative-metric-based — ESG Lens leans **incident/controversy-based** (the RepRisk model): highest
signal, lowest bias. Baked-in literature consensus: controversies dominate positive PR; sentiment alone
is not ESG (a relevance gate first is the #1 failure mode of naive builds); recency decay is
near-universal; source-quality weighting matters, with a static domain tier map as a cheap proxy.

**Data sources.** GDELT DOC 2.1 is the news backbone (key-less, 15-min cadence, titles + metadata only,
noisy entity matching). SEC EDGAR supplies 10-K Item 1/1A for E and S, and DEF 14A proxies — the single
best free G source, without which G scoring is nearly empty. yfinance is an unofficial scraper with no
SLA, wrapped behind a `MetadataProvider` interface and used for metadata plus the benchmark score only.
NewsAPI is implemented but flagged off: ~24h delay and commercial use prohibited.

**Models.** FinBERT-ESG (`yiyanghkust/finbert-esg-9-categories`) is a *sentence-level* classifier trained
on ~2k sentences — feed it headlines, treat softmax as a ranking not a probability, and let its `Non-ESG`
class serve as the relevance gate. `ProsusAI/finbert` predicts sentiment *from an investor's viewpoint*,
which is emphatically not ESG polarity: "Company settles pollution lawsuit for less than expected" is
financially positive and environmentally negative. Controversy detection is therefore lexicon-based, in
versioned YAML, because auditability is the product premise.

**Implementer.** The build is carried out by an OpenCode agent running on **free models**. Cheap models
are excellent at filling a well-specified shape and bad at deciding what the shape should be — which is
why these planning documents exist and why each phase carries an explicit definition of done. Routing
guidance (`docs/opencode_model_routing.md`) is authoring process, not product: green work is scaffold,
schema transcription, pydantic models, repo CRUD, routes, YAML and scoring unit tests; yellow is
collectors, HTTP concurrency, 10-K sectioning and NLP batching; red — spend the best model available —
is the scoring engine and the job state machine.

**Traps already identified** (`docs/handoff_to_backend.md` §4): loading transformers per document
(100× penalty); installing torch without the CPU index URL (~2.5 GB of unused CUDA); omitting the EDGAR
User-Agent (IP block); treating FinBERT sentiment as ESG polarity (invalidates the score); gating
sufficiency on `w` instead of `w_ev` (a real bug in an earlier methodology draft); averaging away
controversies; emitting 50 for no data; forgetting `PRAGMA foreign_keys` is per-connection and defaults
OFF; putting `bart-large-mnli` on the hot path.

**Assumptions to challenge rather than code around:** single user / localhost / no auth; portfolio ≤ 25
tickers; ~10 s of NLP per ticker acceptable; US-listed English-language only; headline-level analysis
sufficient; SQLite adequate to ~10^5 docs; GDELT stays free and key-less; EDGAR stays free with a UA
header; yfinance is best-effort and may return nothing; weights are expert priors with no ground truth.

## Constraints

- **Budget**: Free tools and models only — no paid APIs, no GPU, no managed cloud services. This is a
  hard constraint on every technology choice, not a preference.
- **Tech stack**: Python 3.11+, CPU-only torch, FastAPI + SQLite with raw SQL and repositories (no ORM),
  single process, localhost. `uvicorn src.esg_lens.api.main:app` and nothing else.
- **Performance**: ~10 s of NLP per company for ~50 headlines on a laptop CPU — therefore analysis MUST
  be an async job, never a blocking request. spaCy sm ~5 ms/doc; FinBERT ~40 ms/sentence;
  `bart-large-mnli` ~1–3 s/doc, off by default. ~16 GB RAM if Ollama is co-resident. (C-009)
- **Algorithmic contract**: the formulas in `docs/scoring_methodology.md` §3/§5/§6 are a specification,
  not a suggestion. `w_ev = w_src·w_rec·w_conf` gates sufficiency; `w = w_ev·w_cat` drives aggregation;
  both persisted per signal. Ambiguity gets raised, never silently "improved". (C-001, C-008)
- **Schema**: `db/schema.sql` is transcribed verbatim from `docs/data_model.md`; any schema change
  requires the doc updated in the same commit. All CHECK constraints and the `esg_signals`
  `UNIQUE (document_id, model_version)` are load-bearing. (C-008)
- **API contract**: base path `/api/v1`, JSON, no auth in v1, RFC-7807 errors, OpenAPI at `/docs`.
  The `sources` enum must stay byte-identical to the `raw_documents.source` CHECK. (C-007)
- **Protocol**: EDGAR requires ≤10 req/s *and* a User-Agent containing a contact email — omission means
  an IP block. GDELT ≤1 req/s with undocumented soft limits. 24h disk cache; 7d metadata TTL. (C-006)
- **Testing**: no live network calls in tests — recorded fixtures plus `respx`. Collectors tested against
  fixtures; NLP tested with stub models asserting wiring not accuracy; scoring tested exhaustively
  against hand-computed values including the §8 worked example. (C-011)
- **Transparency (product-level)**: confidence must render next to every score; null must never render
  as 0; `sector_percentile` is the only cross-company comparison field and is null below 5 scored peers;
  sensitivity analysis is required before publishing. (C-012)
- **Implementer capability**: each phase must fit one OpenCode session on a free model and carry an
  explicit, mechanically checkable definition of done.

## Key Decisions

**Status of this table:** the ingest set contains **zero ADR-class documents**, so there are
**no LOCKED decisions**. All 18 entries below are *decision candidates* extracted from
SPEC- and DOC-classified sources (`.planning/intel/decisions.md`, D-001..D-018). They are strong
defaults, not immutable constraints — a future `/gsd:decide` may promote any of them.

> **Recommended next action** (from `INGEST-CONFLICTS.md` I-001 and I-012): promote **D-008**
> (the `w_ev` / `w` two-weight split) and **D-009** (`insufficient_data` is mandatory, never a
> fabricated 50) to real ADRs. The source text already treats both as inviolable, and W-010 shows how
> easily an authoritative formula can drift out of sync with its own prose.

| # | Decision (candidate) | Rationale | Outcome |
|---|----------------------|-----------|---------|
| D-001 | Score titles + GDELT metadata only; no full-text scraping in v1 | Legal + reliability cost, marginal accuracy gain | — Pending |
| D-002 | `yiyanghkust/finbert-esg-9-categories` as primary ESG classifier | Its `Non-ESG` class *is* the relevance gate | — Pending |
| D-003 | `ProsusAI/finbert` for sentiment, as one feature only | Financial sentiment is not ESG polarity | — Pending |
| D-004 | Zero-shot `bart-large-mnli` off the hot path, disabled by default | ~1–3 s/doc vs ~40 ms for FinBERT | — Pending |
| D-005 | spaCy `en_core_web_sm`; not `_trf` | CPU budget; roles are NER, sentence split, PhraseMatcher | — Pending |
| D-006 | Controversy detection is lexicon-based, not model-based | Auditability is the product premise; lexicon in versioned YAML | — Pending |
| D-007 | Never consume an external ESG score as an input | Stored in a separate column as a validation benchmark only | — Pending |
| D-008 | Two weights: `w_ev` gates sufficiency, `w` drives aggregation | Category weight belongs in aggregation, not in the evidence question | — Pending (promote to ADR) |
| D-009 | `insufficient_data` is a first-class state, never a fabricated 50 | Inventing a number destroys the core value | — Pending (promote to ADR) |
| D-010 | Sector-dependent pillar weights (SASB materiality) | Composites are not cross-sector comparable; `sector_percentile` is | — Pending |
| D-011 | SQLite + raw SQL + repositories; no ORM, no Postgres in v1 | Rejected Postgres (ops burden), SQLAlchemy (revisit past ~10 tables) | — Pending |
| D-012 | FastAPI BackgroundTasks for jobs; no Celery/Redis | Narrow interface: `enqueue`/`get`/`update`, `asyncio.Semaphore(1)` | — Pending |
| D-013 | `DELETE /api/v1/portfolio/{job_id}` deferred past v1 | `cancelled` retained in the CHECK so no migration is needed later | — Pending |
| D-014 | Job retention 30 days via a startup sweep | Cascades `job_items`; `esg_scores.job_id` ON DELETE SET NULL preserves history | — Pending |
| D-015 | Config over constants; all weights in versioned YAML | `config_hash` + `methodology_version` stamped on every score row | — Pending |
| D-016 | Backend only; no frontend in this repo | Stated as an absolute ground rule | — Pending |
| D-017 | NewsAPI implemented but feature-flagged off; GDELT carries v1 | Free tier prohibits commercial use and delays articles ~24h | — Pending |
| D-018 | ClimateBERT greenwashing signals adopted post-v1 | Not in the v1 critical path; covers only a slice of E | — Pending |

## Known Documentation Issue

**None outstanding.** `INGEST-CONFLICTS.md` W-010 (the `scoring_methodology.md` §3 prose reading
"damped by 0.4x" against its own `pol(d) = 0.6 * sent(d)` formula) was **fixed before this file was
generated** — the prose now reads "scaled by 0.6 — a 40% damping". Both the synthesizer and the
roadmapper read a pre-fix copy of the file and reported it as outstanding; `grep -rn "damped by 0.4"
docs/` returns zero hits. The Phase 3 acceptance target stands at `S_E = 20.7`.

---
*Last updated: 2026-09-02 after canonical generation from the 7-document ingest (mode: new-project-from-ingest)*
