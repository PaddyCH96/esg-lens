---
phase: 01-collectors
verified: 2026-09-14T00:00:00Z
status: human_needed
score: 5/6 success criteria verified (1 vacuous, 0 failed)
re_verification:
  previous_status: gaps_found
  previous_score: 3/6 (1 vacuous, 2 failed)
  previous_verified: 2026-09-07
  gaps_closed:
    - "backfill populates raw_documents with news + EDGAR sections (RSS, 227 news docs)"
    - "A source that collects nothing is visible to the operator (exit 2, per-source breakdown, dead_sources)"
    - "collection_runs reports how many documents were newly persisted (N1)"
    - "Rate limiting cannot be circumvented within the process (N5, shared TokenBucketTransport)"
    - "A throttled source cannot stall the run (N4 bucket capacity, 120s deadline, circuit breaker)"
  gaps_remaining: []
  regressions: []
requirements:
  COLL-01: satisfied
  COLL-02: satisfied
  COLL-03: superseded by ADR 01c — requirement text still names GDELT, must be amended
  COLL-04: satisfied
  COLL-05: partial (isolation clause still vacuous — yfinance sustainability 404)
  COLL-06: satisfied
  COLL-07: satisfied (as specified; cross-feed near-duplicates survive by design — see R-2)
overrides:
  - must_have: "scripts/backfill.py --tickers AAPL,XOM populates raw_documents with GDELT news and EDGAR filing sections"
    reason: "GDELT's DOC API refused this IP for 8+ days independent of query shape (01b proved the rework was sound and irrelevant). ADR .planning/phases/01c-rss-primary/01c-DECISION.md replaces GDELT with RSS as the primary news source; GDELT collector retained behind ESG_ENABLE_GDELT=1. Measured: GDELT 0 docs/3m23s/exit 2 vs RSS 227 docs/17s/exit 0. The criterion's INTENT — news documents flow into raw_documents — is met."
    accepted_by: "PaddyCH96"
    accepted_at: "2026-09-14T00:00:00Z"
    conditional_on: "REQUIREMENTS.md COLL-03 and the ROADMAP Phase 1 SC1 text are amended to name RSS as primary. Until then the written contract and the shipped system disagree."
human_verification:
  - test: "Re-check Ticker.sustainability for AAPL/XOM once Yahoo serves data again; confirm external_esg_score populates and no signal/scoring path reads it"
    expected: "companies.external_esg_score non-NULL; grep finds it only in yfinance_meta.py and the companies table"
    why_human: "Upstream returns 404 today. SC6/D-006 is vacuously true and cannot be positively demonstrated. Unchanged since 2026-09-07 and not attributable to the source switch."
  - test: "Judge whether the RSS ESG-term query arm has acceptable recall for a heavy-ESG-news issuer. XOM returned 22 documents across E+S+G in 90 days (11/6/5) vs AAPL's 170; the XOM E-pillar hits include 'ExxonMobil Announces Second-Quarter 2026 Results' and 'XOM Stock Quote Price and Forecast'."
    expected: "A human decides whether ~0.24 ESG-query docs/day for XOM is sufficient signal, or whether the query strategy needs alias broadening"
    why_human: "Recall is unmeasured and unmeasurable without a scored baseline (ADR states this explicitly). Requires editorial judgement, not a grep."
  - test: "Amend REQUIREMENTS.md COLL-03 and its traceability table (all COLL-01..07 still read 'Pending'), and ROADMAP Phase 1 Success Criterion 1, to name RSS as the primary news source"
    expected: "The written requirement matches the shipped system; the override above becomes unnecessary"
    why_human: "A documentation decision, not a code change. Left unamended, Phase 5 publication would claim a GDELT-backed corpus that does not exist."
