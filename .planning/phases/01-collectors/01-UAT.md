---
status: complete
phase: 01-collectors
source: [01-01-SUMMARY.md, 01-02-SUMMARY.md, 01-03-SUMMARY.md]
started: 2026-09-03T20:00:00Z
updated: 2026-09-07T00:00:00Z
verdict: FAIL — 2 of 7 failed; GDELT collects zero documents
---

## Current Test

none — all 7 executed 2026-09-06/07.

## Environment

- `.env` created with a real `CONTACT_EMAIL` (SEC EDGAR requires one). Gitignored.
- DB and HTTP cache wiped before the cold-start run, so test 1 was a genuine cold start.
- Note: test 1 was previously marked `pass` on 2026-09-03, but the database was empty (0 rows in
  every table) when re-audited on 2026-09-05. That earlier result was not reproducible and has
  been re-run from scratch.

## Tests

### 1. Cold Start + Backfill AAPL,XOM populates raw_documents
expected: Run backfill --tickers AAPL,XOM — populates raw_documents with GDELT news + EDGAR filing sections, populates companies + company_aliases, exits 0
result: **FAIL**
evidence: |
  Exit code 0 and "Backfill complete: fetched=15 new=15", but every document came from EDGAR:
    AAPL edgar filing_section 12 · XOM edgar filing_section 3 · GDELT 0
    collection_runs: AAPL/gdelt failed, XOM/gdelt failed, both edgar ok
  companies and company_aliases populated correctly for both tickers.
  GDELT — the primary news source per research_notes.md §2.3 — returned nothing. The exit code
  masks this: the run "succeeds" while producing no news signal at all.

### 2. Re-run Backfill adds zero rows (content_hash dedup)
expected: Re-run adds zero new rows, exits 0, collection_runs shows n_new 0
result: **PASS**
evidence: |
  Re-fetched all 15 EDGAR documents and inserted 0. raw_documents before=15 after=15 delta=0.
  content_hash dedup and INSERT OR IGNORE both behave as specified.
  (Tested at collector level. The full backfill re-run had to be killed after 10+ minutes —
  see Gap G-3: GDELT retry backoff stalls the whole run.)

### 3. Collector never-raise contract
expected: Force a collector failure — collection_runs row with status failed, returns [] without raising
result: **PASS**
evidence: |
  tests/unit/test_collector_base.py::test_safe_fetch_never_raises_and_writes_failed_row asserts
  the behaviour (returns [], status=failed, error truncated to 1000 chars, job_id recorded).
  Confirmed in the wild: repeated live GDELT failures wrote status=failed rows and the backfill
  continued to EDGAR and exited 0 rather than crashing. This contract works.

### 4. EDGAR User-Agent and rate limits
expected: Every EDGAR request carries a User-Agent containing a contact email; rates stay within 10/s EDGAR and 1/s GDELT
result: **PASS (after fixes)**
evidence: |
  Was only covered by test_token_bucket_exists_and_not_lock_only, which asserts the STRING
  "TokenBucketTransport" appears in http.py — a grep that passes even if the limiter never
  limits. Added tests/unit/test_rate_limit_behaviour.py with real timing assertions:
    - GDELT 4 requests take >= 2.7s (observed rate <= 1.5/s)
    - EDGAR 15 requests take >= 0.4s
    - buckets are per-host (a slow GDELT bucket does not throttle EDGAR)
    - USER_AGENT rejects placeholder domains, not merely absence of "@"
  The last one is a real gap it closed: config.py's validator only checks for "@", so the shipped
  default "esg-lens@example.com" satisfied it while violating SEC policy.
  **GDELT rate was wrong**: config said 1/s; GDELT's own 429 body says one request per 5 seconds.
  Corrected to 0.2/s. See G-1 — still insufficient in practice.

### 5. yfinance sustainability isolation
expected: Any yfinance sustainability score lands in companies.external_esg_score and nowhere else
result: **PASS (by inspection) — NOT verifiable with live data**
evidence: |
  yfinance Ticker.sustainability returns HTTP 404 "No fundamentals data found" for both AAPL and
  XOM, so external_esg_score is NULL and there is no score to isolate. The assertion is vacuously
  true. Isolation confirmed by inspection: external_esg_score appears only in yfinance_meta.py
  and the companies table, never in signals or scoring.
  This is the yfinance risk in ROADMAP.md materialising ("High likelihood"), and research_notes.md
  §2.1 predicted it ("intermittently empty for long stretches"). The design already degrades
  gracefully, which is the right outcome — but D-006 (external score is benchmark-only) cannot be
  positively demonstrated until Yahoo serves data again.

### 6. GDELT query construction D-01..D-03
expected: alias OR-group per D-02 + broad ESG bundle per D-01, quoted per D-03, chunked at 400 chars
result: **FAIL**
evidence: |
  The queries are constructed as specified — correct alias filtering, quoting, and 400-char
  chunking — and the 18 unit tests in test_gdelt_query.py pass. But GDELT REJECTS them:
    HTTP 200, content-type text/html, body: "Your query was too short or too long."
  Construction is correct per the spec; the spec itself produces queries GDELT will not serve.
  A short hand-built query against the same endpoint returns valid JSON, so the API is healthy
  and the fault is ours. D-01/D-02/D-03 need redesign, not a bug fix.

### 7. NewsAPI disabled by default
expected: NewsApiCollector.fetch returns [] without any HTTP call when enabled is false
result: **PASS**
evidence: |
  tests/unit/test_newsapi_flag.py::test_newsapi_disabled_returns_empty_without_network is
  genuinely behavioural — asserts docs == [] and route.call_count == 0 with respx mounted.

