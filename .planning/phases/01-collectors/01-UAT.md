---
status: testing
phase: 01-collectors
source: [01-01-SUMMARY.md, 01-02-SUMMARY.md, 01-03-SUMMARY.md]
started: 2026-09-03T20:00:00Z
updated: 2026-09-03T20:00:00Z
---

## Current Test

number: 2
name: Re-run Backfill adds zero rows (content_hash dedup)
expected: |
  Re-run same backfill command immediately — adds zero new rows to raw_documents, logs show cached/hishel hit, exits 0, collection_runs shows n_new 0
awaiting: user response

## Tests

### 1. Cold Start + Backfill AAPL,XOM populates raw_documents
expected: Run PYTHONPATH=src python scripts/backfill.py --tickers AAPL,XOM — populates raw_documents with GDELT news + EDGAR filing sections, populates companies + company_aliases, exits 0
result: pass

### 2. Re-run Backfill adds zero rows (content_hash dedup)
expected: Re-run same backfill command immediately — adds zero new rows to raw_documents, logs show cached/hishel hit, exits 0, collection_runs shows n_new 0
result: [pending]

### 3. Collector never-raise contract
expected: Force a collector failure (e.g., invalid ticker or mocked 500) — produces collection_runs row with status failed and returns [] without raising exception to caller
result: [pending]

### 4. EDGAR User-Agent and rate limits
expected: Every outbound EDGAR request carries User-Agent containing contact email (@) and observed request rates stay within 10/s for EDGAR and 1/s for GDELT (check logs or test_http_client spike)
result: [pending]

### 5. yfinance sustainability isolation
expected: Any yfinance sustainability score lands in companies.external_esg_score and nowhere else — never drives scoring input, verified via DB query SELECT external_esg_score
result: [pending]

### 6. GDELT query construction D-01..D-03
expected: GDELT query is alias OR-group filtered per D-02 (drops <=4/stoplist, quoted) plus broad ESG bundle per D-01 (30 terms) quoted per D-03, chunked at 400 chars into 2 queries with warning gdelt_query_chunked, domain lowercased without www, seendate ISO Z, content_hash sha256
result: [pending]

### 7. NewsAPI disabled by default
expected: NewsApiCollector.fetch returns [] without any HTTP call when enabled false (default) — verify via test_newsapi_flag or logs show newsapi_disabled
result: [pending]

## Summary

total: 7
passed: 1
issues: 0
pending: 6
skipped: 0

## Gaps

