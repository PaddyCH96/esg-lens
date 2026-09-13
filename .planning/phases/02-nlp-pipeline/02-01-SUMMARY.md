---
phase: 02-nlp-pipeline
plan: 01
subsystem: nlp-foundation
tags: [config, provenance, model-registry, fixtures, near-duplicate, cleaning]
requires:
  - config/scoring.yaml
  - config/sources.yaml
  - data/esg_lens.db
  - src/esg_lens/db/schema.sql
provides:
  - config/scoring.yaml nlp block + per-doc-type tau
  - raw_json._rss_feed provenance
  - src/esg_lens/nlp/registry.py (cached model singletons)
  - src/esg_lens/nlp/clean.py (normalisation, dedup, sentence split)
  - tests/fixtures/nlp_corpus.json + scripts/export_nlp_fixtures.py
  - tests/nlp_fixture_meta.py (measured cluster constants)
  - conftest fixtures: stub_pipelines, spacy_nlp, capture_logs, nlp_corpus, signals_conn
affects:
  - 02-02 (entity gate, classification, sentiment)
  - 02-03 (controversy, pipeline, persistence)
tech-stack:
  added: []          # no packages installed — T-02-SC honoured
  patterns:
    - "module-level import + call-time accessor for every model (monkeypatchable)"
    - "measured constants live in tests/nlp_fixture_meta.py, never inline in a test"
    - "every threshold resolves from config/*.yaml; magic-number gate enforces it"
key-files:
  created:
    - scripts/migrate_rss_feed_provenance.py
    - scripts/export_nlp_fixtures.py
    - tests/fixtures/nlp_corpus.json
    - tests/nlp_fixture_meta.py
    - tests/unit/test_nlp_config.py
    - tests/unit/test_rss_feed_provenance.py
    - tests/unit/test_nlp_registry.py
    - tests/unit/test_nlp_fixtures.py
    - tests/unit/test_nlp_clean.py
  modified:
    - config/scoring.yaml
    - config/controversy_lexicon.yaml
    - config/rss_feeds.yaml
    - src/esg_lens/config.py
    - src/esg_lens/collectors/rss.py
    - src/esg_lens/nlp/registry.py
    - src/esg_lens/nlp/clean.py
    - tests/conftest.py
    - .planning/REQUIREMENTS.md
    - .planning/ROADMAP.md
decisions:
  - "tau is a per-doc-type mapping; ScoringConfig._validate refuses the old scalar"
  - "The near-duplicate tokenizer is pinned to re.findall(r'[a-z0-9]+', title.lower()) on the RAW title"
  - "Cluster counts are exported as named constants with a measurement date, never inlined in tests"
  - "The Wisconsin/Copilot pair is asserted to stay APART; no Jaccard value is ever asserted"
  - "EDGAR filing sections never join a news cluster — blocking key is (ticker, doc_type)"
metrics:
  tasks: 4
  commits: 4
  tests_added: 60
  suite: "171 passed, 17 skipped"
  completed: 2026-09-14
---

# Phase 2 Plan 1: NLP Foundation Summary

Config-driven thresholds, RSS feed provenance, test-observable model singletons and a
near-duplicate collapse proven against the live 338-document corpus — 8 clusters covering
19 documents, with two genuinely distinct Microsoft lawsuits correctly kept apart.

## What Shipped

| Task | Commit | What |
|------|--------|------|
| 1 — config + RSS provenance | `d02c770` | per-doc-type `tau`, the `nlp` config block, `lex@2026-09b`, `entity_gate` per feed, `_rss_feed` in `raw_json` + idempotent backfill migration |
| 2a — registry | `12d5c20` | `_build_*` / `get_*` split, cached singletons, `reset()`, `max_length=512` on both pipelines, config-driven `batch_size` |
| 2b — fixture + harness | `d472df9` | `scripts/export_nlp_fixtures.py`, `tests/fixtures/nlp_corpus.json`, `tests/nlp_fixture_meta.py`, six conftest fixtures |
| 3 — clean.py | `9860a2a` | `normalize_title`, `text_for_matching`/`display_text`, `content_tokens`, `jaccard`, `cluster_near_duplicates`, `pick_survivor`, `strip_boilerplate`, `split_sentences` |

A plan-correction commit (`c830d3e`) sits between Tasks 2a and 2b; see below.

## The Plan Was Wrong And Was Corrected Mid-Execution

