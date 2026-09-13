---
phase: 02-nlp-pipeline
plan: 02
subsystem: per-document-analysers
tags: [entity-gate, alias-seeding, classification, sentiment, fail-open, batching]
requires:
  - config/scoring.yaml nlp block + per-doc-type tau (02-01)
  - config/rss_feeds.yaml feeds.*.entity_gate (02-01)
  - raw_json._rss_feed provenance (02-01)
  - src/esg_lens/nlp/registry.py, clean.py (02-01)
  - tests/conftest.py fixtures (02-01)
  - data/cache/company_tickers.json (Phase 1 EDGAR collector)
provides:
  - src/esg_lens/nlp/alias_seed.py (EDGAR-cached-title name + alias fallback)
  - scripts/seed_aliases.py
  - src/esg_lens/nlp/entity.py (gate_decision, load_aliases, usable_aliases, best_alias_match)
  - src/esg_lens/nlp/classify.py (classify_documents, classify_section, tau_for)
  - src/esg_lens/nlp/sentiment.py (score_texts, label_for, aggregate_section)
  - company_aliases populated for AAPL/MSFT/XOM in data/esg_lens.db
affects:
  - 02-03 (pipeline.py persists these decisions; controversy.py runs the second spaCy pass)
  - Phase 3 (reads included/exclusion_reason/category/pillar/relevance/sentiment_score)
tech-stack:
  added: []          # no packages installed — T-02-SC honoured; rapidfuzz rejected, stdlib difflib used
  patterns:
    - "trust is declared in config beside the feed it describes, never as a name in a .py"
    - "fail open + marker + warning, never silently admit and never silently reject"
    - "batching is proven by recorded call lengths, not by inspection"
    - "an inference failure yields NULL, never a fabricated 0.0"
key-files:
  created:
    - src/esg_lens/nlp/alias_seed.py
    - src/esg_lens/nlp/entity.py
    - src/esg_lens/nlp/classify.py
    - src/esg_lens/nlp/sentiment.py
    - scripts/seed_aliases.py
    - tests/unit/test_alias_seed.py
    - tests/unit/test_nlp_entity.py
    - tests/unit/test_nlp_classify.py
    - tests/unit/test_nlp_sentiment.py
    - tests/integration/test_nlp_models.py
    - .planning/phases/02-nlp-pipeline/deferred-items.md
  modified:
    - tests/integration/test_backfill.py    # leaked settings.CACHE_DIR; Rule 3 fix
    - data/esg_lens.db                      # 3 company names + 9 aliases; raw_documents untouched
decisions:
  - "The entity gate runs NER even when containment already matched, so entities_json cannot say 'no ORGs' about a document NER never saw"
  - "An inference failure is recorded low_confidence + NULL relevance, never not_esg_relevant"
  - "MSFT's SEC title is ALL-CAPS 'MICROSOFT CORP'; every comparison is lowercased, so the plan's 0.963 ratio holds unchanged"
  - "aggregate_section raises on mismatched lengths rather than letting zip() drop the tail"
metrics:
  tasks: 3
  commits: 6
  tests_added: 69
  suite: "252 passed, 17 skipped"
  completed: 2026-09-14
---

# Phase 2 Plan 2: Entity Gate, Classification and Sentiment Summary

The three per-document analysers, built on an alias table that now actually exists: EDGAR-cached
titles give every ticker a real name and a usable alias, the gate trusts feeds per config and
fails open loudly when it cannot function, and classification is batched, config-thresholded per
doc type, and pinned to the real model's label set by an offline contract test.

## What Shipped

| Task | Commit | What |
|------|--------|------|
| 1 — tests | `38d8c53` | failing alias-seed tests |
| 1 — impl | `4d10af4` | `alias_seed.py`, `scripts/seed_aliases.py`, run against the real DB |
| 2 — tests | `85ee36e` | failing entity-gate tests |
| 2 — impl | `4b80652` | `entity.py` |
| 3 — tests | `d063ba7` | failing classify/sentiment/integration tests |
| 3 — impl | `f989ce0` | `classify.py`, `sentiment.py` |

