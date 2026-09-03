# Context — ESG Lens

Verbatim-attributed notes from DOC-classified sources: research_notes.md,
handoff_to_backend.md, opencode_model_routing.md. Lowest precedence; background and rationale.

---

## Topic: Why this project exists / positioning
source: docs/research_notes.md §1.1, §4
Commercial ESG raters (MSCI, Sustainalytics, Refinitiv, S&P) correlate only ~0.4–0.6 with each
other (Berg, Kölbel & Rigobon, *Aggregate Confusion*, MIT) versus ~0.99 for credit ratings.
Consequence adopted as a design constraint: do not claim a "true" ESG score; ship a transparent,
reproducible score with every input auditable. The honest framing is
"a transparent, reproducible ESG signal aggregator", not "an ESG rating".

Known biases to disclose publicly: disclosure bias (large caps disclose more, so score higher —
mitigated by exposing coverage/confidence), greenwashing in self-reported filings, and rating lag
(news-driven refresh is the actual differentiator).

## Topic: Approach family selection
source: docs/research_notes.md §1.2, §1.3
Three families: disclosure-based, incident/controversy-based, quantitative-metric-based.
**ESG Lens leans incident/controversy-based** — highest-signal, lowest-bias (the RepRisk model).
Literature consensus baked into the methodology: controversies dominate positive PR; sentiment
alone is not ESG (relevance gate first is the #1 failure mode of naive builds); recency decay is
near-universal; source-quality weighting matters, with a static domain tier map as a cheap proxy.

## Topic: Data source evaluation
source: docs/research_notes.md §2 — all external quota claims self-marked `[VERIFY]`
- **GDELT DOC 2.1** — primary news backbone. 65+ languages, 15-min cadence, no key.
  Returns titles + URLs + metadata, NOT full article text. `V2Themes` (`ENV_*`, `HUMAN_RIGHTS`,
  `CORRUPTION`, `LABOR_*`) usable as a free pre-filter. Entity matching noisy ("Apple" the fruit).
- **SEC EDGAR** — 10-K Item 1A + Item 1 → E and S; DEF 14A proxy is the single best free G source
  (without proxies G scoring is nearly empty); 8-K as controversy trigger. Filings are a slow
  baseline; news is the delta. 10-K ≈ 100k+ tokens, so section-split before any BERT.
- **yfinance** — unofficial scraper of an undocumented endpoint; no SLA; `sustainability` often
  empty. Wrap in a `MetadataProvider` interface, cache 7d, degrade gracefully on `None`.
- **NewsAPI free tier** — ~100 req/day, ~24h delay, truncated content, commercial use prohibited.
  Supplementary only, flagged off. RSS feeds and `Ticker.news` noted as unevaluated alternatives
  for the same slot.

## Topic: Model evaluation detail
source: docs/research_notes.md §3
FinBERT-ESG is a *sentence-level* classifier trained on ~2k annotated sentences — feed it
sentences or headlines, not paragraphs; treat softmax as a ranking, not a probability.
ProsusAI/finbert predicts sentiment from an investor's viewpoint: "Company settles pollution
lawsuit for less than expected" is financially positive and environmentally negative.
ClimateBERT `commitment` + `specificity` heads together form a genuine greenwashing signal
(high commitment + low specificity + self-reported source = discount), but cover only a slice of E.
`bart-large-mnli` runs one forward pass per candidate label — 9× FinBERT compute, the single
biggest performance risk in the design. Lighter alternative noted: `MoritzLaurer/deberta-v3-base-mnli`.

## Topic: Ground rules for the implementer
source: docs/handoff_to_backend.md §0
Read order: architecture.md → data_model.md → scoring_methodology.md → api_design.md.
1. Do not build a frontend. 2. Do not invent scoring logic — raise ambiguity, do not silently
"improve" it. 3. Do not change the DB schema without updating data_model.md in the same commit.
4. Config over constants — a magic number in a `.py` file is a bug. 5. No live network in tests.
6. Atomic commits carrying the phase number, e.g. `feat(collectors): add GDELT client [P1]`.

## Topic: Build phase order (handoff numbering, authoritative for roadmapping)
source: docs/handoff_to_backend.md §1
Phase 0 Scaffold → Phase 1 Collectors → Phase 2 NLP pipeline → Phase 3 Scoring engine →
Phase 4 API + jobs → Phase 5 Validation. Each phase carries an explicit Definition of Done.
This Phase 0–5 scheme is the ONLY use of "Phase N" in the document set and covers v1 in full.
Work deliberately excluded from v1 is labelled "post-v1" instead: ClimateBERT commitment/
specificity (research_notes.md §3.3, §3.6) and ONNX Runtime inference (architecture.md §6).

## Topic: Assumptions to challenge rather than code around
source: docs/handoff_to_backend.md §2
Single user / localhost / no auth · portfolio ≤ 25 tickers · ~10 s NLP per ticker acceptable ·
US-listed English-language only · headline-level analysis sufficient · SQLite adequate to ~10^5
docs · GDELT stays free and key-less · EDGAR stays free with a UA header · yfinance is best-effort
and may return nothing · weights are expert priors with no ground truth · Python 3.11+, CPU torch.

## Topic: Implementation traps flagged up front
source: docs/handoff_to_backend.md §4
Loading transformers per document (100× penalty) · installing torch without the CPU index URL
(~2.5 GB of unused CUDA) · skipping the EDGAR User-Agent (IP block) · treating FinBERT sentiment
as ESG polarity (invalidates the score) · gating sufficiency on `w` instead of `w_ev` (a real bug
in an earlier methodology draft) · averaging away controversies · emitting 50 for no data ·
`PRAGMA foreign_keys` defaults OFF and is per-connection · bart-large-mnli on the hot path.

## Topic: Build-tooling / model routing (process, not product)
source: docs/opencode_model_routing.md
Guidance for building the project with OpenCode free tiers. Free lineup rotates monthly; verify
`opencode models` before each phase; prompts may be used for training during free periods (nothing
here is sensitive). Routing rule: cheap models are excellent at filling a well-specified shape and
bad at deciding what the shape should be — these planning docs exist so most of the build is
shape-filling. Green (any free model): scaffold, schema transcription, pydantic models, repo CRUD,
FastAPI routes, YAML config, scoring unit tests. Yellow (strong free + review): collectors, HTTP
client concurrency, 10-K sectioning, NLP batching. Red (best model available): the scoring engine
and the job state machine/concurrency. "If you only ever pay for one thing, make it Phase 3 and
the review passes."

**Scope note for the roadmapper:** this document describes the *authoring workflow*, not the ESG
Lens product. It should not generate product requirements or roadmap phases.