The plan required "7 multi-document clusters covering 12 documents". That is arithmetically
impossible — 7 clusters of >= 2 documents cover at least 14 — and the "12" was measured against
the 114-row MSFT-only database that existed before AAPL and XOM were collected. The previous
executor stopped rather than bending the code to fit. That was the right call and it is why the
numbers below are real.

Re-measuring exposed the more important problem. Three independent measurements of the SAME
corpus disagreed:

| Measurement | Clusters | Documents | Wisconsin-vs-Copilot J |
|---|---|---|---|
| previous executor | 7 | 19 | 0.50 |
| orchestrator | 8 | 19 | 0.41 |
| 02-RESEARCH.md | — | — | 0.46 |

All three agree on **19 documents covered** and all three put the non-merging pair far below the
0.70 threshold. They differ only because each used a slightly different tokenizer. So the cluster
COUNT and any specific J-VALUE are fragile test targets and the coverage is the robust one.

What was done about it:

1. **The tokenizer is pinned and documented** in `clean.content_tokens`:
   `set(re.findall(r"[a-z0-9]+", title.lower()))` — no stemming, no stopword removal, applied to
   the RAW title, not to `normalize_title` output. 02-RESEARCH.md's reference implementation did
   both of the latter; that variant measures **18** documents, not 19, because normalising strips
   the exchange parenthetical that distinguishes two syndicated re-posts.
2. **The tests assert the robust invariants**: every named duplicate landmark group lands in one
   cluster, the Wisconsin data-center suit and the Copilot/noise suit land in different clusters,
   and total coverage is 19 documents.
3. **The cluster count was derived, not assumed.** Measured under the pinned tokenizer it is
   **8**, and it is exported from `tests/nlp_fixture_meta.py` with the date and tokenizer recorded
   in the docstring, so 02-03 imports the measurement rather than retyping a plan number.
4. **No Jaccard value is asserted for the non-merging pair** — only that it does not merge.

A useful cross-check fell out of this: clustering the fixture's 41 MSFT news records reproduces
exactly the same 8 clusters / 19 documents as clustering the full 101-row MSFT corpus. The
fixture slice is representative, not lucky.

## Measurements Recorded

Against `data/esg_lens.db` (338 rows — AAPL 135 news + 12 sections, MSFT 101 + 13, XOM 74 + 3):

- **Near-duplicate clustering, MSFT news, threshold 0.70, pinned tokenizer:** 8 multi-document
  clusters covering 19 documents; `Microsoft (MSFT) faces class-action lawsuit in Wisconsin over
  data center` and `Microsoft (MSFT) Faces Two New Class Actions Over Copilot And Data Center
  Noise` remain in separate clusters.
- **Fixture stride slice, 75 records (25/ticker), `lex@2026-09b` triggers as substrings:** 7 hits
  against the title as stored (9.3%), 24 hits lowercased (32.0%), ratio 3.4x. Both of 02-03's
  floors (`lower >= 2.0 * stored`, `lower >= 0.25`) clear with margin, on a slice where no single
  ticker can carry the result. This is asserted in `test_nlp_fixtures.py`, so 02-03 cannot be
  silently decalibrated by a corpus refresh.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `config/scoring.yaml` documented a threshold with a false measurement**
- **Found during:** Task 2b
- **Issue:** The comment on `nlp.near_duplicate_jaccard` read "Tuned on the real MSFT corpus:
  7 clusters / 12 docs at 100% precision" — the impossible arithmetic from the pre-correction
  plan, measured on the 114-row MSFT-only database. A future tuner would have trusted it.
- **Fix:** Replaced with the pinned tokenizer, the real measurement (8 clusters / 19 documents,
  2026-09-14) and an explicit warning that the bands for distinct stories very nearly touch.
- **Files modified:** `config/scoring.yaml`
- **Commit:** `d472df9`