Exact order: `38d8c53` → `4d10af4` → `85ee36e` → `4b80652` → `d063ba7` → `f989ce0`. Every task is RED-then-GREEN with the failing
run recorded before the implementation existed.

## The Root Cause Is Actually Fixed

Before this plan, on `data/esg_lens.db`:

```
companies:        ('MSFT','MSFT'), ('AAPL','AAPL'), ('XOM','XOM')
company_aliases:  0 rows
```

The entity gate had literally nothing to match. After `scripts/seed_aliases.py`:

| ticker | name | aliases |
|---|---|---|
| AAPL | `Apple Inc.` | `Apple Inc.` (legal), `Apple` (common), `AAPL` (common) |
| MSFT | `MICROSOFT CORP` | `MICROSOFT CORP` (legal), `MICROSOFT` (common), `MSFT` (common) |
| XOM | `ExxonMobil Holdings Corp` | `ExxonMobil Holdings Corp` (legal), `ExxonMobil` (common), `XOM` (common) |

Re-running inserts 0 rows. `raw_documents` is still **338**; `scripts/backfill.py` was not run.
`yfinance_meta.py` was not touched — it remains a Phase 1 defect tracked in `deferred-items.md`.

## Measurements Recorded

All verified on 2026-09-14 with `en_core_web_sm` 3.7.x / transformers 4.44, and all now pinned by
a test:

| Measurement | Value |
|---|---|
| `SequenceMatcher('microsft corp', 'microsoft corp')` | **0.963** — admits |
| `SequenceMatcher('microsft corp', 'microsoft')` | **0.727** — would reject if first-match-wins |
| `SequenceMatcher('micron', 'microsoft')` | **0.667** — best MSFT alias, rejects |
| ORG spans on `Microsoft (MSFT) Faces Shareholder Lawsuit; TD Cowen…` | **3 cased, 1 lowercased** |
| Real DEF 14A section vs the 512-token limit | **>512 tokens, classifies without raising** |
| FinBERT-ESG label set vs `category_weights` + `Non-ESG` | **exact match** |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `test_backfill.py` leaked `settings.CACHE_DIR` into every later test**
- **Found during:** Task 1
- **Issue:** `test_backfill_handles_collector_failure_without_raising` set
  `cfg.settings.CACHE_DIR` to a `tmp_path` and never restored it. Harmless while nothing else
  read the real cache — and `alias_seed.py` now does. The new real-cache test passed in isolation
  and failed in the full suite, which is exactly how this class of bug hides.
- **Fix:** converted to `monkeypatch.setattr`, which restores on teardown and on failure.
- **Files modified:** `tests/integration/test_backfill.py`
- **Commit:** `4d10af4`

**2. [Rule 2 - Missing critical functionality] The gate skipped NER when containment matched**
- **Found during:** Task 2
- **Issue:** The first implementation resolved clause (i) before running spaCy, as a
  cheapest-first optimisation. That is ~3ms faster and makes `entities_json` read
  `{"orgs": []}` for the MAJORITY of admitted documents — because most real headlines contain the
  company name as a substring. `esg_signals.entities_json` is documented as "spaCy ORG entities
  found", so the persisted value would assert "no organisations in this headline" about documents
  NER was never run on. An audit trail that cannot distinguish "found none" from "never looked"
  is worse than none, and it is the same class of dishonesty as a fabricated score.
- **Fix:** NER runs whenever the gate applies; containment is evaluated after it. The skip paths
  (EDGAR, trusted feed, no aliases) still never load spaCy, and three tests monkeypatch
  `registry.get_spacy` to `pytest.fail` to prove it. Cost is ~3ms against 4.4x measured headroom.
- **Files modified:** `src/esg_lens/nlp/entity.py`
- **Commit:** `4b80652`

### Decisions the Plan Left Open

- **An inference failure is `low_confidence` with a NULL relevance**, not `not_esg_relevant`. The
  plan said "return a result the pipeline can persist as excluded" without choosing a reason. The
  `exclusion_reason` CHECK has no 'error' value; `not_esg_relevant` would assert the model judged
  the document irrelevant when it never ran. The NULL relevance is what separates a failure from a
  genuine below-tau result, which always carries a float. Both are asserted.