findings:
  - id: R-1
    severity: warning
    title: "--since is silently a no-op for the primary news source"
    detail: "RssCollector.fetch accepts `since` and never reads it — the window comes only from rss_feeds.yaml lookback_days (90). EdgarCollector also accepts and ignores it. Only GdeltCollector honours it, and GDELT is now off by default. `backfill.py --since 2026-09-01` therefore accepts a flag that changes nothing. Dedup keeps the DB correct, but Phase 4's incremental jobs will re-fetch a fixed 90-day window every run and the CLI lies about it."
    location: "src/esg_lens/collectors/rss.py:179, src/esg_lens/collectors/edgar.py:195, scripts/backfill.py"
  - id: R-2
    severity: warning
    title: "content_hash keys on external_id, so the same article from two feeds is two documents"
    detail: "content_hash = sha256(lower(title) | external_id). Google News and Yahoo assign different guids to the same story, and the per-fetch `seen` set dedups by that same hash, so it cannot catch them. Live corpus: 7 of 227 RSS rows (3.1%) are same-ticker duplicate titles with distinct content_hash — e.g. 'Apple faces a $32.5 billion lawsuit over photos face scans' appears twice. Phase 3 weights each as independent evidence, so a widely-syndicated controversy is double-counted. The implementation matches data_model.md; the spec is what is weak."
    location: "src/esg_lens/collectors/base.py content_hash, src/esg_lens/collectors/rss.py fetch `seen`"
  - id: R-3
    severity: warning
    title: "N5 and the circuit breaker have no behavioural test"
    detail: "The shared-bucket fix is correct in code but no test asserts cached + force_refresh share one budget — the exact test the 2026-09-07 report required. The circuit breaker is tested by re-implementing its bookkeeping inside the test (test_circuit_breaker_logic_trips_after_threshold) plus three greps for string literals in backfill.py. Neither exercises backfill_tickers. G-4 (grep-as-coverage) is therefore still open and has acquired new instances."
    location: "tests/unit/test_backfill_dead_source.py, tests/unit/test_http_client.py"
  - id: R-4
    severity: info
    title: "Dead config keys and an unreachable branch"
    detail: "rss_feeds.yaml declares max_items (100/50) and kind; rss.py reads neither, so no item cap is enforced. rss.py's `if resp.status_code != 200` branch is unreachable — _request_with_retry calls raise_for_status, so non-200 arrives as an exception in the except above it. Harmless today; a reader will trust the cap that isn't there."
    location: "config/rss_feeds.yaml, src/esg_lens/collectors/rss.py:216"
  - id: R-5
    severity: info
    title: "investor.* / ir.* subdomains do not match the tier-4 list"
    detail: "Confirmed live: investor.exxonmobil.com (2 docs) resolves to tier 3 (0.50) and would be weighted as independent coverage. businesswire.com IS in the tier-4 list, so only issuer IR subdomains leak. Already named in the ADR; belongs to Phase 2/3, recorded here so it is not rediscovered."
    location: "config/sources.yaml tiers.4"
---

# Phase 1: Collectors Verification Report (re-verification, 2026-09-14)

**Phase Goal:** Free public data flows into `raw_documents` reliably and repeatably — rate-limited,
cached, deduplicated, and never able to crash the process.
**Verified:** 2026-09-14
**Status:** human_needed — **the goal is achieved**; three items need a human, none of them blocking.
**Re-verification:** Yes — supersedes the 2026-09-07 `gaps_found` report, preserved verbatim below.

## Verdict

**The goal is now genuinely achieved, and I say that having tried to break it.**

On 2026-09-07 the sentence "free public data flows into `raw_documents`" was false: 15 rows, all
EDGAR, zero news. It is now true by measurement, not by narration — 242 rows in
`data/esg_lens.db`, 227 `rss|news` and 15 `edgar|filing_section`, spanning 2026-06-16 to
2026-09-13, with zero null `domain`, `title` or `url` and zero `news.google.com` leakage. I ran
those queries against the database myself. The second run inserted 0 and every `n_new` on the
newer `collection_runs` rows reads 0 against `n_fetched` of 186/41/12/3 — which is both the
idempotency proof and the proof that N1 is fixed, in one result.

The honest caveat is that this was achieved by changing the source, not by fixing GDELT. That is
the right call and the ADR argues it well, but it means Success Criterion 1 as written is not
satisfied — it names GDELT. I have recorded an override rather than a pass, because the
distinction matters: the system works, the written contract does not describe the system, and
COLL-03 in REQUIREMENTS.md still reads "GDELT collector against DOC 2.1". Amend it.

What I could not break, having looked for it specifically: the never-raise contract still holds
under RSS (only `safe_fetch` ever calls `fetch`, and it wraps `asyncio.wait_for` plus a bare
`except Exception`, so the deliberate `RuntimeError` on total-refusal becomes `status=failed`
rather than an escape); malformed XML returns `[]` rather than raising; and `raw_json` string
interpolation takes its value from config keys, not from feed content.

What I did find that neither the UAT nor the ADR caught: `--since` is a no-op for the primary news
source (R-1), and cross-feed near-duplicates survive dedup at ~3% of the corpus (R-2). Both are
warnings, both are cheap, neither blocks Phase 2.

