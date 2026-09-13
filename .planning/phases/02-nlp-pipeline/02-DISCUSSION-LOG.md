# Phase 2 Discussion Log

**Date:** 2026-09-14 · **Mode:** discuss (default, interactive) · **Areas selected:** 4 of 4

## Prior context applied

- Phase 1 decisions **D-01, D-02, D-03 are dead** — all three were GDELT query construction,
  superseded by the RSS switch in 01c. Not carried forward; noted so the planner does not resurrect them.
- Carried forward: never-raise contract, config-over-constants (D-015), raw SQL + repositories
  (D-011), structlog JSON with ticker/source context.
- `todo.match-phase 2` returned 0 matches. No `.planning/codebase/` maps exist — grep fallback used.
- No SPEC.md, no checkpoint, no existing plans.

## Method note

Gray areas were generated **after** benchmarking the models against the real 242-document corpus,
not before. Every option presented carried measured evidence — threshold distributions, duplicate
rates, per-model latency. This is deliberate: Phase 1 failed verification because its spec was
written on assumptions about an API nobody had probed.

## Areas discussed

**1. Relevance threshold (tau) on headlines** → per-doc-type tau (news 0.45, filing 0.60).
Driver: 8/50 real headlines below 0.60, two of them genuine ESG lawsuits at 0.45/0.48.

**2. Near-duplicate syndicated stories** → collapse in `clean.py`, keep highest source tier,
store the rest as `included=false, exclusion_reason='duplicate'`.
Driver: 3.1% duplicate rate; duplicates inflate both the score and the `min_evidence` gate.

**3. Entity gate strictness per source** → trust entity-guaranteed feeds (Yahoo ticker, EDGAR),
gate Google News. Trust declared per-feed in `rss_feeds.yaml`.
Driver: RSS uses one alias per query, so the gate became primary — but Yahoo headlines often omit
the company name entirely.

**4. EDGAR filing sections** → sentence-split, classify batched, aggregate to ONE signal row.
Relevance = mean over ESG-relevant sentences only.
Driver: `esg_signals` is keyed to `document_id`; Item 1A buries material content past boilerplate.

## Scope creep

None raised. Four candidate ideas were routed to Deferred rather than folded in: the `_rss_pillar`
classifier prior, IR-subdomain source tiering (Phase 3 concern), the `--since` no-op collector bug
(R-1, Phase 1 concern), and XOM query recall (collector-side).

## Open items handed to the planner

- Near-duplicate similarity measure and threshold — must be deterministic, no model inference.
- Sentence cap per filing section.
- Cross-sentence aggregation for sentiment and controversy. Controversy should almost certainly
  take **max** severity, not mean — `scoring_methodology.md` §6.1 treats controversies as
  non-averageable, and averaging would contradict the methodology's core editorial judgment.