**2. [Rule 3 - Blocking] Landmark LIKE patterns had to be scoped tighter than the plan implied**
- **Found during:** Task 2b
- **Issue:** The plan names the duplicate groups loosely ("the Levi & Korsinsky / SueWallSt
  deadline-alert groups"). The natural patterns spanned cluster boundaries: `MSFT Shareholder
  Alert%` also catches a short globenewswire deadline stub that correctly does NOT cluster with
  the rest, and `%Investors Have Opportunity to Lead...%` spans two distinct clusters. Task 3's
  "every landmark group lands in one cluster" assertion would have been false for reasons that
  are not bugs.
- **Fix:** Each pattern is scoped to exactly one cluster (`MSFT Shareholder Alert%Class Action%`;
  the "Investors Have Opportunity to Lead" group split into a plain and a Schall Law Firm
  variant), with a comment in the script explaining why the scoping matters.
- **Files modified:** `scripts/export_nlp_fixtures.py`
- **Commit:** `d472df9`

**3. [Rule 1 - Bug] `strip_boilerplate` needed two more literal patterns**
- **Found during:** Task 3
- **Issue:** A pipe-delimited numeric table row and a bare decimal on its own line (`12.4`) have
  no sentence-ending punctuation, so spaCy glued them onto the following real sentence. The
  degenerate-sentence filter cannot recover from that — by the time it sees the sentence, it
  contains real prose and correctly passes.
- **Fix:** Added two conservative line patterns to `strip_boilerplate`: a line containing no
  letters at all, and a pipe-delimited numeric row. Neither can drop a sentence-bearing line.
- **Files modified:** `src/esg_lens/nlp/clean.py`
- **Commit:** `9860a2a`

**4. [Rule 1 - Bug] A written test asserted a stronger property than union-find provides**
- **Found during:** Task 3
- **Issue:** The first version of `test_syndicated_reposts_score_at_or_above_the_configured_threshold`
  asserted every pair within a landmark group clears 0.70. In the real "MSFT Shareholder Alert"
  cluster, 42~43 and 42~53 clear it while 43~53 scores 0.625 — three syndications of one press
  release, correctly one cluster by transitivity. The test was wrong, not the clustering.
- **Fix:** Assert connectivity (each member clears the threshold against at least one other
  member) with a comment recording the real 0.625 case so nobody "tightens" it back.
- **Files modified:** `tests/unit/test_nlp_clean.py`
- **Commit:** `9860a2a`

### Documented Non-Compliance

**Task 3 asked for the REQUIREMENTS/ROADMAP annotations to ship "in the SAME COMMIT as the
lexicon bump".** That was impossible on resume: the lexicon bump shipped in Task 1's commit
(`d02c770`) in a prior session, before execution stopped at Task 2b. The annotations shipped in
Task 3's commit (`9860a2a`) instead. The plan's stated intent — that the amendment not be
forgotten — is satisfied; only the co-location is not.

### Not Done, Deliberately

- **`scripts/backfill.py` was never re-run.** Every threshold in this phase is calibrated to the
  exact 338 rows currently in `data/esg_lens.db`. The backup at `data/esg_lens.db.bak` is intact.
- **No package was installed** (T-02-SC). `rapidfuzz` and `datasketch` were evaluated and
  rejected in research; neither was needed.

## Verification

```
PYTHONPATH=src .venv/bin/python -m pytest -q
    171 passed, 17 skipped

PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_scoring_fixture.py -q
    17 skipped          # the Phase 3 acceptance fixture is still dormant, as required

magic-number gate (AST-aware, excludes comments AND docstrings)
    clean, exit 0

G-4 gate over every new test file
    no read_text / read_bytes / inspect.getsource / ast.parse / __file__ / open(
```

## Known Stubs

None. Every function in `registry.py` and `clean.py` is implemented and exercised against real
data. `esg_lens/nlp/entity.py`, `classify.py`, `sentiment.py`, `controversy.py` and `pipeline.py`
remain untouched pre-GSD scaffolding — they are 02-02's and 02-03's scope and are explicitly
listed in this plan's `<do_not_touch>`.

## Threat Flags

None. No new network endpoint, auth path or schema change. `_rss_feed` is written with
`json.dumps`, never an f-string (T-02-01); the provenance migration uses parameterised SQL only
(T-02-02); `strip_boilerplate` and the sentence cap are the T-02-04 DoS guard.

## What 02-02 and 02-03 Inherit

```python
from esg_lens.nlp import registry          # NEVER `from ...registry import get_*`
registry.get_esg_classifier()              # monkeypatchable because it is a module attribute

from esg_lens.nlp import clean
clean.text_for_matching(title)             # lowercased — use this for every lexicon/alias match
clean.display_text(title)                  # case intact — use this for anything persisted

# conftest fixtures, ready to use:
#   stub_pipelines, spacy_nlp, capture_logs, nlp_corpus, signals_conn, db_conn

from nlp_fixture_meta import MSFT_MULTI_DOC_CLUSTER_COUNT   # import; do not retype the number
```

## Self-Check: PASSED

All created files verified present on disk; all four commit hashes verified in `git log`.