## Prior Findings — N1..N6, item by item

I checked each against current code rather than trusting the SUMMARY or the ADR.

| # | 2026-09-07 status | Now | Evidence |
|---|---|---|---|
| N1 `n_new` = `len(docs)` | HIGH, open | ✓ **CLOSED** | `base.py safe_fetch` writes `n_new=None` and returns `last_run_id`; `update_run_new_count()` is called by `backfill.py` after the insert loop with the real count. Live proof: the re-run rows read `AAPL\|rss\|ok\|186\|0` and `XOM\|edgar\|ok\|3\|0`. Asserted by `test_collector_base.py:87`. |
| N2 rejection recorded as `ok` | HIGH, fixed in UAT | ✓ **CLOSED and carried into RSS** | `rss.py` raises when `attempted and failed == attempted and not docs`; `test_total_failure_records_failed_not_ok` asserts `status=failed`, and `test_partial_failure_keeps_what_succeeded` asserts a partial run still returns its documents. The rule survived the source swap, which is the part that could easily have been lost. |
| N3 fixtures encode a contract the API refuses | HIGH, open | ✓ **CLOSED** | `tests/integration/test_gdelt_real_contract.py` (6 tests) now pins the real rejection and rate-limit bodies (`gdelt_query_rejected.txt`, `gdelt_rate_limited.txt`). The RSS fixtures were captured live on 2026-09-14 before the collector was written — `rss_googlenews_xom_E.xml`, `rss_googlenews_aapl_S.xml`, `rss_yahoo_ticker_aapl.xml`. |
| N4 bucket capacity == rate | MEDIUM, open | ✓ **CLOSED** | `http.py`: `capacity = max(1.0, rate)`. At 0.2/s the first request no longer pays a ~4s pre-wait. Reinforced by a `COLLECTOR_TIMEOUT_SECONDS` deadline in `safe_fetch` and the new backfill circuit breaker. |
| N5 two independent bucket sets | MEDIUM, open | ✓ **CLOSED in code, untested** | `_bypass_client` is constructed with `transport=self._token_transport` — the same instance as the cached client. The required behavioural test does not exist (R-3). |
| N6 fuzzy host→rate match | LOW, open | ✓ **CLOSED** | `_get_rate_for_host` now does exact → longest dotted-suffix → longest substring, with a logged conservative `DEFAULT_UNMAPPED_RATE = 1.0` fallback. The old hardcoded `return 1` for any host containing "gdelt" — which silently bypassed the 0.2/s config — is gone. |

**6 of 6 closed.** G-1, G-2 and G-3 from the UAT are closed too; **G-4 (grep-as-coverage) is not**,
and the new circuit-breaker tests added three more instances of it (R-3).

## Goal Achievement — ROADMAP Success Criteria

| # | Success criterion | Status | Evidence |
|---|---|---|---|
| 1 | backfill populates `raw_documents` with **GDELT** news and EDGAR sections; populates `companies` + `company_aliases` | ✓ PASSED (override) | `rss\|news\|227`, `edgar\|filing_section\|15`. `companies` AAPL/0000320193 and XOM/0002115436 with CIKs persisted, 5 `company_aliases` rows. News flows; it is not GDELT's. Override recorded — amend the criterion text. |
| 2 | Re-running adds **zero** new rows — `content_hash` dedup works | ✓ VERIFIED | 242 fetched, 0 inserted, `raw_documents` 242 → 242. Every re-run `collection_runs` row carries `n_new=0` — which now means something (N1). |
| 3 | Forced failure → `collection_runs` row + empty list, no exception escapes | ✓ VERIFIED | `safe_fetch` catches `TimeoutError` and `Exception`, writes `failed`, returns `[]`. Verified under RSS by `test_total_failure_records_failed_not_ok` and `test_malformed_xml_does_not_raise`. Only `safe_fetch` calls `fetch` anywhere in `src/` or `scripts/` — grepped. |
| 4 | Integration tests pass against recorded fixtures with `respx`, no live network | ✓ VERIFIED (fidelity improved) | 99 passed, 17 skipped, 6.71s — reproduced locally, not taken from the SUMMARY. The 17 skips are `test_scoring_fixture.py`, Phase 3's acceptance gate, correctly skipped with an explicit missing-symbol list. Fixtures are now live captures on both the success and the rejection path. |
| 5 | EDGAR UA carries a contact email; rates within 10/s EDGAR, 1/s GDELT | ✓ VERIFIED | One shared `TokenBucketTransport` (N5), capacity ≥ 1 (N4), deterministic host resolution (N6). `sources.yaml` adds `news.google.com: 0.5` and `feeds.finance.yahoo.com: 1.0` — self-imposed ceilings, documented as such. `test_rate_limit_behaviour.py` asserts real timings. Caveat: `conftest.py` sets `RATE_LIMIT_ENABLED = False` process-wide, so no test exercises the RSS path *through* the limiter. |
| 6 | Any yfinance sustainability score lands in `external_esg_score` and nowhere else | ? VACUOUS | `external_esg_score` is NULL for both tickers; `Ticker.sustainability` still 404s. Correct by inspection, undemonstrated by data. Unchanged, and not caused by the source switch. |

