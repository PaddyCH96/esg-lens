---
phase: 01-collectors
plan: "01-01"
subsystem: collectors
tags: [httpx, hishel, tenacity, token-bucket, sqlite, structlog, respx]
requires:
  - phase: 00-scaffold
    provides: [config.py USER_AGENT validation, sources.yaml rate_limits, schema.sql collection_runs DDL, db engine/repositories]
provides:
  - AsyncHttpClient with token bucket, tenacity 429/5xx retry, hishel 24h forced caching and force_refresh bypass
  - Collector ABC, RawDocument dataclass, content_hash helper, collection_runs never-raise writer
  - Wave 0 fixtures for GDELT, EDGAR tickers/submissions/10-K, yfinance info
  - Unit suites proving hishel_from_cache spike and never-raise contract
affects: [01-02 gdelt collector, 01-03 edgar and yfinance collectors, backfill orchestration]
tech-stack:
  added: [hishel, anysqlite]
  patterns: [per-host token bucket via time.monotonic + asyncio.sleep, tenacity AsyncRetrying with retry_if_exception, hishel FilterPolicy forced caching for header-less 200s, structlog before_sleep_log, parameterised collection_runs inserts]
key-files:
  created: [src/esg_lens/collectors/base.py, src/esg_lens/collectors/__init__.py, tests/unit/test_http_client.py, tests/unit/test_collector_base.py, tests/fixtures/gdelt_sample.json, tests/fixtures/edgar_company_tickers.json, tests/fixtures/edgar_submissions_AAPL.json, tests/fixtures/edgar_10k_item1_excerpt.html, tests/fixtures/yfinance_AAPL_info.json]
  modified: [src/esg_lens/collectors/http.py, tests/conftest.py]
key-decisions:
  - "Hishel FilterPolicy with AsyncSqliteStorage default_ttl 24h to force-cache 200s despite missing Cache-Control (SEC/GDELT omit headers) — SpecificationPolicy would store nothing"
  - "TokenBucketTransport as httpx.AsyncBaseTransport with per-host buckets replenished via time.monotonic, wrapping AsyncHTTPTransport inside AsyncCacheTransport so cache hits bypass rate limit"
  - "Force_refresh bypass handled at client layer via separate bypass client and Cache-Control: no-cache header, since FilterPolicy does not respect no-cache by itself"
  - "collection_runs FK fallback: on IntegrityError retry with job_id None to preserve never-raise contract even when caller passes unknown job_id"
patterns-established:
  - "Shared AsyncHttpClient is the only httpx client collectors use — get_http_client singleton"
  - "Collector.safe_fetch writes collection_runs with ? placeholders, truncates error to 1000, never raises"
  - "content_hash = sha256(lower(stripped title) | coalesce(external_id, url))"
requirements-completed: [COLL-01, COLL-02]
duration: 12min
completed: 2026-09-03
---

# Phase 01 Plan 01: Collectors Foundation Summary

**Shared httpx client with per-host token buckets (10/s EDGAR, 1/s GDELT), tenacity 429/5xx retry, hishel 24h forced caching, plus Collector ABC never-raise contract with collection_runs**

## Performance

- **Duration:** 12 min
- **Started:** 2026-09-03T19:10:00Z
- **Completed:** 2026-09-03T19:22:00Z
- **Tasks:** 2
- **Files modified:** 11

## Accomplishments

- Replaced hand-rolled JSON file cache and per-host lock with hishel AsyncCacheTransport + AsyncSqliteStorage + FilterPolicy that stores every 200 for 24h regardless of missing Cache-Control headers — spike proves second identical GET returns `hishel_from_cache True`
- Token bucket per host reading limits from `config/sources.yaml` (`sec.gov: 10`, `api.gdeltproject.org: 1`) via `time.monotonic` and `asyncio.sleep`, keyed by `request.url.host`
- Tenacity `AsyncRetrying` with `stop_after_attempt 5`, `wait_exponential multiplier 1 min 2 max 10`, retry predicate only on `429/500,502,503,504` and `RequestError/ConnectError/ReadTimeout` with `before_sleep_log` via structlog — does not retry 400/401/403/404
- `force_refresh` bypass via `Cache-Control: no-cache` header and separate bypass client
- Collector ABC with `RawDocument` dataclass (CHECKs for source/doc_type, filing_type/section, ISO-8601 `published_at`), standalone `content_hash` per `data_model.md`, and `safe_fetch` that always writes `collection_runs` with `?` placeholders, truncates error to 1000 chars, and returns `[]` never raises
- Wave 0 fixtures and extended `conftest` with `hishel_temp_storage` fixture

