---
phase: 02-nlp-pipeline
status: complete
plans: [02-01, 02-02, 02-03]
requirements: [NLP-01, NLP-02, NLP-03, NLP-04, NLP-05, NLP-06, NLP-07, NLP-08]
suite: "306 passed, 17 skipped"
completed: 2026-09-16
---

# Phase 2: NLP Pipeline — Phase Summary

Every `raw_documents` row now becomes exactly one auditable `esg_signals` row: gated, classified,
scored, weighted, stamped with a `model_version`, and — when excluded — carrying a stated reason.
Phase 3 can compute a score and explain it without re-reading a single document.

## The three plans

| Plan | Delivered |
| ---- | --------- |
| 02-01 | `registry.py` (lazy model singletons, `max_length=512` on both heads), `clean.py` (cased/lowercased text, pinned near-duplicate tokenizer, `pick_survivor`, sentence splitting with a DoS cap), the `config/scoring.yaml` `nlp` block, the real-corpus fixture + `tests/nlp_fixture_meta.py`, and the conftest stubs |
| 02-02 | `alias_seed.py` + `entity.py` (per-feed trust, fail-open-loudly), `classify.py` (batched, per-doc-type tau, filing-section aggregation), `sentiment.py` (financial `P(pos)-P(neg)`, never a fabricated 0.0), `company_aliases` seeded for AAPL/MSFT/XOM |
| 02-03 | `controversy.py` (lemma matching that fires on Title Case, symmetric negation guard, ADJ filter, section MAX), `scoring/weights.py` (three functions), `esg_signals.duplicate_of` FK + migration, `pipeline.py` (8 stages), the perf guard |

## What the phase actually fixed

1. **Pitfall 1 — the silent 6%.** `attr="LEMMA"` matching is case-sensitive, so the trigger
   `lawsuit` never matched `Lawsuit` and controversy detection fired on ~6% of headlines instead of
   ~39%, with a green suite. `severity()` now lowercases before matching, and five real Title Case
   corpus headlines are asserted through it — delete the lowercasing and they fail. A separate
   lowercased-vs-stored differential on the ticker-balanced 75-record slice guards the headroom
   against corpus or lexicon erosion.
2. **Nothing is silently dropped.** `COUNT(esg_signals) == COUNT(documents processed)`. Every
   excluded row carries a reason; every included row carries none. Both directions asserted.
3. **Gate precedence is declared**, not incidental: `too_old` > `duplicate` > `entity_mismatch` >
   `not_esg_relevant` > `low_confidence`, with `_exclude` refusing to overwrite.
4. **Age exclusion runs before clustering**, so every non-NULL `duplicate_of` references a row that
   is actually `included=1` — asserted as an invariant over the whole corpus, not one case.
5. **Inference is batched and each model loads once**, proven by recorded call lengths and counting
   builders rather than by review. 19 clustered documents cost one classification per survivor.
6. **Re-running is a no-op** at the same `model_version`, and a bumped version (the lexicon is part
   of the key) reprocesses everything while the old rows survive.

## The Phase 2 / Phase 3 line, held

`weights.py` has exactly three functions. `polarity` is NULL on every row and
`weights.polarity` is asserted *not callable*, so the boundary is a CI gate rather than a human
reading a skip count. `aggregate.py` and `composite.py` are untouched.
`tests/unit/test_scoring_fixture.py` still reports **17 skipped** — Phase 3's acceptance contract
has not been activated early.

`evidence_weight(w_src, w_rec, relevance, *, tau=None)` keeps `tau` keyword-with-default because
that fixture calls it with three positional arguments; a required fourth parameter would have
raised `TypeError` the instant Phase 3 unskipped it, with nothing in Phase 2 able to notice.

## Honest limits carried forward

- The whole phase is calibrated against the exact 338 rows in `data/esg_lens.db` (backup at
  `data/esg_lens.db.bak`). **Do not re-run `scripts/backfill.py`.**
- Cluster counts and Jaccard values are tokenizer-dependent; the tokenizer is pinned in
  `clean.content_tokens` and the measured constants live in `tests/nlp_fixture_meta.py`. Re-measure
  rather than editing a failing number in place.
- `exclusion_reason='unsupported_language'` has no producer: v1 is English-only (assumption 3) and
  inventing a detector to fill a CHECK value would be a capability claim with nothing behind it.
- `sentiment_score` is financial sentiment, not ESG polarity. Phase 3 must combine it with
  `controversy_severity` and category; reading it as polarity bakes in a sign error.
- The entity gate is strict on the fixture slice (many `entity_mismatch` exclusions on
  syndicated/aggregator headlines). That is visible in the DB by design — Phase 3's coverage
  reporting is what should decide whether it needs loosening, not a guess here.
- `01b-04` (GDELT) remains blocked externally and is unrelated to this phase.
