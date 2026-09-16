---
phase: 02-nlp-pipeline
plan: 03
subsystem: controversy-and-orchestration
tags: [controversy, lemma-matching, duplicate_of, weights, pipeline, batching, audit-trail]
requires:
  - src/esg_lens/nlp/registry.py, clean.py (02-01)
  - src/esg_lens/nlp/entity.py, classify.py, sentiment.py (02-02)
  - config/controversy_lexicon.yaml, config/scoring.yaml nlp block (02-01)
  - tests/conftest.py fixtures + tests/nlp_fixture_meta.py (02-01)
provides:
  - src/esg_lens/nlp/controversy.py (severity, section_controversy, build_matchers)
  - src/esg_lens/scoring/weights.py (recency_weight, evidence_weight, scoring_weight — and nothing else)
  - esg_signals.duplicate_of nullable FK + scripts/migrate_duplicate_of.py
  - src/esg_lens/nlp/pipeline.py (process_documents — 8 stages, one esg_signals row per document)
affects:
  - Phase 3 (reads every column this writes; polarity, aggregate.py and composite.py are still its own)
  - Phase 4 (the API explains a score from the rows this persists)
tech-stack:
  added: []          # no packages installed — T-02-SC honoured
  patterns:
    - "lowercase BEFORE lemma matching; the cased NER pass and the lowercased lemma pass are never merged"
    - "controversies take MAX severity with the UNION of terms — never a mean (§6.1)"
    - "nothing is silently dropped: an excluded document persists with a stated reason"
    - "batching is proven by recorded call lengths, not by inspection"
    - "an excluded row's weights are NULL, never a 0.0 Phase 3 could aggregate"
key-files:
  created:
    - src/esg_lens/nlp/controversy.py
    - src/esg_lens/scoring/weights.py
    - src/esg_lens/nlp/pipeline.py
    - scripts/migrate_duplicate_of.py
    - tests/unit/test_nlp_controversy.py
    - tests/unit/test_phase2_weights.py
    - tests/unit/test_nlp_pipeline_gates.py
    - tests/unit/test_nlp_pipeline_signals.py
    - tests/integration/test_nlp_perf.py
  modified:
    - src/esg_lens/db/schema.sql          # esg_signals.duplicate_of
    - docs/data_model.md                  # same DDL, same commit (handoff §0.3)
    - tests/unit/test_schema.py           # expected-column list
decisions:
  - "Gate precedence is a declared tuple, not the incidental order of the stages"
  - "Age exclusion runs BEFORE clustering, so every duplicate_of names an included row"
  - "tau is keyword-with-default on evidence_weight — a cross-phase signature contract"
  - "Filing-section relevance is the mean over ESG-relevant sentences only; controversy is the MAX"
metrics:
  tasks: 4
  commits: 8
  suite: "306 passed, 17 skipped"
  completed: 2026-09-16
---

# Phase 2 Plan 03: Controversy, Weights and the Pipeline Summary

Deterministic controversy severity that actually fires on Title Case headlines, the three Phase 2
weight functions, the `duplicate_of` audit FK, and an eight-stage pipeline that turns every
`raw_documents` row into exactly one auditable `esg_signals` row.

## What Was Built

| Task | Name | Commits |
| ---- | ---- | ------- |
| 1 | `nlp/controversy.py` — lemma matching that fires on headlines | 78dc98e, bd120de |
| 2 | `duplicate_of` FK and the three weight functions | 60e2f3b, 13af20b |
| 3a | `pipeline.py` stages 1-5, gate precedence, excluded-row persistence | 1ba0b59, d797d4f |
| 3b | `pipeline.py` stages 6-8, weights, filing sections, no-op re-run | 268206a, 24502b3 |

### Task 3b specifically (this session)

- `_analyse_documents` — one `classify_documents` call per `(doc_type)` group, one batched
  `score_texts` call over the admitted set, then `controversy.severity` per admitted text. A
  one-at-a-time loop fails `test_inference_is_actually_batched`, which asserts
  `max(recorded len(texts)) > 1`.
- `_analyse_sections` — the D-2-04 path: `clean.split_sentences` (capped at
  `nlp.max_sentences_per_section`) → `classify.classify_section` → relevance-weighted
  `sentiment.aggregate_section` over **exactly** the sentence set that produced the category →
  `controversy.section_controversy` (MAX severity, UNION of terms). The sentiment pass is batched
  *across* sections, so a ten-section filing costs one sentiment call. Exactly one
  `esg_signals` row per `document_id`.
- `_apply_weights` — `scoring_methodology.md` §5 via `esg_lens.scoring.weights`, with `tau` passed
  explicitly by keyword from `classify.tau_for(doc_type)` (news 0.45, filing_section 0.60 — never a
  literal) and the half-life resolved per doc type. Excluded rows keep NULL weights.
- `source_weight()` reads the tier weight off `clean.source_tier`, so the tier map stays in one
  place rather than being duplicated between `clean` and `pipeline`.