## Task Commits

Each task was committed atomically:

1. **Task 1: Replace HttpClient with token bucket + tenacity + hishel forced caching** - `34aed79` (feat)
2. **Task 2: Create Collector ABC, RawDocument, content_hash, collection_runs helper and Wave 0 scaffolding** - `f369513` (feat)

**Plan metadata:** `pending` (docs: complete plan)

## Files Created/Modified

- `src/esg_lens/collectors/http.py` - AsyncHttpClient with TokenBucketTransport, tenacity retry, hishel FilterPolicy + AsyncSqliteStorage 24h, UA @ validation, force_refresh
- `src/esg_lens/collectors/__init__.py` - re-exports AsyncHttpClient and get_http_client
- `src/esg_lens/collectors/base.py` - RawDocument dataclass, content_hash, Collector ABC with safe_fetch and _write_run parameterised inserts
- `tests/conftest.py` - added hishel_temp_storage fixture (AsyncSqliteStorage with default_ttl 24h)
- `tests/fixtures/gdelt_sample.json` - recorded GDELT DOC 2.1 artlist with articles, seendate, domain, sourcecountry
- `tests/fixtures/edgar_company_tickers.json` - dict keyed "0" with cik_str/ticker/title for AAPL etc.
- `tests/fixtures/edgar_submissions_AAPL.json` - filings.recent.form array with 10-K/10-Q/DEF 14A
- `tests/fixtures/edgar_10k_item1_excerpt.html` - HTML excerpt containing ITEM 1 and ITEM 1A
- `tests/fixtures/yfinance_AAPL_info.json` - Ticker.info plus sustainability totalEsg
- `tests/unit/test_http_client.py` - UA @, rate limit lookup, retry predicate, token bucket existence, forced cache spike (hishel_from_cache), force_refresh bypass
- `tests/unit/test_collector_base.py` - content_hash spec, never-raise ok/failed, collection_runs row verification, truncation, parameterised SQL

## Decisions Made

- Use `FilterPolicy` over `SpecificationPolicy` because SEC/GDELT omit `Cache-Control` — Spec policy would never cache; FilterPolicy with `_Only200Filter` forces 24h storage of 200s via `AsyncSqliteStorage(default_ttl=86400)`
- Nest `TokenBucketTransport` inside `AsyncCacheTransport` so cache hits do not consume rate-limit tokens; bypass client re-uses same rate logic for `force_refresh`
- Handle `collection_runs.job_id` FK failures by retrying with `NULL` to keep never-raise invariant even when tests or callers pass unknown job_ids — inserted jobs in tests to verify correct path preserves job_id

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Installed missing anysqlite dependency**
- **Found during:** Task 1 (AsyncSqliteStorage init)
- **Issue:** `hishel` requires `anysqlite` via `hishel[async]` extra; `AsyncSqliteStorage` raised `ImportError: anysqlite required`
- **Fix:** `pip install anysqlite` and `hishel[httpx]` ; storage now initializes with `AsyncSqliteStorage(database_path=..., default_ttl=86400)`
- **Files modified:** none (environment)
- **Verification:** `AsyncHttpClient(cache_dir='/tmp/...')` init succeeds
- **Committed in:** 34aed79 (Task 1 commit)

