---
status: resolved
trigger: "UAT test 8: no_aliases fail-open path has 0 occurrences on live data; 28 filing sections but only 9 edgar markers"
created: 2026-09-16
updated: 2026-09-16
---

## Current Focus

hypothesis: (P) no_aliases branch is reachable via a ticker absent from SEC company_tickers.json; (S) 19 missing edgar markers are pre-gate exclusions, but dedup on synthetic filing titles is a real bug
test: mutation-test the unit tests; end-to-end run on scratch DB with NSRGY
expecting: mutations fail tests; NSRGY admits with marker+warning
next_action: run mutation tests

## Symptoms

expected: no_aliases fail-open observable and proven
actual: 0 no_aliases markers in 338 live signals; 19/28 filing sections lack entities_json
errors: none
reproduction: sqlite query over data/esg_lens.db
started: always

## Eliminated

## Evidence

- checked: data/cache/company_tickers.json (10426 tickers)
  found: unsponsored ADRs NSRGY/RHHBY/BASFY/VWAGY absent
  implication: real-world trigger for no_aliases exists

- checked: filing_section signal breakdown
  found: 9 edgar + 16 duplicate + 3 too_old = 28
  implication: secondary hypothesis confirmed mechanically

- checked: clean.cluster_near_duplicates
  found: clusters on TITLE tokens only; filing titles are synthetic ("SEC Filing 8-K")
  implication: distinct 8-Ks with distinct bodies collapse at jaccard 1.0 — real bug

## Resolution

root_cause: |
  PRIMARY (no bug): the no_aliases branch IS reachable. Trigger = a ticker absent from the
  cached SEC company_tickers.json (10,426 SEC filers). Unsponsored ADRs of large non-US
  issuers — NSRGY, RHHBY, BASFY, VWAGY — are absent. alias_seed logs
  alias_seed_ticker_absent_from_cache and inserts nothing; companies.name stays the bare
  ticker (yfinance 404s, Phase 1 defect); load_aliases returns [ticker]; usable_aliases
  strips it; 0 < min_aliases_for_entity_gate(1) -> branch fires. 0 live occurrences is a
  SAMPLING artefact of the 3-ticker corpus (all SEC filers), not dead code.

  SECONDARY (real bug, fixed): 28 filing sections = 9 edgar markers + 16 duplicate + 3 too_old.
  The 19 without markers were excluded at stages 3-4, before the entity gate — mechanism
  confirmed. But the 16 'duplicate' exclusions are WRONG. clean.cluster_near_duplicates
  tokenises the TITLE only, and EDGAR section titles are synthetic labels the splitter writes
  ('SEC Filing 8-K'), so every 8-K a company files scores jaccard 1.0 against every other.
  Five distinct MSFT 8-Ks with 3.8-4.8kB of different bodies collapsed to one; three years of
  10-K Item 1 collapsed to one. 57% of the filing evidence base discarded, suite green.

fix: |
  clean.NEVER_LEXICALLY_DEDUPED = {"filing_section"}; cluster_near_duplicates skips those blocks.
  Adding the body to the token set was measured and rejected: pairs drop 35 -> 16 but consecutive
  quarterly 8-Ks still score 0.94-0.96 on shared XBRL boilerplate. UNIQUE (ticker, content_hash)
  on raw_documents already makes exact duplicates unstorable, so lexical clustering of filings
  can only produce false positives.

verification: |
  * 8 mutations (marker / warning / admit removed; dedup fix reverted; exemption widened to news)
    each caught by at least one test.
  * End-to-end on a scratch copy of the live DB with a real NSRGY document through the real
    process_documents: included=1, exclusion_reason NULL,
    entities_json {"orgs": [], "gate_skipped": "no_aliases", "gate_skipped_no_aliases": true},
    warning entity_gate_skipped_no_aliases at level=warning, ticker=NSRGY, document_id=339.
  * Full suite 310 passed / 17 skipped. weights.py still 3 functions. polarity still all NULL.
    data/esg_lens.db untouched at 338 rows.

files_changed: [src/esg_lens/nlp/clean.py, tests/unit/test_nlp_pipeline_gates.py]
