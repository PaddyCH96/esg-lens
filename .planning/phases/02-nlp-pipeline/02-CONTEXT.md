# Phase 2: NLP Pipeline — Context

**Created:** 2026-09-14
**Phase:** 2 of 5 · NLP Pipeline
**Requirements:** NLP-01..NLP-08

## Phase Boundary

Every `raw_documents` row becomes a weighted, auditable `esg_signals` row — or an explicitly
excluded one with a stated reason — fast enough to run on a laptop CPU.

**In scope:** model registry, cleaning, entity gate, ESG classification, sentiment, controversy
detection, pipeline orchestration writing `esg_signals`.
**Not in scope:** scoring/aggregation (Phase 3), API (Phase 4), any new data source.

## Empirical grounding

Measured on the **real 242-document corpus** (227 RSS + 15 EDGAR), Python 3.12 venv, CPU,
`torch.set_num_threads(4)` — not on invented fixtures:

    warm model load (cached)   1.8s    once per process
    spaCy NER      50 docs     0.17s   (3 ms/doc)
    FinBERT-ESG    50 docs     1.51s   (30 ms/doc)
    FinBERT-sent   50 docs     0.58s   (12 ms/doc)
    TOTAL          50 docs     2.26s   <- success criterion is <10s; 4.4x headroom
    242-doc corpus projected  ~11s

The <10s criterion is comfortably reachable. Performance is **not** a risk in this phase; the
open questions are all about correctness on real text.

## Implementation Decisions

### D-2-01: Per-doc-type relevance threshold
`tau_news = 0.45`, `tau_filing_section = 0.60`. Both in `config/scoring.yaml` per D-015.

**Evidence:** at a uniform `tau=0.60`, 8 of 50 real headlines fell below threshold, two of them
genuine ESG lawsuits scoring 0.45 and 0.48.

> ⚠ **Do not quote those two titles as fixture landmarks.** They came from the AAPL corpus as it
> stood on 2026-09-13. That corpus was destroyed by a smoke test and re-collected on 2026-09-14,
> and RSS returns different articles each time — `Lawsuit Against Apple To Remain In State Court`
> no longer exists in `data/esg_lens.db` (verified: 0 rows). The *measurement* stands; the
> *specific titles* do not. Any fixture landmark must be re-verified against the live DB, which
> is exactly why 02-01's export script asserts each landmark is present.

**Rationale:** 0.60 was chosen in `scoring_methodology.md` §5 for the register FinBERT-ESG was
trained on — corporate-report *sentences*. Headlines are shorter and out-of-domain, so softmax
confidence runs systematically lower for the same true relevance. Lowering it globally would
weaken filing-section classification, where 0.60 is appropriate because long-form relevant text
genuinely does score higher.

**Note for Phase 3/5:** this makes `tau` a tuning parameter with a real precision/recall
trade-off. It belongs in the ±25% sensitivity analysis (`scripts/sensitivity.py`).

### D-2-02: Near-duplicates collapsed in cleaning, best kept
Detect near-duplicate documents **per ticker** by normalised-title similarity. Keep the one with
the highest source-credibility tier; store the others with `included=false` and
`exclusion_reason='duplicate'` — a value `data_model.md` already defines.

**Evidence:** 3.1% of RSS rows (7/227) are the same story syndicated under slightly different
titles with distinct `content_hash` values. The Tata Electronics breach appears 3 times.

**Rationale:** fix it once, upstream. `content_hash` dedup is exact-match and cannot catch these
by design. Left alone, duplicates inflate **both** the weighted score **and** `Σ w_ev`, so a
syndicated story would also push a pillar over the `min_evidence` sufficiency gate — two defects,
not one. Collapsing in `clean.py` keeps the audit trail intact (nothing is deleted) and means
Phase 3 sees one signal per real-world event.

**Left to the planner:** the similarity measure and threshold. Must be deterministic and cheap —
no model inference. Record the retained document's id on the excluded rows if practical.

### D-2-03: Per-source entity-gate trust
Skip the entity gate for feeds where the provider asserts the ticker; apply the full spaCy NER +
alias match to everything else.

| Source | Gate | Why |
|---|---|---|
| `yahoo_ticker` RSS | **skip** | Yahoo tags the feed to the ticker itself |
| EDGAR filings | **skip** | Filed *by* the company; identity is definitional |
| `google_news` RSS | **apply** | Query-matched only; "Apple the fruit" is possible |

Trust is a **per-feed property declared in `config/rss_feeds.yaml`**, not hard-coded in the gate.

**Rationale:** Phase 1 treated the entity gate as "a second line rather than sole filter" because
GDELT queries carried a filtered alias OR-group (D-02). The RSS switch killed that — one alias
drives each query — so the gate became primary for Google News. But applying it uniformly would
reject Yahoo documents whose headlines never name the company ("Q3 earnings beat expectations"),
discarding real signal to a check that cannot add information about identity it already has.

### D-2-04: Filing sections — sentence-split, classify, aggregate to one signal
spaCy splits the section into sentences; FinBERT-ESG classifies them batched; the section produces
**one** `esg_signals` row. Category = dominant ESG label across relevant sentences.
Relevance = mean confidence over ESG-relevant sentences only (not over all sentences, which would
let boilerplate drag a genuinely relevant section below `tau`).