**Score: 5/6 verified (1 by override), 1 vacuous, 0 failed.** Was 3/6 with 2 failures.

## Does swapping the primary source invalidate anything verified under GDELT?

I looked for this specifically, because a source swap is exactly where previously-verified
properties get silently dropped.

- **SC3 never-raise** — re-verified under RSS, not inherited. The total-failure-must-be-`failed`
  rule (N2) was re-implemented in `rss.py` with its own test rather than being lost with GDELT.
- **SC5 rate limiting** — strengthened, not weakened. The shared bucket and deterministic host
  resolution are host-agnostic; the two new RSS hosts were added to `sources.yaml` with rates.
- **SC2 dedup** — the mechanism is unchanged and re-proven on a 16x larger corpus. But the corpus
  is now multi-feed, which *exposes* a dedup weakness that a single-source GDELT corpus could not
  (R-2). The swap did not break dedup; it revealed its boundary.
- **SC4 fixtures** — improved. The GDELT tests that were the N3 defect were not deleted; they were
  supplemented with real captured rejection bodies and kept working behind the flag.
- **COLL-03** — this is the one thing genuinely invalidated. The GDELT collector still passes its
  tests and still cannot collect. Code-satisfied, operationally dead, opt-in. Requirement must be
  rewritten rather than quietly reinterpreted.

## New Risks Introduced by RSS — what I checked

| Concern | Finding |
|---|---|
| Never-raise under RSS | **Holds.** Deliberate `RuntimeError` on total refusal is caught by `safe_fetch`; `fetch` is never called directly anywhere in the codebase. |
| content_hash collision across feeds | **Real, quantified — R-2.** 7/227 rows (3.1%) are same-ticker duplicate titles with distinct hashes. No cross-ticker duplicates. |
| Yahoo feed has no ESG filter | **Confirmed, 35 of 227 rows (15%) carry no pillar tag** (`raw_json IS NULL`), and `finance.yahoo.com` is the single largest domain at 52 rows. The Phase 2 relevance gate carries this load, as the ADR states. Accepted by design, not a defect. |
| IR / press-release domains vs tier-4 list | **Partly a non-issue — R-5.** `businesswire.com`, `prnewswire.com`, `globenewswire.com`, `prweb.com`, `accesswire.com` are all already tier 4. Only issuer IR subdomains leak: `investor.exxonmobil.com`, 2 documents, currently tier 3. |
| A test asserting a contract live RSS refuses | **None found.** Fixtures are live captures. The nearest thing is `conftest.py` setting `HTTP_MAX_RETRY_ATTEMPTS = 1` and disabling the limiter globally, so the mocked 429/503 tests never exercise retry-under-RSS — an untested path, not a false contract. |
| Query construction quality | **Weakest link in the new design.** `_primary_company` picks the *longest* alias, so XOM queries the exact phrase `"ExxonMobil Holdings Corporation"` — a name publishers do not use. Result: 22 XOM documents across all three pillars in 90 days vs AAPL's 170, and the XOM E-pillar hits are largely non-ESG corporate news. Routed to human judgement rather than called a defect, because recall is unmeasurable pre-baseline. |

## Behavioural checks I ran

