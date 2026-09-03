# Constraints — ESG Lens

Extracted from the three SPEC-classified documents (api_design.md, architecture.md,
data_model.md) plus scoring_methodology.md. Precedence among these is equal (all SPEC, no
per-doc override). Where they touch the same subject they were verified consistent.

---

## C-001 — Scoring formulas (nfr / algorithmic contract)
source: docs/scoring_methodology.md §3, §5, §6

```
sent(d) = P(positive) - P(negative)
pol(d)  = -min(1.0, 0.3*ctrl + 0.3*max(0, -sent))   if ctrl > 0
        = 0.6 * sent                                 otherwise
w_rec(d)  = 0.5 ** (age_days / half_life)     half_life = 90d news / 365d filings
w_conf(d) = rel(d) if rel(d) >= tau else 0    tau = 0.60
w_ev(d)   = w_src * w_rec * w_conf            # sufficiency gate
w(d)      = w_ev * w_cat(category)            # aggregation
raw_P     = Σ w·pol / Σ w
base_P    = 50 + 50*raw_P
pen_P     = min(40, Σ_{ctrl>0} k[ctrl]*w_rec*w_src),  k = {1:1.0, 2:4.0, 3:12.0}
S_P       = clamp(base_P - pen_P, 0, 100)
gate      = Σ w_ev < min_evidence (1.0)  →  null / insufficient_data
S         = Σ_P pillar_weight[sector][P] * S_P, renormalised over present pillars
confidence = clamp(0.4*min(1, n_docs/30) + 0.3*min(1, Σ w_ev/10) + 0.3*pillar_coverage, 0, 1)
```
`max_age_days` default 730 — older documents excluded entirely.
Both `w_ev` and `w` persisted per signal so scores are re-derivable without re-running NLP.

## C-002 — Category → pillar map and materiality weights (schema/config)
source: docs/scoring_methodology.md §2
E: Climate Change 0.45, Natural Capital 0.25, Pollution & Waste 0.30 (sum 1.00)
S: Human Capital 0.40, Product Liability 0.35, Community Relations 0.25 (sum 1.00)
G: Corporate Governance 0.45, Business Ethics & Values 0.55 (sum 1.00)
Non-ESG dropped at gate 2. All three pillar sets verified to sum to 1.0.

## C-003 — Sector pillar weights (config)
source: docs/scoring_methodology.md §2
default E .34 / S .33 / G .33; Energy .50/.25/.25; Utilities .45/.25/.30;
Financial Services .15/.25/.60; Technology .25/.35/.40; Healthcare .20/.45/.35;
Consumer Defensive .35/.40/.25. All verified to sum to 1.00.

## C-004 — Source credibility tiers (config)
source: docs/scoring_methodology.md §5
tier 1 wire/major 1.00 · tier 2 reputable 0.80 · tier 3 unknown 0.50 (default) ·
tier 4 promotional 0.25 (prnewswire/businesswire/globenewswire/IR domains) · filing 0.70 (EDGAR).

## C-005 — Controversy lexicon severity tiers (config)
source: docs/scoring_methodology.md §4
3 severe/systemic · 2 material · 1 minor/allegation · 0 none. spaCy PhraseMatcher over lemmas
with a negation guard (`cleared of`, `dismissed`, `settled without`, `dropped`) demoting by 1.

## C-006 — HTTP / external API constraints (protocol)
source: docs/architecture.md §5.1; docs/handoff_to_backend.md Phase 1; docs/research_notes.md §2
EDGAR 10 req/s AND a declared User-Agent containing a contact email (mandatory — omission = IP block).
GDELT ≤ 1 req/s, undocumented soft limits, backoff on 429. Disk cache 24h keyed on request URL,
metadata TTL 7 days. Ticker→CIK via `company_tickers.json`, cached; do not scrape.

## C-007 — API contract surface (api-contract)
source: docs/api_design.md
Base path `/api/v1`, JSON, no auth in v1 (header key addable later), RFC-7807 errors,
OpenAPI at `/docs` and `/openapi.json`. Ticker `^[A-Z0-9.\-]{1,10}$` uppercased server-side.
Status codes 200/202/400/404/409/422/429/500/503. `sources` enum must stay byte-identical to the
`raw_documents.source` CHECK constraint.

## C-008 — Database schema constraints (schema)
source: docs/data_model.md
- `raw_documents.source` CHECK IN ('gdelt','edgar','yfinance','newsapi')  ← 'rss' deliberately absent
- `raw_documents.doc_type` CHECK IN ('news','filing_section','press_release')
- `esg_signals` UNIQUE (document_id, model_version); controversy_severity BETWEEN 0 AND 3
- `esg_scores.status` CHECK IN ('ok','insufficient_data','failed'); composite NULL when insufficient
- `esg_scores.evidence_weight` = Σ w_ev(d), explicitly NOT Σ w(d)
- `jobs.status` CHECK IN ('queued','running','done','partial','failed','cancelled')
- `score_contributions.contribution` = signed points contributed by ONE signal, defined as
  `50 * (w * pol) / Σ(w over the pillar)`; contributions sum to `(base_P - 50)`; the controversy
  penalty is NOT distributed across contributions but reported on `esg_scores.{e,s,g}_penalty`.
  `rank` = 1 is the largest |contribution|.
- `esg_scores.job_id` ON DELETE SET NULL so job purges never delete score history
- WAL journal mode; `PRAGMA foreign_keys = ON` set per connection

## C-009 — Performance / non-functional budget (nfr)
source: docs/architecture.md §1; docs/research_notes.md §3.6; docs/handoff_to_backend.md §2
~10 s NLP per company for ~50 headlines on laptop CPU → analysis MUST be an async job.
spaCy sm ~5 ms/doc · FinBERT-ESG ~40 ms/sentence · FinBERT sentiment ~40 ms/sentence ·
controversy negligible · bart-large-mnli ~1–3 s/doc (off by default).
CPU-only torch, Python 3.11+, ~16 GB RAM if Ollama is co-resident. Corpus assumed < ~10^5 documents.
Single process: `uvicorn src.esg_lens.api.main:app` and nothing else. One job at a time.

## C-010 — Scope boundaries (nfr)
source: docs/handoff_to_backend.md §3; docs/architecture.md §8
Out of scope for v1: frontend/dashboard · auth/multi-tenancy · Docker/K8s · Celery/Redis ·
Postgres · real-time streaming · full-text article scraping · non-English / non-US coverage ·
model fine-tuning · portfolio weighting by position size · backtesting against returns.

## C-011 — Testing constraints (nfr)
source: docs/architecture.md §7; docs/handoff_to_backend.md §0.5
No live network in tests — recorded fixtures + `respx`. Collectors tested against fixtures;
NLP tested with stub models asserting wiring not accuracy; scoring tested exhaustively against
hand-computed values including the §8 worked example.

## C-012 — Reporting / transparency constraints (api-contract)
source: docs/api_design.md client contract notes; docs/scoring_methodology.md §6.3, §6.4, §9
Confidence must be rendered next to every score (product requirement, not a nicety).
Never render null as 0. `sector_percentile` is the only cross-company comparison field, null below
5 scored peers. Sensitivity analysis (±25% weight perturbation) required before publishing.