**Rationale:** `data_model.md` keys `esg_signals` to `document_id`, and one filing section is one
document — emitting several rows per section would break that contract and double-count in
Phase 3. Item 1A buries material ESG content well past the boilerplate opening, so a first-N-
sentences cut would systematically miss the reason filings are worth reading. Cost is
comfortable: 15 sections against a 4.4x performance headroom.

**Left to the planner:** sentence cap per section (guard against a pathological 10-K), and how
sentiment/controversy aggregate across sentences — controversy should almost certainly take the
**max** severity, not the mean, consistent with `scoring_methodology.md` §6.1 treating
controversies as non-averageable.

## Claude's Discretion

Governed by `docs/handoff_to_backend.md: Phase 2` and `REQUIREMENTS.md: NLP-01..08`:
- Lazy process-wide singletons in `nlp/registry.py`; models load once at FastAPI `lifespan`, never
  inside a batch loop. Warm load is 1.8s — worth enforcing with a test.
- Batched inference (`batch_size=16` from config), `device=-1`, `torch.set_num_threads` from settings.
- `HF_HOME=data/models` so weights stay inside the project (gitignored).
- Controversy via spaCy `PhraseMatcher` over lemmas from `config/controversy_lexicon.yaml` with the
  negation guard; **always persist matched terms** — they are the audit trail.
- Every signal row records `model_version`; re-running at the same version is a no-op.
- Excluded documents are always stored with an `exclusion_reason`, never silently dropped.
- Never-raise contract, structlog JSON with `ticker`/`document_id` context (Phase 1 pattern).

## Canonical References

- `docs/handoff_to_backend.md` — Phase 2 build order + traps
- `docs/architecture.md` §5.2 — NLP component contract
- `docs/scoring_methodology.md` §3, §5, §6.1 — polarity, weights, controversy non-averageability
- `docs/data_model.md` — `esg_signals` DDL, `exclusion_reason` enum, UNIQUE(document_id, model_version)
- `docs/research_notes.md` §3 — model evaluation and the financial-vs-ESG sentiment caveat
- `.planning/phases/01c-rss-primary/01c-DECISION.md` — why the corpus looks the way it does
- `.planning/phases/01-collectors/01-VERIFICATION.md` — findings R-1, R-2 feeding D-2-02
- `config/scoring.yaml`, `config/controversy_lexicon.yaml`, `config/rss_feeds.yaml`

## Existing Code Insights

**Reusable:** `db/engine.py:get_connection()` (WAL + FK pragmas), `config.py:settings` /
`ScoringConfig` (version + `config_hash` stamping), `collectors/base.py` never-raise +
`content_hash` pattern, `tests/conftest.py` in-memory-DB fixture.

**⚠ Existing `nlp/` code is NOT a head start.** `clean.py` (48 lines) and `registry.py` (43) come
from the pre-GSD boilerplate commit `778b961`. **No test imports `esg_lens.nlp`** — zero coverage.
`registry.py` uses `from src.esg_lens.config import settings`, the only file in the codebase with
that import style; it does not resolve under `PYTHONPATH=src`. Treat both as scaffolding to
replace, not extend.

**Integration points:** reads `raw_documents` (+ `companies`/`company_aliases` for the gate);
writes `esg_signals` including `weight_evidence` and `weight_total` — Phase 3 reads exactly those.

## Specific Ideas

- The user consistently chooses **fix-it-upstream** over compensating downstream (D-2-02), and
  **per-context correctness** over uniform simplicity (D-2-01, D-2-03). Three of four decisions
  introduce a conditional rather than a single global rule, each justified by measured data.
- Precision/recall preference has **inverted** since Phase 1. There the user wanted maximum recall
  at collection time (D-01 broad bundle). Here they accepted a *lower* threshold to recover real
  documents — same underlying goal (don't lose genuine signal), now applied at the gate rather
  than the query.

## Deferred Ideas

- **`_rss_pillar` hint** in `raw_json` as a classifier prior — available, deliberately unused.
  The hint reflects which query surfaced a document, not what it is about; using it would
  contaminate the classifier with search-term bias. Revisit only with a scored baseline.
- **IR subdomain source tiering** (`investor.exxonmobil.com`, `ir.*`) — 2 documents today. It is a
  `config/sources.yaml` weighting concern, so it belongs in Phase 3, not here.
- **R-1: `--since` is a silent no-op** for RSS and EDGAR (only GDELT honours it). Phase 4
  incremental jobs will re-fetch a fixed 90-day window forever. Collector bug, not NLP.
- **XOM query recall** — `_primary_company` picks the longest alias, so XOM queries
  "ExxonMobil Holdings Corporation", a name publishers do not use (22 docs vs AAPL's 170).
  Collector-side; fix before Phase 5 publication.
- **ClimateBERT greenwashing signals** — post-v1 per D-018.

## Next Steps

`/gsd:plan-phase 2` — the planner should read this file plus `docs/handoff_to_backend.md` Phase 2.
Open items explicitly left to planning: near-duplicate similarity measure and threshold (D-2-02),
sentence cap per filing section, and cross-sentence aggregation for sentiment/controversy (D-2-04).