| Check | Command | Result | Status |
|---|---|---|---|
| Suite is green as claimed | `PYTHONPATH=src pytest -q` | `99 passed, 17 skipped in 6.71s` | ✓ PASS |
| Skips are Phase 3 only | `pytest -q -rs` | all 17 from `test_scoring_fixture.py` | ✓ PASS |
| Corpus composition | `sqlite3` group by source, doc_type | `rss/news 227`, `edgar/filing_section 15` | ✓ PASS |
| No null domain/title/url | `SELECT count(*) ... WHERE ... IS NULL` | `0` | ✓ PASS |
| No Google redirect leakage | `WHERE domain LIKE '%news.google.com%'` | `0` | ✓ PASS |
| N1 is real | `SELECT ticker,source,status,n_fetched,n_new FROM collection_runs` | re-run rows `186→0`, `41→0`, `12→0`, `3→0` | ✓ PASS |
| Cross-feed duplicates | group by ticker, lower(title) having count>1 | 7 rows, each with 2 distinct content_hashes | ✗ FINDING R-2 |
| XOM query shape | `build_pillar_query` with live config | 104–116 chars, exact-phrase on an unused legal name | ⚠ routed to human |

## Can Phase 2 Proceed?

**Yes.** The 2026-09-07 "no" rested on three arguments; two are now dead and the third has flipped.

1. *"Every gate Phase 2 builds is news-shaped and there is no news."* — 227 news headlines across
   two tickers and 90 days. The entity gate now has something to reject: the Yahoo arm is
   entity-guaranteed, the Google arm is not, and the corpus already contains
   `"ExxonMobil Holdings Corporation (XOM) vs. Chevron Corporation (CVX)"` — a real multi-entity
   headline, not a fixture.
2. *"Phase 3 inherits an EDGAR-only corpus and every pillar returns insufficient_data."* — dead.
   `min_evidence` is 1.0; even XOM's thinnest pillar (G, 5 documents) clears it before weighting.
   AAPL is comfortable at 34/78/58.
3. *"Fix the collector now, it is cheaper than after Phase 2."* — this has inverted. The remaining
   open questions (R-2 dedup strength, the query-recall question, tier-4 IR subdomains) are all
   questions Phase 2 is better placed to answer than Phase 1, because they need a relevance gate
   and a scored baseline to judge against. Holding Phase 1 open to guess at them is now the
   expensive option.

Carry R-1, R-2 and R-5 into the Phase 2 context as known inputs. R-3 (the missing N5 test and the
grep-as-coverage regression) should be closed as cheap cleanup, not as a gate.

## Required Before Phase 1 Is Marked Closed

Bookkeeping, not engineering:

1. Amend **REQUIREMENTS.md COLL-03** to name RSS as the primary news source with GDELT retained
   behind `ESG_ENABLE_GDELT=1`, and amend **ROADMAP Phase 1 Success Criterion 1** likewise. The
   traceability table still marks COLL-01..07 `Pending` — mark them.
2. Fix or remove `--since` for RSS and EDGAR, or document it as GDELT-only (R-1).
3. Add the behavioural shared-bucket test that the 2026-09-07 report asked for (R-3).

---

_Re-verified: 2026-09-14_
_Verifier: Claude (gsd-verifier)_

---
---

# SUPERSEDED — original verification, 2026-09-07 (`gaps_found`, 3/6)

_Preserved verbatim. Its structured `gaps:` frontmatter is reproduced below for history; the
authoritative frontmatter for this phase is the one at the top of this file._

