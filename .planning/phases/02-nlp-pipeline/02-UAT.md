---
status: testing
phase: 02-nlp-pipeline
source: [02-01-SUMMARY.md, 02-02-SUMMARY.md, 02-03-SUMMARY.md, 02-PHASE-SUMMARY.md]
started: 2026-09-16T00:00:00Z
updated: 2026-09-16T00:00:00Z
mvp_mode: false
---

## Current Test

none — all 11 executed 2026-09-16.

## Tests

### 1. Cold Start — fresh DB, schema plus migrations, pipeline runs
expected: init_db.py builds the schema, both migrations apply and are idempotent on a second run, and the pipeline processes documents without error
result: pass

### 2. Pipeline turns every document into exactly one signal row
expected: 338 documents in, 338 esg_signals rows out — one per document, none silently dropped. Included and excluded both persisted.
result: pass

### 3. Every exclusion carries a stated reason (NLP-08)
expected: Every included=0 row has a non-null exclusion_reason drawn from the schema CHECK. All five reachable reasons appear: not_esg_relevant, entity_mismatch, duplicate, low_confidence, too_old.
result: pass

### 4. Pitfall 1 — controversy fires on Title Case headlines (NLP-06)
expected: Title Case headlines like "Apple (AAPL) Faces £3 Billion UK iCloud Lawsuit As Class Action Moves Ahead" score a controversy severity >= 2. Before the lowercasing fix this matched 6/101; it should now be far higher.
result: pass

### 5. Controversy terms are stored, and the negation guard demotes
expected: Every row with controversy_severity > 0 stores its matched terms as the audit trail. "The securities fraud lawsuit was dismissed" scores one tier lower than the same sentence without "dismissed" — the window is symmetric, so a negator AFTER the trigger still counts.
result: pass

### 6. Filing sections aggregate to ONE row with MAX controversy (D-2-04)
expected: A 10-K Item 1/1A section produces exactly one esg_signals row keyed to its document_id, not one per sentence. Controversy across sentences takes the MAX severity, never the mean, with matched terms unioned.
result: pass

### 7. Per-source entity-gate trust (D-2-03)
expected: Yahoo ticker feed and EDGAR documents skip the entity gate (identity is provider-asserted); Google News documents are gated. Trust is read from config/rss_feeds.yaml, not hard-coded.
result: pass

### 8. Entity gate fails open, loudly, when aliases are missing
expected: A ticker with no usable aliases admits the document, leaves exclusion_reason NULL, records a marker in entities_json, AND emits a warning. It must never admit silently.
result: pass (after investigation — see Gaps)
evidence: |
  Initially flagged as the weakest test: the no_aliases path had NEVER fired on live data
  (markers were {none: 242, trusted_feed: 52, edgar: 9, no_aliases: 0}), so it was proven only by
  a unit test. A debug pass proved it properly:
  - REACHABLE, with a real trigger. Alias seeding reads the cached SEC company_tickers.json, which
    holds SEC filers only. NSRGY, RHHBY, BASFY and VWAGY are all absent — unsponsored ADRs do not
    file with the SEC. Zero live occurrences is a sampling artefact of a 3-ticker corpus in which
    all three happen to be SEC filers, not dead code.
  - The existing unit tests are genuinely behavioural, verified by mutation: removing the marker
    fails 1 test, downgrading the warning to debug fails 2, failing closed fails 2.
  - Proven end to end on a real NSRGY document through the real pipeline: included=1,
    exclusion_reason=None, entities_json carries gate_skipped=no_aliases plus
    gate_skipped_no_aliases=true, and the entity_gate_skipped_no_aliases WARNING fires.
  No source change was needed for the gate itself — D-2-03 was implemented correctly.

### 9. Re-running at the same model_version is a no-op (NLP-07)
expected: A second pipeline run over the same documents inserts 0 rows and reports them all as skipped_existing.
result: pass

### 10. Performance — 50 documents under 10 seconds on CPU (SC-1)
expected: Models load once per process (~1.8s warm), then 50 documents complete inference in well under the 10s budget.
result: pass

### 11. Phase 2/3 boundary holds
expected: esg_signals.polarity is NULL on every row, scoring/weights.py has exactly three functions, and tests/unit/test_scoring_fixture.py reports exactly 17 skipped. Fewer skips means Phase 3 leaked forward.
result: pass

## Summary

total: 11
passed: 11
issues: 0
pending: 0
skipped: 0
bugs_found_and_fixed: 1 (severe, found while investigating test 8)

## Gaps

### G-2-01 (FIXED) — filing sections were being destroyed by title-only near-duplicate clustering

Found while investigating why 28 filing sections carried only 9 `edgar` gate markers. The other 19
had been excluded before the entity gate ran: 16 as `duplicate`, 3 as `too_old`.

Those 16 were wrong. `clean.cluster_near_duplicates` tokenises the TITLE ALONE, and EDGAR section
titles are synthetic labels the splitter writes. The corpus has **28 filing sections sharing only
4 distinct titles** — 13 documents all titled `'SEC Filing 8-K'`. Every 8-K MSFT files scores
Jaccard 1.0 against every other one.

Destroyed: five separate MSFT 8-Ks (3831-4809 bytes of different bodies, dates spanning
2026-04 to 2026-09) collapsed to one; 10-K Item 1 for 2026, 2025 and 2024 collapsed to one; same
for Item 1A. **57% of the filing evidence base discarded behind a green suite** — the Phase 1
failure mode exactly.

Fix: `NEVER_LEXICALLY_DEDUPED = frozenset({"filing_section"})` in clean.py. The obvious
alternative was measured and rejected first — adding the body to the token set drops collapsing
pairs 35 -> 16, but consecutive quarterly 8-Ks still score 0.94-0.96 on shared XBRL boilerplate.
Lexical similarity cannot separate filings. The correct argument is structural: two filings from
one issuer are two legal events, never two copies of one document, and `raw_documents` already
carries UNIQUE (ticker, content_hash) so exact duplicates are unstorable.

Measured effect, filings-only as intended:
    filing duplicates  16 -> 0        news duplicates  16 -> 16 (unchanged)
    filings included    9 -> 19       total included  148 -> 161
Thirteen filing documents recovered — and filings are the G pillar's best free source, which the
RSS switch had already left news-thin.

Commit 28af7b3. Suite 310 passed / 17 skipped (was 306/17).

### Open, not blocking Phase 2

- **AAPL 10-K Item 1 bodies are 18 bytes** (`"Item 1. Business 1"`). The EDGAR section splitter is
  extracting headers rather than section bodies. Pre-existing Phase 1 defect, unrelated to the NLP
  pipeline, but it means AAPL contributes almost no filing signal. Worth a Phase 1 fix before
  Phase 5 publication.
- **No run-level alarm for fail-open.** A ticker can sit in the corpus failing open forever and the
  only trace is a per-document warning nobody greps. A summary line in `nlp_pipeline_complete`
  ("N of M tickers had no usable aliases") would surface it where someone would act. Deferred.
- `exclusion_reason='unsupported_language'` has no producer — v1 is English-only by assumption.