## Summary

total: 7
passed: 4
failed: 2
partial: 1
issues: 4

## Gaps

### G-1 (BLOCKER) — GDELT collects zero documents
GDELT is the primary news source. Without it the system has only SEC filings, which means no
news-derived E/S/G signal at all — the project's stated differentiator (research_notes.md §1.1:
"News-driven signals refresh daily — this is our actual differentiator").
Two compounding causes:
  a) Query rejected: "Your query was too short or too long." The D-01 bundle (~30 terms) plus the
     D-02 alias group exceeds what GDELT will serve, even under the D-03 400-char chunking.
  b) Rate limit: config said 1/s, GDELT's own 429 says one per 5 seconds. Corrected to 0.2/s,
     but sustained probing still triggers a longer cooldown, so 0.2/s may still be too fast.
Fix requires redesigning the GDELT query strategy — fewer terms per request, more requests, wider
spacing — which is a Phase 1 rework, not a patch. Do not start Phase 2 on top of this: an NLP
pipeline with no news to process cannot be meaningfully tested.

### G-2 (HIGH) — exit code 0 hides total failure of a source
The backfill exits 0 and prints "Backfill complete: fetched=15 new=15" while a source collected
nothing. The never-raise contract (correctly) prevents a crash, but nothing escalates
"a source produced zero documents for every ticker" to the operator or the exit code.
Suggested: non-zero exit, or a loud summary line, when any enabled source returns 0 across all
tickers. This matters more in Phase 4, where a job would be marked done with no news signal.

### G-3 (MEDIUM) — a throttled source stalls the entire run
The full re-run had to be killed after 10+ minutes: tenacity retries (2s, 4s, 8s...) across two
query chunks and two tickers, with no overall deadline. In Phase 4 this hangs a job with no
progress and no cancellation path (DELETE /portfolio/{job_id} is deferred post-v1).
Suggested: a per-collector wall-clock budget, and cap retries on 429 specifically.

### G-4 (LOW) — grep-based tests reported as coverage
Several Phase 1 tests assert that a string appears in a source file rather than testing
behaviour: test_token_bucket_exists_and_not_lock_only, test_newsapi_flag_check_present_in_source,
test_base_contains_structlog_and_never_raise, parts of test_collector_base and test_gdelt_query.
These pass regardless of whether the code works, and one of them (the token bucket) was masking a
5x-wrong rate limit. Partially addressed by test_rate_limit_behaviour.py; the rest should be
converted or deleted rather than counted as coverage.

## Fixes applied during UAT

- config/sources.yaml — GDELT rate 1/s -> 0.2/s, with the verified limit documented.
- collectors/gdelt.py — guard the JSON parse. A non-200 or non-JSON body now skips that chunk with
  a warning instead of throwing, so one bad chunk no longer discards documents an earlier chunk
  already returned, and no longer fails the whole collector.
- collectors/edgar.py + collectors/base.py — persist the resolved CIK to companies.cik. Nothing
  was writing that column: yfinance has no CIK to supply and EDGAR resolved it per-run and
  discarded it, so it stayed NULL forever despite being in the schema. Verified: AAPL 0000320193,
  XOM 0002115436.
- tests/unit/test_rate_limit_behaviour.py — new, 5 behavioural rate-limit and User-Agent tests.

## Note on XOM identity

The cold-start run resolved XOM to CIK 0002115436, "ExxonMobil Holdings Corporation", which looks
wrong against the long-standing CIK 0000034088. Verified directly against SEC's own
company_tickers.json: SEC maps XOM to 2115436 / "ExxonMobil Holdings Corp". The collector is
correct and Exxon has reorganised. AAPL resolves to 320193 as expected. No defect.

---

## Post-verification findings (gsd-verifier, 2026-09-07)

Independently confirmed against the database and source before acting on them:

- **N2 (fixed)** — the JSON-parse guard added earlier in this UAT made observability *worse*.
  Skipping a rejected chunk let `safe_fetch` return normally, so a total failure was recorded as
  `XOM|gdelt|ok|0|0`. The pre-fix `failed` row was more honest. Now: if every query is rejected
  and zero documents result, the collector raises, and `safe_fetch` records `status=failed` with
  the reason. Partial rejection logs `gdelt_partial_collection` and still returns what it got.
- **N5 (fixed)** — `AsyncHttpClient` built two `TokenBucketTransport` instances (cached and
  force-refresh) with independent per-host bucket dicts, so a force-refresh run received a second
  full budget: up to 2x the configured rate, i.e. 20 req/s against sec.gov. That is exactly the
  IP-block failure the limiter exists to prevent. The bypass client now shares one bucket.
- **N1 (open)** — `safe_fetch` writes `n_new = len(docs)`, a duplicate of `n_fetched`. The
  re-run rows read `edgar|ok|12|12` although zero rows were inserted (proven in UAT 2). `n_new`
  has never meant anything. Fixing it needs insert counts plumbed back from the caller, so it
  belongs with the Phase 1 rework, not a patch.
- **N3 (open)** — every GDELT integration fixture mocks a 200 JSON body, so the suite asserts a
  contract the live API refuses and cannot go red on G-1. Add a fixture reproducing the real
  "Your query was too short or too long." response as part of the rework.
- **N4 (open)** — token buckets initialise capacity to `rate`, so at 0.2/s the bucket never holds
  a whole token and every request pays a ~4s pre-wait. With 429 in the retry set and no overall
  deadline, this is the mechanical cause of the 10-minute stall in G-3.
- **N6 (open)** — `_get_rate_for_host` matches with `key in host` / `key.endswith(host)` over an
  unordered dict, so host-to-rate resolution is order-dependent.