- **`aggregate_section` raises `ValueError` on mismatched lengths.** `zip()` would silently drop
  the tail and return a plausible wrong number over a sentence set that is, by contract, the same
  set seen twice.
- **`classify_section`'s `relevance` is the mean over the WHOLE relevant set**, across categories,
  not just the winning category's sentences. The plan's wording ("mean confidence over exactly
  that sentence set") points at the set defined in the preceding clause, and the sentiment
  aggregate must run over the same set for the two numbers to describe the same document.

### A Plan Detail That Was Slightly Wrong (and is harmless)

The plan repeatedly names the seeded MSFT legal alias `Microsoft Corp`. The SEC file actually
carries `MICROSOFT CORP`, all-caps. Nothing downstream cares — every alias comparison in
`entity.py` lowercases both sides, so the stated 0.963 and 0.667 ratios hold exactly as written.
It is recorded in a test comment so nobody later "fixes" the casing into an invented name the SEC
does not assert.

### Not Done, Deliberately

- `scripts/backfill.py` was not re-run; `raw_documents` is still 338 rows.
- No package was installed. `rapidfuzz` was rejected in research; `difflib` is stdlib (T-02-SC).
- `polarity` is not computed — Phase 3.
- Nothing in `scoring/`, `schema.sql`, `registry.py`, `clean.py`, `conftest.py` or `config/*.yaml`
  was edited.

## Verification

```
PYTHONPATH=src .venv/bin/python -m pytest -q
    252 passed, 17 skipped          # 171 baseline + 69 new + 12 (alias) … no regressions

PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_scoring_fixture.py -q
    17 skipped

grep for model-confidence assertions in the unit tests
    none

magic-number gate over src/esg_lens/nlp/*.py (AST-aware)
    clean, exit 0

G-4 gate over all five test files this plan owns
    no read_text / read_bytes / inspect.getsource / ast.parse / __file__ / open(

git diff pyproject.toml
    empty
```

## Known Stubs

None. Every exported function is implemented and exercised. `controversy.py` and `pipeline.py`
remain empty — they are 02-03's scope and are in this plan's `<do_not_touch>`.

## Threat Flags

None new. `alias_seed.py` uses parameterised SQL with `INSERT OR IGNORE` and the existing
`alias_type` CHECK (T-02-07). `entity.py` parses `raw_json` inside try/except and defaults an
unparseable or unknown feed to `apply` (T-02-10). `max_length=512` is still on both pipelines and
the >512-token section test proves it (T-02-08). The integration test sets `HF_HUB_OFFLINE=1`
(T-02-11). The `gate_skipped_no_aliases` marker plus warning is the recorded, auditable trade for
T-02-09 (research assumption A5).

## What 02-03 Inherits

```python
from esg_lens.nlp import alias_seed, entity, classify, sentiment

aliases  = entity.load_aliases(conn, ticker)          # call ONCE per ticker, not per document
decision = entity.gate_decision(doc, aliases, feeds_cfg)
#   .admit .reason .entities .gate_skipped .matched_alias .match_ratio .match_kind
#   .entities_json   -> ready for esg_signals.entities_json

results = classify.classify_documents(titles, doc_type="news", ticker=t)
section = classify.classify_section(clean.split_sentences(body), ticker=t, document_id=i)
#   .included .exclusion_reason .category .pillar .relevance .relevant_sentences

scores = sentiment.score_texts(titles)                 # None entries mean the model failed
label  = sentiment.label_for(score)                    # None-safe
sec    = sentiment.aggregate_section(
             [r for _, r in section.relevant_sentences],
             [scores[i] for i, _ in section.relevant_sentences],
         )
```

Two spaCy passes per news document are intentional. This plan's is CASED (NER); 02-03's is
LOWERCASED (lemma matching). **Do not merge them** — both `entity.py`'s docstring and
`test_ner_on_cased_text_finds_more_orgs_than_on_lowercased_text` exist to make that fail loudly.

## Self-Check: PASSED

All ten created files verified present on disk. All six commit hashes verified in `git log`.