**2. [Rule 1 - Bug] Fixed collection_runs FK constraint failure on unknown job_id**
- **Found during:** Task 2 (test_safe_fetch with job_id="job1" no jobs row)
- **Issue:** `collection_runs.job_id REFERENCES jobs(id)` caused `IntegrityError: FOREIGN KEY constraint failed` when `safe_fetch` wrote with a job_id that had no `jobs` row, breaking never-raise contract
- **Fix:** Wrapped `_write_run` in try/except `IntegrityError` retry with `job_id=None`; updated tests to insert `jobs` rows so happy-path preserves job_id, fallback preserves never-raise
- **Files modified:** src/esg_lens/collectors/base.py, tests/unit/test_collector_base.py
- **Verification:** `PYTHONPATH=src pytest tests/unit/test_collector_base.py -q` passes (2 previously failed now pass)
- **Committed in:** f369513 (Task 2 commit)

**3. [Rule 2 - Missing Critical] Added hishel default_ttl and Only200 filter**
- **Found during:** Task 1 design review
- **Issue:** Plan requires 24h TTL and forced caching of 200s; default hishel storage has no TTL and FilterPolicy without filter would cache errors
- **Fix:** Set `AsyncSqliteStorage(default_ttl=24*3600)` and `FilterPolicy(response_filters=[_Only200Filter()])` where `_Only200Filter` returns `status_code==200`
- **Files modified:** src/esg_lens/collectors/http.py
- **Verification:** `test_forced_cache_hishel_from_cache_true_on_second_request` passes with no Cache-Control header
- **Committed in:** 34aed79

---

**Total deviations:** 3 auto-fixed (1 blocking, 1 bug, 1 missing critical)
**Impact on plan:** All auto-fixes essential for correctness and verification; no scope creep.

## Issues Encountered

- `pytest-asyncio` not installed in env caused initial async test failures — installed `pytest-asyncio` and `respx` to enable `asyncio_mode=auto` and httpx mocking
- Hishel `FilterPolicy` without TTL would store forever; verified `_is_pair_expired` in `AsyncSqliteStorage` respects `default_ttl` so entries expire after 24h

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Ready for 01-02 (GDELT collector) — it can import `get_http_client`, `Collector`, `RawDocument`, `content_hash` and reuse `hishel_temp_storage` + Wave 0 fixtures in integration tests
- Ready for 01-03 (EDGAR + yfinance) — CIK cache and 10-K HTML split can be tested against `edgar_*` fixtures
- No blockers; full suite green: `PYTHONPATH=src pytest -q` 24 passed

---
*Phase: 01-collectors*
*Completed: 2026-09-03*

## Self-Check: PASSED

- [x] src/esg_lens/collectors/http.py contains AsyncCacheTransport and AsyncSqliteStorage and TokenBucketTransport and AsyncRetrying and FilterPolicy
- [x] src/esg_lens/collectors/http.py contains settings.USER_AGENT and no hardcoded UA without @ (validator present)
- [x] src/esg_lens/collectors/http.py contains rate limit lookup from sources.yaml and no asyncio.Lock per host as sole limiter (0 code uses)
- [x] src/esg_lens/collectors/http.py contains retry predicate 429 and 500,502,503,504 not retrying 400/401/403/404
- [x] src/esg_lens/collectors/http.py contains force_refresh via Cache-Control no-cache
- [x] src/esg_lens/collectors/__init__.py contains AsyncHttpClient and get_http_client
- [x] src/esg_lens/collectors/base.py contains class Collector, class RawDocument, def content_hash, def safe_fetch, INSERT INTO collection_runs with ? placeholders
- [x] src/esg_lens/collectors/base.py contains except Exception returning [] and structlog get_logger
- [x] tests/fixtures/gdelt_sample.json exists with articles and seendate
- [x] tests/fixtures/edgar_company_tickers.json exists with AAPL cik_str
- [x] tests/fixtures/edgar_submissions_AAPL.json exists with filings.recent.form
- [x] tests/fixtures/edgar_10k_item1_excerpt.html exists with ITEM 1A
- [x] PYTHONPATH=src pytest tests/unit/test_http_client.py tests/unit/test_collector_base.py -q passes
- [x] Second identical GET returns hishel_from_cache True verified
- [x] PYTHONPATH=src pytest -q full suite green
- [x] grep -r hishel_from_cache tests/unit/test_http_client.py succeeds