```yaml
phase: 01-collectors
verified: 2026-09-07T00:00:00Z
status: gaps_found
score: 3/6 success criteria verified (1 vacuous, 2 failed)
requirements:
  COLL-01: satisfied (with defect N5)
  COLL-02: satisfied (with defect N2)
  COLL-03: failed
  COLL-04: satisfied
  COLL-05: partial (unverifiable — upstream 404)
  COLL-06: satisfied
  COLL-07: satisfied (with defect N1)
gaps:
  - truth: "backfill --tickers AAPL,XOM populates raw_documents with GDELT news and EDGAR filing sections"
    status: failed
    reason: "GDELT contributes zero rows. raw_documents contains 15 rows, all source=edgar, doc_type=filing_section. The primary news source — the project's stated differentiator — yields nothing."
    artifacts:
      - path: "src/esg_lens/collectors/gdelt.py"
        issue: "build_gdelt_queries emits an alias OR-group crossed with the full D-01 ESG bundle (scoring category keys + lexicon tiers 1-3), halved at 400 chars. GDELT answers HTTP 200 text/html 'Your query was too short or too long.' Construction matches the spec; the spec produces unservable queries."
      - path: "tests/integration/test_gdelt.py"
        issue: "Every test mocks a 200 JSON body. The fixture encodes a contract the live API rejects, so the suite is green while the collector collects nothing. No test bounds query length or term count against what GDELT will actually serve."
    missing:
      - "Redesign D-01/D-02/D-03: far fewer ESG terms per request, more requests, wider spacing; validate against the live endpoint before re-fixturing"
      - "A test that pins the accepted query shape (term count / encoded length) rather than a hand-authored happy-path JSON body"
  - truth: "A source that collects nothing is visible to the operator"
    status: failed
    reason: "Since the UAT JSON-parse guard, a rejected GDELT query is skipped with a warning and the collector returns []. safe_fetch then records status='ok', n_fetched=0. collection_runs now shows XOM|gdelt|ok|0|0 — total failure recorded as success. backfill.py always exits 0 and prints 'Backfill complete: fetched=15 new=15'."
    artifacts:
      - path: "src/esg_lens/collectors/gdelt.py"
        issue: "gdelt_non_200_skipped / gdelt_non_json_body_skipped continue the loop; no partial/failed status is propagated"
      - path: "scripts/backfill.py"
        issue: "no non-zero exit, no per-source summary, no escalation when an enabled source returns 0 across all tickers"
    missing:
      - "Propagate a 'partial'/'failed' collection_runs status when chunks are skipped (the CHECK already allows 'partial')"
      - "Per-source summary line and non-zero exit when an enabled source returns zero for every ticker"
  - truth: "collection_runs reports how many documents were newly persisted"
    status: failed
    reason: "NEW — not caught by the UAT. base.safe_fetch writes n_new=len(docs), i.e. the fetched count, for every run. The second backfill re-ran EDGAR and inserted zero rows, yet collection_runs shows AAPL|edgar|ok|12|12. n_new is a duplicate of n_fetched and never reflects insertion."
    artifacts:
      - path: "src/esg_lens/collectors/base.py"
        issue: "self._write_run(..., len(docs), len(docs), ...) — collectors do not own persistence, so they cannot know n_new; backfill computes the real n_new locally and only logs it"
    missing:
      - "Have the persisting caller update the collection_runs row with the true n_new, or return the run id from safe_fetch for the caller to update"
  - truth: "Rate limiting cannot be circumvented within the process"
    status: failed
    reason: "NEW — not caught by the UAT. AsyncHttpClient builds two independent TokenBucketTransport instances (cached client and force_refresh bypass client), each with its own per-host bucket dict. A force_refresh run therefore gets a second full budget for the same host, so observed rates can reach 2x the configured limit. This is the exact failure the SEC-blocks-the-IP risk is guarding against."
    artifacts:
      - path: "src/esg_lens/collectors/http.py"
        issue: "self._token_transport and the bypass client's TokenBucketTransport do not share bucket state"
    missing:
      - "One shared TokenBucketTransport (or shared bucket dict) behind both clients"
      - "A behavioural test asserting cached + force_refresh requests share one budget"
  - truth: "A throttled source cannot stall the run"
    status: partial
    reason: "Confirmed in code (UAT G-3). _RETRY_STATUS includes 429, retried 5 times with 2-10s exponential waits, per chunk, per ticker, with no wall-clock budget. Compounded by the token bucket: capacity is initialised to rate, so at 0.2/s tokens never exceed 0.2 and every single request pays a ~4s wait before it is even issued. The UAT re-run had to be killed after 10+ minutes."
    artifacts:
      - path: "src/esg_lens/collectors/http.py"
        issue: "429 treated as an ordinary retryable status; no per-collector deadline; bucket capacity == rate"
    missing:
      - "Per-collector wall-clock budget"
      - "Distinct 429 handling (honour the stated cooldown, cap attempts) separate from 5xx backoff"
human_verification:
  - test: "Re-run backfill for AAPL/XOM after the GDELT query redesign and count raw_documents by source"
    expected: "source=gdelt rows > 0 for both tickers"
    why_human: "Requires a live GDELT call; the fixtures cannot demonstrate the API accepts the query"
  - test: "Re-check Ticker.sustainability for AAPL/XOM once Yahoo serves data again"
    expected: "companies.external_esg_score populated; no esg_signals or scoring path reads it"
    why_human: "Upstream returns 404 today; the isolation claim is vacuous until data exists"
---
```


# Phase 1: Collectors Verification Report

**Phase Goal:** Free public data flows into `raw_documents` reliably and repeatably — rate-limited, cached, deduplicated, and never able to crash the process.
**Verified:** 2026-09-07
**Status:** gaps_found — **Phase 1 is not done**
**Re-verification:** No — initial verification, following the 2026-09-06/07 UAT.

