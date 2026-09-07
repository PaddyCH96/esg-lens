---
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