- Everything populates the `SignalRecord` that Task 3a's single `INSERT_SQL` already covered.
  **No second write path was added.**
- `document_text()` and `section_sentences()` are exported so tests can ask *what was submitted to
  the classifier* without reading source (gap G-4).

## Verification

```
PYTHONPATH=src .venv/bin/python -m pytest -q          306 passed, 17 skipped
tests/unit/test_scoring_fixture.py -q                 17 skipped   (Phase 3 has not leaked)
magic-number gate over src/esg_lens/nlp                clean, exit 0
G-4 gate over all five plan test files                 exit 0
G-4 carve-out narrowness on test_schema.py             exit 0
grep polarity src/esg_lens/nlp/                        comments only, no assignment
weights.py                                             exactly 3 functions
```

Baseline was 295 passed / 17 skipped; Task 3b added 10 unit tests plus the perf test. The perf
test ran against the real weights (the HF cache is present) and cleared the 10s budget.

Proven by test, not by review:
- **Duplicates never cost inference.** Over the 19 documents covered by multi-document clusters,
  the recorded classifier submissions attributable to them total exactly
  `MSFT_MULTI_DOC_CLUSTER_COUNT` — one per survivor. The count is imported from
  `tests/nlp_fixture_meta.py`; it is tokenizer-dependent and is never retyped.
- **Re-run is a no-op.** A second run at the same `model_version` inserts 0 rows and leaves every
  `processed_at` byte-identical; a bumped version inserts a fresh row per document and the
  originals survive untouched.
- **Each model is built once** across a whole run (counting builders on `registry._build_*`).
- **`polarity` is NULL on every row**, and all five weights are non-NULL on every included row.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `test_nlp_pipeline_gates.py` needed the inference stubs**

- **Found during:** Task 3b verification.
- **Issue:** Task 3a's gate tests were written when `_apply_analysis` was a no-op, so they call
  `process_documents` over the whole fixture corpus with no `stub_pipelines`. The moment stage 6
  existed they began constructing the real 400MB FinBERT heads; the file did not finish inside a
  120-second timeout.
- **Fix:** Added an autouse `_stub_inference(stub_pipelines)` fixture to that file, with a comment
  explaining that it weakens nothing — not one assertion in that module concerns a model's output,
  and the stubs admit everything the real models admitted, so gate precedence, dedup bookkeeping
  and the never-raise assertions observe exactly what they observed before. File now runs in 24s,
  all 14 tests still pass.
- **Files modified:** `tests/unit/test_nlp_pipeline_gates.py` (named in the plan's
  `files_modified`).
- **Commit:** 24502b3

**2. [Rule 1 - Bug] The "duplicates never cost inference" assertion counted ids, not submissions**

- **Found during:** Task 3b RED→GREEN.
- **Issue:** The first draft summed `submitted.count(text)` once per clustered document id. Cluster
  members frequently share a byte-identical title, so the survivor's single classification was
  charged again to every duplicate that read the same — reporting 14 classifications where 8 had
  actually happened. The implementation was correct; the measurement was not.
- **Fix:** Count over *submissions* (`sum(1 for text in submitted if text in clustered_texts)`),
  with the reasoning recorded in the test.
- **Commit:** 24502b3

No architectural changes were needed and no packages were installed (T-02-SC).

## Known Stubs

None. Every column Phase 2 owns is populated from a real computation; `polarity` is deliberately
NULL and is Phase 3's to fill (`weights.polarity` is asserted absent by
`test_phase2_weights.py::test_polarity_is_not_implemented_in_phase_two`).

## Notes for Phase 3

- `model_version` is `finbert-esg-9cat@v1|prosus-finbert@v1|lex@2026-09b`. The lexicon version is
  part of the cache key on purpose (T-02-16): a lexicon edit changes the string, the stage-2 no-op
  stops matching, and every document is reprocessed. Rows at the old version survive.
- `sentiment_score` is FINANCIAL sentiment, not ESG polarity. "Microsoft carbon emissions rose 25%"
  scores +0.919 POSITIVE and that is correct on its axis. Phase 3 computes `polarity` from
  `sentiment_score` + `controversy_severity` + category.
- `weight_evidence` is the sufficiency weight and excludes `w_cat` (D-008). `weight_total` is the
  aggregation weight. Do not swap them.
- `duplicate_of` always references a document that has an `included=1` row at the same
  `model_version`, or is NULL. That invariant is asserted over the whole corpus run.

## Self-Check: PASSED

- `src/esg_lens/nlp/pipeline.py` — FOUND
- `src/esg_lens/nlp/controversy.py` — FOUND
- `src/esg_lens/scoring/weights.py` — FOUND
- `tests/unit/test_nlp_pipeline_signals.py` — FOUND
- `tests/integration/test_nlp_perf.py` — FOUND
- commits 78dc98e, bd120de, 60e2f3b, 13af20b, 1ba0b59, d797d4f, 268206a, 24502b3 — FOUND