## Verdict

The goal is **not achieved**. Half the sentence is true: what does arrive is deduplicated,
rate-limited, cached, and cannot crash the process. The first half is not: free public data does
not flow. Two of the three sources are working; the one that carries the project's entire
premise — daily news signal — delivers zero documents, and the system reports that as success.

`raw_documents` contains 15 rows. All 15 are EDGAR 10-K Item 1/Item 1A sections. `source=gdelt`
count is 0. A collectors phase whose news collector collects nothing has built the plumbing and
not the supply.

## Goal Achievement — ROADMAP Success Criteria

| # | Success criterion | Status | Evidence |
|---|---|---|---|
| 1 | backfill populates `raw_documents` with GDELT news **and** EDGAR sections; populates `companies` + `company_aliases` | ✗ FAILED | `select source,count(*) from raw_documents` → `edgar\|filing_section\|15`. Zero GDELT. `companies` (AAPL/320193, XOM/2115436) and 5 `company_aliases` rows are correct. Half the criterion, and the half that matters least for signal. |
| 2 | Re-running adds zero new rows — `content_hash` dedup works | ✓ VERIFIED (with defect) | 15 → 15, delta 0. `content_hash` = sha256(lower title \| external_id) in `base.content_hash`; `DocumentRepo.insert` is INSERT OR IGNORE on the UNIQUE. But `collection_runs.n_new` lied about it — see N1. |
| 3 | Forced failure → `collection_runs` row + empty list, no exception escapes | ✓ VERIFIED (with regression) | `safe_fetch` catches, logs, writes status=failed, returns `[]`; asserted by `test_safe_fetch_never_raises_and_writes_failed_row` and observed live (the 429 row is in the DB). Regression: since the UAT guard, a *rejected* GDELT query is now recorded as `ok/0` rather than `failed` — N2. |
| 4 | Integration tests pass against recorded fixtures with `respx`, no live network | ✓ VERIFIED (low fidelity) | 71 passed, 17 skipped, no network. But the GDELT fixtures mock a 200 JSON body, so they assert a contract the live API refuses — N3. |
| 5 | EDGAR UA carries a contact email; rates within 10/s EDGAR, 1/s GDELT | ✓ VERIFIED (with defect) | `AsyncHttpClient.__init__` raises without `@`; the config validator now rejects placeholder domains; `test_rate_limit_behaviour.py` asserts real timings and per-host buckets. GDELT corrected to 0.2/s in `config/sources.yaml`. Defect: two independent bucket sets — N5. |
| 6 | Any yfinance sustainability score lands in `external_esg_score` and nowhere else | ? VACUOUS | `external_esg_score` appears only in `yfinance_meta.py` and the `companies` table. But `Ticker.sustainability` 404s for both tickers, `external_esg_score` is NULL, so there is nothing to isolate. Correct by inspection, undemonstrated by data. |

**Score: 3/6 verified, 1 vacuous, 2 failed.**

## Requirements Coverage

| Req | Status | Evidence |
|---|---|---|
| COLL-01 shared client, UA, buckets, tenacity, hishel 24h + force_refresh | ⚠️ SATISFIED with defect | `http.py` has all five. `force_refresh` uses a bypass client with a *second* bucket set (N5). |
| COLL-02 `Collector` ABC, never raises | ⚠️ SATISFIED with defect | Contract holds and is behaviourally tested. Status fidelity regressed (N2). |
| COLL-03 GDELT DOC 2.1 alias OR-group + ESG bundle | ✗ **FAILED** | The code is written to spec and returns nothing. A collector that collects zero documents does not satisfy a collector requirement, however faithful the construction. |
| COLL-04 EDGAR CIK → 10-K/8-K/DEF 14A, Item 1/1A only | ✓ SATISFIED | 15 rows, `filing_section` constrained to Item 1/Item 1A in `RawDocument.__post_init__`; CIKs resolved and now persisted. |
| COLL-05 yfinance metadata + alias seeding; sustainability → `external_esg_score` only | ⚠️ PARTIAL | Metadata and aliases work. The isolation clause is unverifiable — upstream 404. |
| COLL-06 NewsAPI implemented, `enabled: false` | ✓ SATISFIED | `test_newsapi_disabled_returns_empty_without_network` asserts `docs == []` and `route.call_count == 0`. |
| COLL-07 dedup on `content_hash` — re-run adds zero rows | ⚠️ SATISFIED with defect | Behaviour correct; reporting wrong (N1). |

## Findings Not Covered by the UAT

| # | Finding | Severity | Location |
|---|---|---|---|
| N1 | `collection_runs.n_new` is set to `len(docs)`, never the insert count. Re-run rows read `12/12` while inserting 0. Phase 4 job reporting would inherit this lie. | HIGH | `src/esg_lens/collectors/base.py` `safe_fetch` |
| N2 | The UAT's JSON-parse guard **downgraded** GDELT rejection from `status=failed` to `status=ok, n_fetched=0`. The DB now literally records total failure as success (`XOM\|gdelt\|ok\|0\|0`). This makes G-2 strictly worse, not better. | HIGH | `src/esg_lens/collectors/gdelt.py` |
| N3 | GDELT integration tests mock 200 JSON. The fixture asserts a contract the live API rejects; the suite can never go red on this defect. | HIGH | `tests/integration/test_gdelt.py` |
| N4 | Token bucket capacity is initialised to `rate`, so at 0.2/s it never holds a whole token and every request pays a ~4s pre-wait. Combined with 429 in `_RETRY_STATUS` and no deadline, this is the mechanical cause of G-3's 10-minute stall. | MEDIUM | `src/esg_lens/collectors/http.py` |
| N5 | Cached client and `force_refresh` bypass client hold independent per-host buckets → up to 2x the configured rate. Directly undermines the "SEC blocks the IP" mitigation and success criterion 5. | MEDIUM | `src/esg_lens/collectors/http.py` |
| N6 | `_get_rate_for_host` matches with `host.endswith(key) or key.endswith(host) or key in host` over an unordered dict — a permissive fuzzy match that could resolve an unrelated host to the wrong limit. | LOW | `src/esg_lens/collectors/http.py` |

Handoff §4 traps applicable to Phase 1 — EDGAR User-Agent — is honoured (and the placeholder-email
hole was closed during the UAT). The remaining traps belong to Phases 2–4.

## Can Phase 2 Proceed?

**No — and the argument for "yes" is worth stating so it can be rejected explicitly.**

The case for proceeding: Phase 2's own definition of done is fixture-based — 50 fixture headlines,
stub models, wiring tests. None of that needs live GDELT. Phase 2 could be written and go green
tomorrow.

Why that is not good enough:

1. Every gate Phase 2 builds is news-shaped. The entity gate exists to reject "Apple the fruit" —
   a headline problem that 10-K Item 1 text does not have. The controversy lexicon is tuned to
   incident reporting; filings do not narrate their own scandals. Sentiment on boilerplate risk
   prose is close to meaningless. Green stub tests would prove wiring while the gates go
   unexercised against the input class they exist for.
2. Phase 3 inherits an EDGAR-only corpus: one source tier (0.70), annual recency, self-reported
   tone. `Σ w_ev` would sit near the sufficiency floor and most pillars would return
   `insufficient_data`. The §8 acceptance fixture would still pass — it is synthetic — but the
   first real end-to-end score would be null or near-null, and that would be discovered two phases
   downstream.
3. The GDELT fix is a query-strategy redesign (D-01/D-02/D-03), not a patch. Doing it after
   Phase 2 means re-opening the collector, re-recording fixtures, and re-validating the NLP
   pipeline against a document class it has never seen. Cheaper now.

The UAT's own conclusion — "do not start Phase 2 on top of this" — is correct and this
verification does not soften it.

## Required Before Phase 1 Closes

1. Redesign the GDELT query strategy until a live run puts `source=gdelt` rows in `raw_documents`
   for AAPL and XOM (COLL-03, SC1).
2. Make a zero-document source loud: `partial`/`failed` status on skipped chunks, per-source
   summary, non-zero exit (N2, G-2).
3. Fix `collection_runs.n_new` to mean what the schema says (N1).
4. Share one token bucket across the cached and bypass clients (N5), and give collectors a
   wall-clock budget with distinct 429 handling (N4, G-3).
5. Replace or supplement the GDELT happy-path fixtures with a test that constrains the query to a
   shape GDELT will serve (N3), and convert or delete the remaining grep-as-coverage tests (G-4).

Items 1 and 2 are blocking. Items 3–5 are cheap and should ride along in the same rework.

---

_Verified: 2026-09-07_
_Verifier: Claude (gsd-verifier)_
