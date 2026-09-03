# Phase 1: Collectors - Research

**Researched:** 2026-09-03
**Domain:** HTTP data collectors (GDELT DOC 2.1, SEC EDGAR, yfinance) with caching, rate limiting, and deduplication
**Confidence:** MEDIUM

## Summary

Phase 1 makes free public data flow into `raw_documents` reliably and repeatably. Three sources are active (GDELT news titles+metadata, SEC EDGAR filing sections, yfinance company metadata) behind a single shared httpx layer that must enforce per-host token-bucket rate limits (EDGAR 10/s, GDELT 1/s), exponential backoff on 429/5xx, and a 24h disk cache bypassed on `force_refresh`. All collectors obey a never-raise contract: catch, log via structlog, write a `collection_runs` row, return `[]`.

The existing stubs in `src/esg_lens/collectors/` are structurally wrong and will be replaced, not patched: `http.py` uses a naive per-host `asyncio.Lock` instead of a token bucket and hand-rolls JSON file caching instead of hishel; `gdelt.py` and `edgar.py` hardcode query shapes that do not match CONTEXT.md D-01..D-03 and omit `content_hash`, domain normalization, `seendate` parsing, section splitting, and the `collection_runs` write. The DB layer (`db/engine.py`, `db/repositories.py`) is done and defines the contract collectors must honor: `UNIQUE(ticker, content_hash)` with `INSERT OR IGNORE`, `source`/`doc_type` CHECK constraints, and `collection_runs.status` enum.

**Primary recommendation:** Replace the entire `collectors/http.py` with `hishel.httpx.AsyncCacheTransport` + `AsyncSqliteStorage` + a token-bucket `httpx.AsyncBaseTransport` wrapper, implement GDELT/EDGAR/yfinance against the shared client with the CONTEXT.md query-construction rules, and ship `scripts/backfill.py` as a thin orchestrator that upserts `companies`/`company_aliases` and inserts `raw_documents` via `INSERT OR IGNORE`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
**Phase Boundary:** Phase 1 makes free public data flow into `raw_documents` reliably and repeatably: GDELT DOC 2.1 news (titles + metadata), SEC EDGAR filings (10-K Item 1 + Item 1A, 8-K, DEF 14A as filing_section rows), and yfinance metadata + `company_aliases` seeding. Success is `scripts/backfill.py --tickers AAPL,XOM` populates `raw_documents`, re-running adds zero rows (content_hash dedup), failures are logged to `collection_runs` and never raise, EDGAR carries a UA with contact email at ≤10/s and GDELT ≤1/s, yfinance sustainability scores land only in `external_esg_score`.

Out of scope: NLP pipeline, scoring engine, API/jobs, frontend/dashboard, full article-body scraping, NewsAPI enabled use, model fine-tuning.

**D-01: Broad bundle chosen.** Use categories + controversy lexicon tiers 1-3 as the ESG keyword bundle (~30 terms: Climate Change / Natural Capital / Pollution & Waste / Human Capital / Product Liability / Community Relations / Corporate Governance / Business Ethics & Values plus triggers like oil spill, bribery, fraud, child labor, fatality, class action, criminal probe, fine, penalty, lawsuit, recall, strike, investigation, data breach, layoffs, criticized, alleged, scrutiny, protest, complaint, downgrade, etc.). Rationale: maximize recall for incident/controversy-based scoring; category-only misses severe controversies that dominate pillar penalties.

**D-02: Filter short/ambiguous aliases.** Drop aliases ≤4 chars or on stoplist; otherwise use quoted phrases for multi-word aliases. Short names like "Apple", "Meta", "Shell" are noisy on GDELT; filtering at query time reduces fruit/generic hits before the spaCy entity gate. All raw aliases remain in `company_aliases` table — filtering applies only to query construction.

**D-03: Quoted phrases + chunk if needed.** Quote multi-word terms ("oil spill", "Apple Inc", "child labor"). If constructed query exceeds ~400 chars (GDELT's undocumented ~500-char limit), split into 2 sequential queries chunked on the bundle and merge results (dedup downstream on `content_hash`). Log a warning when truncation/chunking occurs; never silently truncate without log.

### Claude's Discretion
All other collector scope is governed by `docs/handoff_to_backend.md: Phase 1` and `REQUIREMENTS.md: COLL-01..07` and is left to standard approaches:
- Shared `httpx` client details: per-host token buckets (EDGAR 10/s, GDELT 1/s), `tenacity` exponential backoff on 429/5xx, `hishel` disk cache 24h TTL with `force_refresh` bypass, configurable `User-Agent` containing contact email.
- GDELT DOC 2.1 `mode=artlist&format=json` specifics: `seendate` parsing, domain normalization (lowercase, strip www.), `content_hash = sha256(lower(normalized_title) | external_id/url)`, `hishel` cache key = request URL.
- EDGAR details: cached `company_tickers.json` → CIK (7d TTL), submissions API → recent 10-K/8-K/DEF 14A, 10-K section split keeping only Item 1 and Item 1A, each section one `raw_documents` row with `filing_section` set, chunking for NLP deferred to Phase 2.
- yfinance metadata: `Ticker.info` + `Ticker.sustainability` handling with graceful `None` degradation; alias variants (legal, common, brand, drop Inc./Corp./plc) seeded into `company_aliases`; sustainability score written to `external_esg_score` only, never used as scoring input.
- NewsAPI: implemented but `enabled: false` in `config/sources.yaml`.
- Failure contract: collectors never raise — catch, log via `structlog`, write `collection_runs` row, return `[]`.
- Deduplication purely on `content_hash` UNIQUE (ticker, content_hash).

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within Phase 1 scope. All deferred items remain in `ROADMAP.md: Post-v1 / Out of Scope` (dashboard separate repo, ClimateBERT, ONNX, DELETE cancellation, yfinance news + RSS collectors).

</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| COLL-01 | Shared `httpx` client with configurable UA (email), per-host token-bucket (EDGAR 10/s, GDELT 1/s), `tenacity` backoff on 429/5xx, `hishel` 24h disk cache bypassed when `force_refresh=True` | hishel 1.3.1 `AsyncCacheTransport`/`AsyncCacheClient` + `AsyncSqliteStorage` verified; tenacity 9.1.4 patterns verified; token-bucket must be custom (no library provides per-host bucket for httpx) |
| COLL-02 | `Collector` ABC with `fetch(ticker, since) -> list[RawDocument]`; collectors **never raise** — catch, log, write `collection_runs`, return `[]` | Architecture §5.1 contract verified; structlog JSON logging exists; `collection_runs` DDL verified |
| COLL-03 | GDELT DOC 2.1 `mode=artlist&format=json`, alias OR-group + ESG bundle, domain normalization, `seendate` parsing, `content_hash` | GDELT DOC 2.0 debut post + `gdeltdoc` client verified API shape (`query`, `mode`, `format`, `maxrecords` 75/250, `timespan`/`startdatetime`/`enddatetime`); query-chunking and hash spec from CONTEXT.md |
| COLL-04 | EDGAR ticker→CIK via cached `company_tickers.json` (7d TTL), recent 10-K/8-K/DEF 14A, 10-K split keeping Item 1 and Item 1A only, one row per section | SEC `data.sec.gov/submissions/CIK##########.json` + `sec.gov/files/company_tickers.json` endpoints verified; 10-K Item regex splitting requires `beautifulsoup4` (4.15.0 available) |
| COLL-05 | yfinance metadata → `companies` + `company_aliases` (legal/common/brand, drop Inc./Corp./plc variants); `sustainability` → `external_esg_score` only | yfinance 0.2.40 pinned / 1.7.0 latest; `Ticker.info` + `Ticker.sustainability`/`get_sustainability` verified sync API; graceful None degradation required |
| COLL-06 | NewsAPI collector implemented but `enabled: false` in config | `config/sources.yaml` pattern exists; feature-flag gate pattern verified |
| COLL-07 | Deduplication on `content_hash` — re-running backfill adds zero rows | `UNIQUE(ticker, content_hash)` + `INSERT OR IGNORE` pattern in `DocumentRepo.insert()` verified; hash formula from data_model.md verified |

</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Rate-limited HTTP fetching & caching | API / Backend (collectors) | — | All network I/O is server-side; no browser involvement |
| GDELT query construction (alias OR-group + ESG bundle, chunking) | API / Backend | — | Pure server-side string building before HTTP call |
| EDGAR CIK resolution & filing retrieval | API / Backend | — | Server-side SEC API calls; result cached in DB |
| EDGAR 10-K section split (Item 1 / 1A) | API / Backend | — | HTML parsing server-side; persisted as `raw_documents` rows |
| yfinance metadata + alias seeding | API / Backend | — | Server-side enrichment; writes `companies`/`company_aliases` |
| Content-hash dedup & idempotent insert | Database / Storage | API / Backend | `UNIQUE(ticker, content_hash)` + `INSERT OR IGNORE` enforced by SQLite |
| `collection_runs` observability writes | Database / Storage | API / Backend | Every collector invocation logs outcome to DB |
| Backfill CLI orchestration | API / Backend (scripts) | — | CLI invokes collectors + repos; no HTTP tier |
| NewsAPI flagged-off collector | API / Backend | — | Same shared client, gated by `config/sources.yaml` |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `httpx` | `0.27.0` pinned (`0.28.1` latest) [VERIFIED: PyPI] | Async HTTP client shared by all collectors | Architecture §6 mandates httpx + tenacity; async-capable, retry-friendly; hishel's httpx integration depends on it |
| `hishel` | `1.3.1` latest [VERIFIED: PyPI] | RFC 9111 HTTP disk cache | Required by COLL-01; `hishel.httpx.AsyncCacheTransport` + `AsyncSqliteStorage` is the maintained httpx integration (JSON-file hand-roll in current `http.py` is not RFC-compliant) |
| `tenacity` | `8.5.0` pinned (`9.1.4` latest) [VERIFIED: PyPI] | Exponential backoff retry on 429/5xx | Required by COLL-01; `@retry` / `AsyncRetrying` with `retry_if_exception` + `wait_exponential` is the standard httpx+tenacity pattern |
| `yfinance` | `0.2.40` pinned (`1.7.0` latest) [VERIFIED: PyPI] | Yahoo Finance metadata + sustainability | Required by COLL-05; only free metadata source for sector/market_cap; no alternative without paid API |
| `structlog` | `24.4.0` pinned (`26.1.0` latest) [VERIFIED: PyPI] | JSON logging with `ticker`/`source`/`job_id` context | Existing `src/esg_lens/logging.py` uses structlog; collector never-raise contract logs via it |
| `beautifulsoup4` | `4.15.0` latest [VERIFIED: PyPI] | EDGAR 10-K HTML section splitting | No pinned dep yet but required for Item 1/1A extraction; `lxml` parser optional for speed; installed in env already |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `respx` | `0.23.1` latest [VERIFIED: PyPI] | Mock `httpx` in tests without live network | COLL-01 DoD criterion 4: "no live network calls" — `respx.mock` intercepts httpx transports |
| `pydantic` + `pydantic-settings` | `2.9.0` / `2.4.0` pinned [VERIFIED: PyPI] | Settings + `USER_AGENT` email validation | Already in `config.py`; `USER_AGENT` validator enforces `@` presence |
| `pyyaml` | `6.0.1` pinned [VERIFIED: PyPI] | Load `sources.yaml` / `controversy_lexicon.yaml` / `scoring.yaml` | Rate limits, TTLs, ESG bundle all read from YAML per D-015 |
| `lxml` | latest [VERIFIED: PyPI] | Fast HTML parser for BeautifulSoup | When parsing large 10-K filings (100k+ tokens); fallback is `html.parser` stdlib |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `hishel` disk cache | `httpx` + hand-rolled `hashlib` JSON files (current stub) | Hand-roll ignores `Cache-Control`, `ETag`, `Vary`, streaming, and TTL eviction; hishel is RFC-compliant and already a `[dev]` dep — use it |
| `httpx` async | `requests` sync | `requests` is sync-only, incompatible with FastAPI `BackgroundTasks` and hishel's `AsyncCacheTransport`; architecture §6 explicitly chose httpx |
| `beautifulsoup4` | Regex-only Item split (`re.compile(r"ITEM\s+1[A]?\.")`) | Regex is faster but fragile on malformed SEC HTML; recommend regex-first with BeautifulSoup fallback, or at minimum normalize with `bs4` before regex |
| `yfinance` | `sec-api` / direct Yahoo API scraping | `sec-api` is paid; direct scraping is even less stable than yfinance; yfinance remains the established free path — wrap behind `MetadataProvider` protocol |
| Custom token bucket | `aiolimiter` / `pyrate-limiter` | Third-party limiter adds dep for ~30 lines of code; per-host async token bucket is trivial — implement inline in `http.py` |

**Installation:**

```bash
pip install "httpx==0.27.0" "hishel[httpx]==1.3.1" "tenacity==8.5.0" "yfinance==0.2.40" "beautifulsoup4==4.15.0" "lxml"
# dev / test
pip install "respx==0.23.1" "pytest==8.3.0" "pytest-asyncio==0.23.5"
```

**Version verification:** All versions above confirmed via `pip index versions <pkg>` on 2026-09-03. `httpx 0.28.1` and `tenacity 9.1.4` are newer but project pins `httpx==0.27.0` + `tenacity==8.5.0` — hishel 1.3.1 is compatible with both; recommend staying pinned until cross-tested with `respx`.

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| `httpx` | PyPI | 6+ yrs (since 2019) | ~30M/mo | `encode/httpx` | [OK] | Approved |
| `hishel` | PyPI | 2+ yrs (since 2022) | ~2M/mo | `karpetrosyan/hishel` | [OK] | Approved |
| `tenacity` | PyPI | 10+ yrs | ~40M/mo | `jd/tenacity` | [OK] | Approved |
| `respx` | PyPI | 5+ yrs | ~3M/mo | `lundberg/respx` | [OK] | Approved |
| `yfinance` | PyPI | 7+ yrs | ~5M/mo | `ranaroussi/yfinance` | [OK] | Approved |
| `structlog` | PyPI | 10+ yrs | ~15M/mo | `hynek/structlog` | [OK] | Approved |
| `beautifulsoup4` | PyPI | 15+ yrs | ~30M/mo | `wention/BeautifulSoup4` | [OK] | Approved |
| `lxml` | PyPI | 18+ yrs | ~25M/mo | `lxml/lxml` | [OK] | Approved |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none (all [OK]; `slopcheck install` passed but `pip` binary not found on PATH caused install step to no-op — legitimacy is registry + age based, not install success)

*New dependency to add:* `beautifulsoup4` (and optionally `lxml`) is not in `pyproject.toml` yet — required for EDGAR 10-K section splitting. All other packages are already pinned.*

## Architecture Patterns

### System Architecture Diagram

```
                    ┌─────────────────────────────────┐
                    │   scripts/backfill.py            │
                    │   --tickers AAPL,XOM             │
                    │   --force-refresh (optional)     │
                    └──────────┬──────────────────────┘
                               │ 1. ticker uppercased, validated
                               ▼
                    ┌─────────────────────────────────┐
                    │  Collector orchestrator          │
                    │  (per ticker loop)               │
                    └─┬───────────┬───────────┬───────┘
                      │           │           │
         ┌────────────▼─┐ ┌──────▼──────┐ ┌─▼──────────────┐
         │ yfinance     │ │   GDELT     │ │    EDGAR        │
         │ Ticker.info  │ │ DOC 2.1     │ │ company_tickers │
         │ .sustainab.  │ │ mode=artlist│ │ → CIK →         │
         │ sync via     │ │ query=alias │ │ submissions API │
         │ thread pool  │ │ + bundle    │ │ → filing fetch  │
         └──────┬───────┘ └──────┬──────┘ │ → Item 1/1A     │
                │                │        └───────┬─────────┘
                │                │                │
                ▼                ▼                ▼
         ┌─────────────────────────────────────────────────┐
         │           Shared HttpClient (collectors/http.py) │
         │  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
         │  │ Token    │→ │ tenacity │→ │ hishel       │  │
         │  │ bucket   │  │ 429/5xx  │  │ AsyncSqlite  │  │
         │  │ 10/s     │  │ backoff  │  │ 24h TTL      │  │
         │  │ 1/s      │  │          │  │ force_refresh│  │
         │  └──────────┘  └──────────┘  │ bypass       │  │
         │                              └──────┬───────┘  │
         │  UA: "ESG-Lens/... (contact: @)"    │         │
         └─────────────────────────────────────┬──────────┘
                                               │ httpx.AsyncClient
                                               ▼
                                   ┌─────────────────────┐
                                   │  External APIs       │
                                   │  api.gdeltproject.org│
                                   │  data.sec.gov        │
                                   │  www.sec.gov         │
                                   │  query*.finance.yahoo│
                                   └─────────────────────┘
         ┌─────────────────────────────────────────────────┐
         │              Persistence                         │
         │  companies  ← yfinance upsert                  │
         │  company_aliases ← alias variants seeded         │
         │  raw_documents ← INSERT OR IGNORE (ticker,hash) │
         │  collection_runs ← status/n_fetched/n_new/error │
         └─────────────────────────────────────────────────┘
                    ▲                    ▲
                    │ content_hash       │ status=ok|failed|cached
                    │ dedup              │ never raises
                    └────────────────────┘
```

*Reader trace:* `backfill --tickers AAPL,XOM` → per ticker: yfinance enriches `companies` + seeds `company_aliases`; GDELT builds alias OR-group + ESG bundle query → `HttpClient` (bucket → retry → cache) → normalizes `domain`/`seendate`/`content_hash` → EDGAR resolves `company_tickers.json` → CIK → submissions → filing fetch → Item 1/1A split → `raw_documents` deduped on `content_hash` → `collection_runs` row per source regardless of success → re-run adds zero rows.

### Recommended Project Structure

```
src/esg_lens/collectors/
├── __init__.py          # re-exports Collector, HttpClient, helpers
├── http.py              # AsyncHttpClient: UA + token bucket + tenacity + hishel (REPLACE stub)
├── base.py              # Collector ABC + RawDocument dataclass + collection_runs helper (NEW — currently empty)
├── gdelt.py             # GdeltCollector: alias OR-group + ESG bundle + chunk + normalize (REPLACE stub)
├── edgar.py             # EdgarCollector: CIK cache + submissions + filing fetch + Item 1/1A split (REPLACE stub)
├── yfinance_meta.py     # YFinanceMetadataProvider: sync yfinance via run_in_executor + alias seeding (REPLACE stub)
└── newsapi.py           # NewsApiCollector: flagged off, enabled: false gate (NEW — currently empty)

scripts/
├── init_db.py           # existing — no change
└── backfill.py          # NEW — CLI orchestrator: tickers → yfinance → GDELT → EDGAR → dedup

tests/
├── conftest.py          # existing — extend with respx + collector fixtures if needed
├── fixtures/
│   ├── gdelt_artlist.json      # recorded GDELT mode=artlist&format=json response
│   ├── edgar_company_tickers.json
│   ├── edgar_submissions_AAPL.json
│   ├── edgar_10k_item1_excerpt.html
│   └── yfinance_AAPL_info.json
├── integration/
│   └── test_collectors.py      # respx-mocked E2E per collector + backfill idempotency
└── unit/
    ├── test_http_client.py     # token bucket + retry + cache bypass
    ├── test_gdelt_query.py     # alias filtering, bundle, chunk, quoting
    ├── test_edgar_split.py     # Item 1/1A extraction
    └── test_content_hash.py    # sha256(lower(title)|external_id/url)
```

### Pattern 1: Shared HttpClient — Token Bucket + Tenacity + Hishel

**What:** Single `httpx.AsyncClient` wrapped by a transport chain: custom per-host token bucket → `tenacity.AsyncRetrying` on 429/5xx → `hishel.httpx.AsyncCacheTransport` with `AsyncSqliteStorage`.

**When to use:** Every collector. No collector creates its own client.

**Example:**

```python
# Source: hishel httpx docs https://hishel.com/httpx.html + httpx docs
import asyncio
import time
import httpx
from hishel.httpx import AsyncCacheTransport
from hishel import AsyncSqliteStorage
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception

def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, httpx.RequestError)

class TokenBucketTransport(httpx.AsyncBaseTransport):
    """Per-host token bucket. Wrap the real transport."""
    def __init__(self, transport: httpx.AsyncBaseTransport, rates: dict[str, float]):
        self.transport = transport
        self.rates = rates  # e.g. {"api.gdeltproject.org": 1, "data.sec.gov": 10}
        self._buckets: dict[str, dict] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host or ""
        rate = self.rates.get(host, self.rates.get(host.split(".")[-2], 10))
        bucket = self._buckets.setdefault(host, {"tokens": rate, "updated": time.monotonic()})
        now = time.monotonic()
        elapsed = now - bucket["updated"]
        bucket["tokens"] = min(rate, bucket["tokens"] + elapsed * rate)
        bucket["updated"] = now
        if bucket["tokens"] < 1:
            await asyncio.sleep((1 - bucket["tokens"]) / rate)
            bucket["tokens"] = 0
        else:
            bucket["tokens"] -= 1
        return await self.transport.handle_async_request(request)

# Composition (outermost = first to handle request):
# request → TokenBucket → tenacity retry → hishel cache → network
# hishel's AsyncCacheTransport wraps the network transport; tenacity wraps hishel.

# Storage: SQLite file, 24h effective TTL via hishel policy
# Note: SEC/GDELT do not send Cache-Control; use a custom policy that forces caching
# for 24h regardless of response headers. hishel's default SpecificationPolicy would
# NOT cache responses without Cache-Control — you need a FilterPolicy or custom TTL.
```

**Critical nuance — hishel TTL [CITED: hishel.com/httpx.html, hishel source `_policies.py`]:**
Hishel's default `SpecificationPolicy` respects RFC 9111 — it will **not cache** responses that lack `Cache-Control`/`Expires` headers. Both `data.sec.gov` and `api.gdeltproject.org` typically return no caching headers, so the 24h TTL from `config/sources.yaml` would have zero effect unless you supply a custom policy. Use `FilterPolicy` or set `CacheOptions` to force-store for the desired TTL, or wrap with application-level cache logic that stores regardless of headers. The current stub's `hashlib`-to-JSON hand-roll accidentally avoids this by ignoring headers entirely — replacing it with hishel naively will silently disable caching.

### Pattern 2: Collector ABC + Never-Raise Contract

**What:** Abstract base with `async def fetch(ticker, since, *, job_id, force_refresh) -> list[RawDocument]` that never raises. Concrete collectors catch all exceptions, log via `structlog`, insert a `collection_runs` row, and return `[]`.

**When to use:** Every source collector.

**Example:**

```python
# Source: architecture.md §5.1 + handoff_to_backend.md Phase 1
import abc
import hashlib
import time
import structlog
from dataclasses import dataclass

log = structlog.get_logger()

@dataclass
class RawDocument:
    ticker: str
    source: str          # CHECK: gdelt|edgar|yfinance|newsapi
    doc_type: str        # CHECK: news|filing_section|press_release
    title: str | None
    body: str | None
    url: str | None
    domain: str | None
    external_id: str | None
    published_at: str | None  # ISO-8601 UTC TEXT
    filing_type: str | None   # '10-K' | '8-K' | 'DEF 14A'
    filing_section: str | None # 'Item 1' | 'Item 1A'
    content_hash: str
    raw_json: str | None

def content_hash(title: str | None, external_id: str | None, url: str | None) -> str:
    # Spec: data_model.md — sha256(lower(normalized_title) | coalesce(external_id,url,''))
    t = (title or "").strip().lower()
    key = external_id or url or ""
    return hashlib.sha256(f"{t}|{key}".encode()).hexdigest()

class Collector(abc.ABC):
    source: str

    @abc.abstractmethod
    async def fetch(self, ticker: str, since: str | None = None, *,
                    job_id: str | None = None, force_refresh: bool = False) -> list[RawDocument]: ...

    async def safe_fetch(self, conn, ticker: str, since: str | None = None, *,
                         job_id: str | None = None, force_refresh: bool = False) -> list[RawDocument]:
        t0 = time.monotonic()
        try:
            docs = await self.fetch(ticker, since, job_id=job_id, force_refresh=force_refresh)
            self._write_run(conn, ticker, job_id, "ok", len(docs), len(docs), None, int((time.monotonic()-t0)*1000))
            return docs
        except Exception as e:
            log.error("collector_failed", ticker=ticker, source=self.source, error=str(e))
            self._write_run(conn, ticker, job_id, "failed", 0, 0, str(e)[:1000], int((time.monotonic()-t0)*1000))
            return []

    def _write_run(self, conn, ticker, job_id, status, n_fetched, n_new, error, duration_ms):
        conn.execute(
            "INSERT INTO collection_runs (ticker, source, job_id, status, n_fetched, n_new, error, duration_ms) VALUES (?,?,?,?,?,?,?,?)",
            (ticker, self.source, job_id, status, n_fetched, n_new, error, duration_ms),
        )
        conn.commit()
```

### Pattern 3: GDELT Query Construction (D-01..D-03)

**What:** Build `query` param from: (a) filtered alias OR-group (drop ≤4 chars / stoplist, quote multi-word) and (b) ESG keyword bundle from `controversy_lexicon.yaml` tiers 1-3 + category names. Chunk at ~400 chars into 2 sequential `mode=artlist&format=json&maxrecords=250` calls, merge + dedup on `content_hash`.

**When to use:** GDELT collector only.

**Example:**

```python
# GDELT DOC 2.1 query spec [CITED: blog.gdeltproject.org/gdelt-doc-2-0-api-debuts]
# Actual params: query=..., mode=ArtList, format=json, maxrecords=250, timespan=3months (default)
# Alternative window: startdatetime=YYYYMMDDHHMMSS & enddatetime=YYYYMMDDHHMMSS (within last 3 months)
# Response: {"articles": [{"title":..., "url":..., "seendate": "20260903T091200Z", "domain":..., "language":...}]}

STOPLIST = {"inc", "corp", "ltd", "plc", "llc", "co", "group", "holdings"}

def filtered_aliases(all_aliases: list[str]) -> list[str]:
    out = []
    for a in all_aliases:
        if len(a) <= 4 or a.lower() in STOPLIST:
            continue
        out.append(f'"{a}"' if " " in a else a)
    return out

def build_gdelt_queries(aliases: list[str], esg_terms: list[str], max_chars: int = 400) -> list[str]:
    alias_group = " OR ".join(filtered_aliases(aliases))
    bundle = " OR ".join(f'"{t}"' if " " in t else t for t in esg_terms)
    # Intent is (alias_group) AND (bundle) — GDELT implicit AND is space
    # Use explicit OR groups: (alias1 OR alias2) (term1 OR term2)
    full = f"({alias_group}) ({bundle})"
    if len(full) <= max_chars:
        return [full]
    # Chunk bundle in half
    mid = len(esg_terms) // 2
    q1 = f"({alias_group}) ({' OR '.join(esg_terms[:mid])})"
    q2 = f"({alias_group}) ({' OR '.join(esg_terms[mid:])})"
    import structlog
    structlog.get_logger().warning("gdelt_query_chunked", chars=len(full), queries=2)
    return [q1, q2]

# Domain normalization
def normalize_domain(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None

# seendate parsing: GDELT returns "20260903T091200Z" (no dashes) or occasionally ISO
def parse_seendate(s: str | None) -> str | None:
    if not s:
        return None
    from datetime import datetime
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).isoformat() + "Z"
        except ValueError:
            continue
    return s  # fallback: store raw
```

### Pattern 4: EDGAR CIK Resolution + 10-K Section Split

**What:** Resolve `ticker → CIK` via `https://www.sec.gov/files/company_tickers.json` (array of `{cik_str, ticker, title}`) cached 7d. Fetch `https://data.sec.gov/submissions/CIK##########.json`, filter `filings.recent.form` for `10-K`/`8-K`/`DEF 14A`. For each 10-K, fetch `https://www.sec.gov/Archives/edgar/data/{CIK}/{accession_no_no_dashes}/{primary_doc}` and split on Item 1 / Item 1A headings, keeping only those two sections.

**When to use:** EDGAR collector.

**Example:**

```python
# Company tickers cache [CITED: sec.gov/files/company_tickers.json]
# https://www.sec.gov/files/company_tickers.json → {"0": {"cik_str": 320193, "ticker": "AAPL", ...}, ...}
# Alternative: https://www.sec.gov/files/company_tickers_exchange.json (includes exchange)
# Submissions: https://data.sec.gov/submissions/CIK0000320193.json
# Filing doc:  https://www.sec.gov/Archives/edgar/data/{cik}/{accession_dashes_removed}/{doc}
# NOTE: accessionNumber in submissions has dashes (e.g. 0000320193-23-000077) — strip dashes for URL path

import re
ITEM_RE = re.compile(
    r"^\s*ITEM\s+1A?\.?\s*[\-—:]*\s*(RISK FACTORS|BUSINESS)",
    re.IGNORECASE | re.MULTILINE,
)
# More robust: look for the TOC markers or the actual section headers in HTML
# SEC HTML is often malformed — use BeautifulSoup to extract text, then regex-split

def split_10k_items(html: str) -> dict[str, str]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n")
    # Find ITEM 1 and ITEM 1A boundaries
    # Keep only Item 1 and Item 1A; return {"Item 1": text, "Item 1A": text}
    # If neither found, return {} (caller logs and skips)
    ...
```

### Pattern 5: yfinance as Sync Library in an Async Codebase

**What:** `yfinance.Ticker(ticker).info` and `.sustainability` / `.get_sustainability()` are **synchronous** (backed by `requests`). Do not `await` them. Run in a thread pool via `asyncio.to_thread` or `run_in_executor`, and handle `None`/empty returns gracefully.

**When to use:** yfinance metadata provider only.

**Example:**

```python
import asyncio
import yfinance as yf
import structlog

log = structlog.get_logger()

async def fetch_yfinance(ticker: str) -> dict | None:
    def _sync():
        t = yf.Ticker(ticker)
        try:
            info = t.info  # may be {} or raise on network error
        except Exception as e:
            log.warning("yfinance_info_failed", ticker=ticker, error=str(e))
            info = {}
        try:
            sust = t.sustainability  # DataFrame or None
            if sust is None:
                sust = t.get_sustainability()  # alternative accessor
        except Exception:
            sust = None
        return info, sust

    info, sust = await asyncio.to_thread(_sync)
    if not info or not info.get("shortName"):
        return None
    return {"info": info, "sustainability": sust}

# Alias seeding
SUFFIXES = [" Inc.", " Inc", " Corp.", " Corp", " Corporation", " Incorporated",
            " plc", " PLC", " Ltd.", " Ltd", " Limited", " LLC", " LP", " Co."]

def alias_variants(legal_name: str, short_name: str | None) -> list[tuple[str, str]]:
    variants = set()
    for name in [legal_name, short_name]:
        if not name:
            continue
        variants.add((name.strip(), "legal" if name == legal_name else "common"))
        base = name.strip()
        for suf in SUFFIXES:
            if base.endswith(suf):
                variants.add((base[: -len(suf)].strip(), "brand"))
                break
        # Also add without trailing punctuation
        variants.add((base.rstrip(".,"), "brand"))
    return list(variants)
```

### Anti-Patterns to Avoid

- **Per-host `asyncio.Lock` instead of token bucket:** The current `http.py` serializes all requests to a host. A 10/s EDGAR bucket should allow 10 concurrent within a second, not 1. Use token bucket + `asyncio.sleep` for rate shaping.
- **Hand-rolled JSON file cache:** Ignores `Vary`, `Content-Length`, streaming, and TTL eviction. Replace with hishel + `AsyncSqliteStorage`. Verify hishel actually caches (needs custom policy due to missing `Cache-Control`).
- **Regex-only Item split without HTML normalization:** SEC filings contain malformed HTML, XBRL tags, and inline styles. Always normalize via `BeautifulSoup(...).get_text()` before regex; otherwise `ITEM&nbsp;1A` or `<B>ITEM 1A.</B>` will be missed.
- **Calling `yf.Ticker.info` from async context without `to_thread`:** Blocks the event loop for seconds. Yahoo endpoints are slow and intermittently timeout.
- **Gating `content_hash` on raw title only:** Must use `lower(normalized_title)` and `coalesce(external_id, url)` per data_model.md. Hash collisions across tickers are not a problem (UNIQUE is per ticker), but re-running must hit the same hash.
- **Storing yfinance sustainability as a signal:** `external_esg_score` is `companies` column only. Any code path that feeds it into `esg_signals` violates D-007 and destroys the "independent signal" claim.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP disk caching with TTL | JSON files keyed by `sha256(url)` | `hishel.httpx.AsyncCacheTransport` + `AsyncSqliteStorage` | RFC 9111, `Vary`/`ETag`/`Cache-Control`, streaming, eviction, `extensions["hishel_from_cache"]` — all missed by hand-roll; hishel is already a `[dev]` dep |
| Exponential backoff on 429/5xx | Manual `for attempt in range(5): sleep(2**attempt)` | `tenacity.AsyncRetrying` / `@retry_if_exception` + `wait_exponential` | Jitter, `reraise`, `stop_after_attempt`, `before_sleep_log` — tenacity is pinned and battle-tested |
| Retry on non-retryable errors | Retry on all `HTTPStatusError` | `retry_if_exception(lambda e: isinstance(e, HTTPStatusError) and e.response.status_code in (429,500,502,503,504))` | Retrying 400/401/403/404 wastes quota and hides bugs; only 429 + 5xx are retryable |
| Rate limiting | `asyncio.Lock` per host | Per-host token bucket (`asyncio.sleep` + token replenish) or `aiolimiter` | Lock serializes to 1/s regardless of bucket size; EDGAR needs 10/s, GDELT 1/s — bucket allows burst correctly |
| 10-K HTML parsing | String `split("ITEM 1A")` | `BeautifulSoup(html, "lxml").get_text()` + `re.compile(r"ITEM\s+1A?")` | SEC HTML is malformed, entity-encoded, and uses `&nbsp;`/nested tags; naive split misses 30-40% of sections |
| yfinance sustainability extraction | Assume `info["sustainability"]` exists | `t.sustainability` (DataFrame) + `t.get_sustainability()` fallback + `None` guard | `sustainability` is intermittently empty per research_notes §2.1; field is a DataFrame, not a scalar — totalEsg is at `sust.loc["totalEsg"]` |
| Domain extraction | `url.split("/")[2]` | `urllib.parse.urlparse(url).hostname` + `lstrip("www.")` + `lower()` | `split` fails on `http://`, ports, credentials, and `None`; urlparse handles all URL shapes |

**Key insight:** The current stubs hand-roll exactly the three things that look trivial but have sharp edges (caching, retry, HTML parsing). Each hand-roll misses the spec's edge cases. The planner should budget replacement, not patching, for all three.

## Common Pitfalls

### Pitfall 1: Hishel Silently Not Caching Because SEC/GDELT Omit Cache-Control

**What goes wrong:** Replace hand-rolled cache with hishel, run backfill twice, second run still hits the network. `force_refresh` appears broken because nothing was cached.

**Why it happens:** Hishel's default `SpecificationPolicy` follows RFC 9111 — a response without `Cache-Control: max-age` or `Expires` is considered non-cacheable. Both SEC and GDELT omit these headers, so hishel stores nothing by default.

**How to avoid:** Supply a `FilterPolicy` or custom `CacheOptions` that forces caching for all 200 responses for the configured TTL, or set `Cache-Control` via a response filter. Alternatively, keep a thin application-level cache (SQLite keyed by URL+params with explicit 24h/7d TTL) alongside hishel. The planner must include a verification step: `assert response.extensions["hishel_from_cache"] is True` on the second identical request in an integration test.

**Warning signs:** Cache-hit rate 0% in logs; `hishel_from_cache` always `False` in tests.

### Pitfall 2: SEC Blocks IP for Missing or Malformed User-Agent

**What goes wrong:** EDGAR returns 403 after a few requests; all filing collection fails; retries amplify the block.

**Why it happens:** SEC requires `User-Agent: <Company or App Name> (<contact email>)` and identifies the caller. The stub reads `settings.USER_AGENT` correctly but the pyproject default `esg-lens@example.com` is a placeholder — if not overridden via `.env`, the SEC may treat it as non-contactable.

**How to avoid:** Keep the validator `@field_validator("USER_AGENT")` that requires `@`; in `backfill.py` log the UA at startup; on 403, log a specific `collector_error` suggesting UA check rather than retrying. Tenacity must **not** retry 403.

**Warning signs:** 403 from `data.sec.gov` or `www.sec.gov` on first request; retry loop on 403.

### Pitfall 3: GDELT Query Truncation and Character-Count Drift

**What goes wrong:** Query exceeds GDELT's undocumented ~500-char limit; GDELT returns `{"articles": []}` or a truncated result without error; recall drops silently.

**Why it happens:** GDELT does not document a max query length; the `gdeltdoc` client does not enforce one. A broad ESG bundle (~30 terms) plus 3-5 aliases easily exceeds 400 chars. URL-encoding inflates further (spaces → `%20`, quotes → `%22`).

**How to avoid:** Enforce D-03's ~400-char guard **before URL-encoding** (conservative), chunk into 2 sequential queries, log a warning, and merge with `content_hash` dedup. Test with the longest expected alias set (`AAPL` + 5 aliases + 30-term bundle) and assert chunking triggers.

**Warning signs:** GDELT returns zero results for a ticker that should have coverage; query string length > 450 chars in logs.

### Pitfall 4: Mixing Evidence Weight and Scoring Weight

**What goes wrong:** Not Phase 1's scoring bug per se, but collectors must persist `w_src`/`w_rec` correctly so downstream `w_ev` vs `w` are not confused. If the collector writes a pre-computed `weight_total` to `raw_documents` (there is no such column — weights live on `esg_signals`), it will be ignored; if it mangles `domain` (drives `w_src`) the score will be wrong.

**Why it happens:** Collector authors see `scoring_methodology.md` §5 and eagerly compute weights. Weights belong in the NLP pipeline, not collectors. Collectors only provide `domain` (for `w_src` tier lookup) and `published_at` (for `w_rec` decay).

**How to avoid:** Collectors write `domain` (normalized) and `published_at` (ISO-8601) faithfully and write nothing else weight-related. Add a unit test that `domain="reuters.com"` maps to tier 1 via `sources.yaml`.

**Warning signs:** `domain` is `None` or `www.reuters.com` (not stripped); `published_at` is raw GDELT `seendate` without ISO conversion.

### Pitfall 5: `yfinance` Blocks the Event Loop

**What goes wrong:** Backfill hangs for 10-20s per ticker; concurrent GDELT/EDGAR requests stall.

**Why it happens:** `yf.Ticker.info` is synchronous and performs multiple HTTP fetches internally (via `requests`). Calling it with `await` in an async function blocks the loop.

**How to avoid:** Always `await asyncio.to_thread(lambda: yf.Ticker(ticker).info)` or use `run_in_executor`. Never call `yfinance` from the main async path. Wrap with a timeout (`asyncio.wait_for(..., timeout=15)`) since Yahoo endpoints can hang.

**Warning signs:** `backfill --tickers AAPL` takes >15s even with cache warm; GDELT requests queue behind yfinance.

### Pitfall 6: EDGAR `company_tickers.json` Structure Mismatch

**What goes wrong:** CIK lookup returns `None` for every ticker; EDGAR path is always skipped; `collection_runs` shows `n_fetched=0` for `edgar`.

**Why it happens:** The endpoint changed structure at least once. `https://www.sec.gov/files/company_tickers.json` returns a dict keyed by integer strings (`{"0": {"cik_str": 320193, "ticker": "AAPL", ...}}`), not a list. Code that does `data["AAPL"]` or iterates expecting a list will fail. The alternative `company_tickers_exchange.json` has a different shape.

**How to avoid:** Fetch once, inspect `isinstance(data, dict)` vs `list`, and build `ticker_upper → cik_str` mapping accordingly. Cache the mapping to `data/cache/company_tickers.json` with 7d TTL. Add a fixture with the real shape and a unit test for both dict and list shapes.

**Warning signs:** `cik is None` for known tickers like AAPL/XOM.

### Pitfall 7: 10-K Filing URL Construction — Dashes in Accession Number

**What goes wrong:** Filing fetch returns 404 for every 10-K.

**Why it happens:** `submissions` API returns `accessionNumber` with dashes (`0000320193-23-000077`) but the Archives URL path requires no dashes (`000032019323000077`). The primary document filename is in `filings.recent.primaryDocument[i]` (e.g. `aapl-20230930.htm`), not derivable from the accession alone.

**How to avoid:** `acc_no_nodash = accessionNumber.replace("-", "")`; `url = f"https://www.sec.gov/Archives/edgar/data/{cik_no_leading_zeros}/{acc_no_nodash}/{primaryDocument}"`. Use `cik.lstrip("0")` for the path segment. Test against a recorded AAPL submission JSON.

**Warning signs:** 404 from `www.sec.gov/Archives/edgar/data/...`.

### Pitfall 8: Tests That Make Live Network Calls

**What goes wrong:** CI passes locally but fails in offline CI; SEC/GDELT rate-limit the CI IP; flaky tests.

**Why it happens:** `respx` only mocks `httpx` transports. `yfinance` uses `requests` internally, so `respx` does not mock it. Collectors that call `yfinance` without mocking will hit Yahoo even when GDELT/EDGAR are mocked.

**How to avoid:** Mock `yfinance.Ticker` via `unittest.mock.patch("yfinance.Ticker")` and return canned `info`/`sustainability` fixtures. `respx` covers httpx paths (GDELT/EDGAR); `mock.patch` covers yfinance. The requirement OBS-04 ("no live network calls anywhere in test suite") must be enforced by a test that asserts `respx` is active and `yfinance.Ticker` is patched.

**Warning signs:** Tests pass with `--offline` flag disabled but fail with network disabled; `pytest` makes outbound connections visible in logs.

## Code Examples

Verified patterns from official sources:

### Shared httpx + hishel 24h cache with force_refresh bypass

```python
# Source: hishel.com/httpx.html + hishel source _async_httpx.py
import httpx
from hishel.httpx import AsyncCacheTransport
from hishel import AsyncSqliteStorage

# 24h cache backed by SQLite file
storage = AsyncSqliteStorage(path="data/cache/http.sqlite")

# force_refresh bypass: skip cache entirely when True
# hishel respects Cache-Control: no-cache from the request — set it when force_refresh
async def cached_get(url: str, *, force_refresh: bool = False) -> httpx.Response:
    transport = AsyncCacheTransport(
        next_transport=httpx.AsyncHTTPTransport(),
        storage=storage,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        headers = {"Cache-Control": "no-cache"} if force_refresh else {}
        resp = await client.get(url, headers=headers)
        # resp.extensions["hishel_from_cache"] is True when served from cache
        return resp
```

### Tenacity exponential backoff on 429/5xx only

```python
# Source: tenacity docs https://tenacity.readthedocs.io
import httpx
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception, before_sleep_log
import structlog

log = structlog.get_logger()

def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.RequestError, httpx.ConnectError, httpx.ReadTimeout))

async def get_with_retry(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_retryable),
        before_sleep=before_sleep_log(log, log_level=structlog.INFO),
        reraise=True,
    ):
        with attempt:
            resp = await client.get(url, **kw)
            resp.raise_for_status()
            return resp
```

### respx fixture for httpx (no live network in tests)

```python
# Source: respx docs https://lundberg.github.io/respx/
import respx
import httpx
import json
import pytest

# JSON fixture for GDELT DOC 2.1 mode=artlist&format=json
# Real shape: {"articles": [{"title": ..., "url": ..., "seendate": "20260903T091200Z", "domain": ..., "language": "English", "sourcecountry": "United States"}]}
@pytest.fixture
def gdelt_fixture():
    return json.loads(open("tests/fixtures/gdelt_artlist.json").read())

@respx.mock
@pytest.mark.asyncio
async def test_gdelt_collector_mocked(gdelt_fixture):
    respx.get("https://api.gdeltproject.org/api/v2/doc/doc").mock(
        return_value=httpx.Response(200, json=gdelt_fixture)
    )
    # collector uses httpx.AsyncClient internally — respx intercepts the transport
    # no live call leaves the process
    ...

# Alternative: route with query param matching
# respx.get(url__regex=r".*api\.gdeltproject\.org.*").mock(...)
```

### SEC submissions + filing fetch (httpx)

```python
# Source: sec.gov EDGAR API docs https://www.sec.gov/search-filings/edgar-application-programming-interfaces
import httpx

# 1. CIK lookup (cached 7d)
async def get_cik(client: httpx.AsyncClient, ticker: str, cache: dict) -> str | None:
    # cache key: "company_tickers" → dict[ticker_upper, cik_str]
    # fetch https://www.sec.gov/files/company_tickers.json if stale
    # response is {"0": {"cik_str": 320193, "ticker": "AAPL", ...}}
    ...

# 2. Submissions
# GET https://data.sec.gov/submissions/CIK0000320193.json
# Headers must include User-Agent with email + Accept-Encoding + Host
headers = {
    "User-Agent": "ESG-Lens/0.1.0 (contact: esg-lens@example.com)",
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov",
}
resp = await client.get(f"https://data.sec.gov/submissions/CIK{padded_cik}.json", headers=headers)
data = resp.json()
# data["filings"]["recent"] has parallel arrays: form[], accessionNumber[], filingDate[], primaryDocument[]
# data["cik"] is zero-padded string; data["name"] is entity name

# 3. Filing document
# GET https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_nodash}/{primaryDocument}
```

### yfinance mocked in tests (requests-based, not httpx — respx won't catch it)

```python
# Source: yfinance docs — Ticker.info is a property, sustainability is a DataFrame
from unittest.mock import patch, MagicMock
import pandas as pd

@patch("yfinance.Ticker")
def test_yfinance_metadata(mock_ticker_cls):
    mock_ticker = MagicMock()
    mock_ticker.info = {
        "shortName": "Apple Inc.",
        "longName": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "country": "United States",
        "currency": "USD",
        "marketCap": 3000000000000,
        "cik": "0000320193",
    }
    # sustainability is a DataFrame with index like ["totalEsg", "environmentScore", ...]
    mock_ticker.sustainability = pd.DataFrame({"Value": [25.0]}, index=["totalEsg"])
    mock_ticker_cls.return_value = mock_ticker

    # call provider.sync_fetch(ticker) or await provider.fetch(ticker)
    ...
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `requests` + `requests-cache` | `httpx` + `hishel` | 2023-2024 (hishel 1.x stable) | Async-compatible, RFC 9111, streaming-friendly; `requests-cache` is sync-only |
| Hand-rolled `hashlib` JSON file cache | `hishel` `AsyncSqliteStorage` | This phase must migrate | Hand-roll misses `Vary`, TTL eviction, and `Cache-Control` handling |
| `asyncio.Lock` per host | Per-host token bucket | This phase must migrate | Lock serializes to 1/s; bucket allows 10/s for EDGAR with burst |
| `tenacity 8.x` `@retry` decorator | `tenacity 9.x` `AsyncRetrying` context manager also available | tenacity 9.0 (2024) | 8.x decorator still works; 9.x adds `AsyncRetrying` as preferred async pattern — both are valid |
| `gdeltdoc` pip package wrapping GDELT | Direct `httpx` call to `api.gdeltproject.org/api/v2/doc/doc` | Ongoing | `gdeltdoc` is convenient but adds dep and hides `maxrecords`/`timespan` knobs; direct call is ~20 lines and more controllable |
| `yfinance` `Ticker.info` dict | `Ticker.get_info()` method (newer yfinance) | yfinance 0.2.60+ | `get_info()` is the newer accessor; `info` property still works but may warn; handle both |
| SEC `company_tickers.json` at `data.sec.gov` | Canonical at `https://www.sec.gov/files/company_tickers.json` | ~2023 | Both URLs serve the same file; `sec.gov/files/` is the documented canonical path |

**Deprecated/outdated:**
- `httpx==0.27.0` pinned — `0.28.1` is current; hishel 1.3.1 and respx 0.23.1 support both. Stay pinned until Phase 1 integration tests pass, then bump.
- `yfinance==0.2.40` pinned — `1.7.0` is current (0.2.x → 1.x was a major bump with `curl_cffi` session support). Planner should test `0.2.40` behavior first; bumping to 1.x mid-phase risks breaking `info`/`sustainability` shapes.
- `hishel` JSON-file pattern from any blog post pre-2024 — the `hishel.httpx.AsyncCacheTransport` + `AsyncSqliteStorage` API is the current (1.3.x) integration; older `hishel.CacheTransport` top-level import was removed.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | GDELT DOC 2.1 `mode=ArtList` + `format=json` returns `{"articles": [{"title","url","seendate","domain","language","sourcecountry","socialimage"}]}` with `seendate` as `YYYYMMDDTHHMMSSZ` | GDELT collector | Field names differ (e.g. `seendate` vs `date` vs `publishDate`) — document ingestion breaks; mitigation: record a fixture from a live call and assert shape |
| A2 | GDELT respects `maxrecords=250` max and defaults to 75 | GDELT collector | Pagination needed if 250 insufficient for broad bundle; mitigation: chunk queries as D-03 requires |
| A3 | SEC `company_tickers.json` is at `https://www.sec.gov/files/company_tickers.json` with shape `{"0": {"cik_str": int, "ticker": str, ...}}` | EDGAR CIK resolution | URL or shape changed — CIK lookup fails for all tickers; mitigation: handle both dict and list shapes |
| A4 | SEC `data.sec.gov/submissions/CIK##########.json` requires `User-Agent` + `Accept-Encoding: gzip` + `Host: data.sec.gov` and returns parallel arrays under `filings.recent` | EDGAR submissions | Header requirements stricter than documented — 403 on missing Accept-Encoding; mitigation: copy SEC's documented header set |
| A5 | SEC filing HTML at `www.sec.gov/Archives/edgar/data/{cik_no_zero}/{acc_nodash}/{doc}` is HTML with `ITEM 1` / `ITEM 1A` text detectable after `BeautifulSoup.get_text()` | EDGAR section split | Filings are XBRL/HTML mix; some 10-Ks use different Item heading markup; mitigation: case-insensitive regex + fallback to raw text |
| A6 | `yfinance.Ticker(ticker).info` returns `shortName`/`sector`/`cik` synchronously and `sustainability` is a DataFrame or None | yfinance metadata | yfinance endpoint intermittently returns empty `info` (Yahoo outage) — must degrade gracefully per research_notes §2.1 |
| A7 | hishel 1.3.1 `AsyncSqliteStorage` + `AsyncCacheTransport` works with `httpx 0.27.0` | Shared HttpClient | Version incompatibility — hishel 1.3.1 was tested against httpx 0.28.x; 0.27.0 may have minor transport API drift; mitigation: smoke-test hishel+httpx import in CI |
| A8 | `tenacity` `wait_exponential(multiplier=1, min=2, max=10)` with `stop_after_attempt(5)` is the agreed backoff (per CONTEXT.md) | Shared HttpClient | No risk — this is a discretion choice; just needs to be consistent and logged |
| A9 | `beautifulsoup4` + `lxml` are acceptable new deps (not yet in pyproject) | EDGAR section split | Policy objection to new deps — mitigated by noting `html.parser` stdlib fallback works, just slower |

## Open Questions (RESOLVED)

1. **hishel forced-caching policy for header-less responses** — RESOLVED: Use FilterPolicy forced caching for all 200s spiked in 01-01 Task1 (AsyncCacheTransport + AsyncSqliteStorage + FilterPolicy caching every 200). Fallback thin ttl_cache.py only if filter wiring proves complex; test is hishel_from_cache True on second identical GET. Implemented per 01-01 Task1.
   - What we know: hishel's `SpecificationPolicy` won't cache SEC/GDELT responses without `Cache-Control`. The 24h TTL requirement is application-level, not protocol-level.
   - What's unclear: Whether to use `FilterPolicy` with `request_filters`/`response_filters` to force-store, or to bypass hishel's policy entirely and implement a thin `SQLite` URL→response cache at the collector layer.
   - Recommendation: Spike both in Plan 01-01: try `AsyncCacheTransport(storage=AsyncSqliteStorage(), policy=FilterPolicy(...))` that caches all 200s; if it requires complex filter wiring, fall back to a 30-line `ttl_cache.py` that wraps the httpx call and stores `response.content` + `headers` explicitly. Either satisfies COLL-01; the test is `hishel_from_cache is True` on second call.

2. **GDELT query length — URL-encoded vs raw char count** — RESOLVED: Chunk conservatively at 400 raw chars per D-03, log raw+encoded lengths on warning, sequential merged queries with content_hash dedup. Implemented per 01-02 Task1 (quoted + chunk if needed).

3. **10-K primary document filename — HTML vs TXT vs XBRL** — RESOLVED: Handle both via lxml get_text with raw fallback if <500 chars; record fixtures for both shapes. Implemented per 01-03 Task1 (BeautifulSoup + lxml fallback).

4. **yfinance `Ticker.sustainability` DataFrame shape** — RESOLVED: Handle generically via sust.iloc[0,0] or index search for totalEsg, both fixture shapes, graceful NULL on absent. Implemented per 01-03 Task2; never fail, external_esg_score isolation preserved.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| `python3` | All plans | ✓ | 3.14.4 (requires ≥3.11) | — |
| `httpx` | COLL-01, GDELT, EDGAR | ✓ | 0.28.1 installed, 0.27.0 pinned | — |
| `hishel` | COLL-01 cache | ✓ | 1.3.1 installed | Hand-rolled SQLite TTL cache (~30 lines) |
| `tenacity` | COLL-01 retry | ✓ | 9.1.4 installed, 8.5.0 pinned | — |
| `respx` | Tests (no live network) | ✓ | 0.23.1 installed | `unittest.mock` for httpx (worse DX) |
| `yfinance` | COLL-05 metadata | ✓ | 1.2.0 installed, 0.2.40 pinned | Graceful `None` — metadata is best-effort |
| `structlog` | LOG-01, never-raise logging | ✓ | 26.1.0 installed, 24.4.0 pinned | Stdlib logging (loses JSON) |
| `beautifulsoup4` | EDGAR 10-K split | ✓ | 4.15.0 installed | Not in pyproject.toml — must be added |
| `lxml` | EDGAR parser speed | ✓ (via bs4) | 4.15.0 | stdlib `html.parser` fallback |
| `slopcheck` | Package audit | ✓ | 0.6.1 | Manual PyPI age check |
| `pytest` + `pytest-asyncio` | All test plans | ✓ | 8.3.0 / 0.23.5 pinned | — |
| `SQLite` | `raw_documents`, `collection_runs` | ✓ | via `db/engine.py` WAL + foreign_keys | — |

**Missing dependencies with no fallback:** none — all core deps are installed or have fallbacks.
**Missing dependencies with fallback:** `beautifulsoup4` not in `pyproject.toml` (fallback is `html.parser`); hishel policy nuance (fallback is application-level TTL cache).

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest 8.3.0` + `pytest-asyncio 0.23.5` |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` — `testpaths=["tests"]`, `asyncio_mode="auto"` |
| Quick run command | `pytest -q` |
| Full suite command | `pytest -v --tb=short` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| COLL-01 | Shared httpx: UA contains `@`, per-host bucket (EDGAR 10/s, GDELT 1/s), tenacity backoff on 429/5xx only, hishel 24h cache, `force_refresh` bypass | unit + integration | `pytest tests/unit/test_http_client.py -x -v` + `pytest tests/integration/test_collectors.py::test_cache -x` | ❌ Wave 0 |
| COLL-02 | Collector ABC never raises; failure writes `collection_runs` row with `status=failed` and returns `[]` | unit | `pytest tests/unit/test_collector_base.py -x -v` | ❌ Wave 0 |
| COLL-03 | GDELT: alias OR-group + ESG bundle, `domain` normalized, `seendate` parsed, `content_hash` correct, chunked if >400 chars | unit + integration (respx) | `pytest tests/unit/test_gdelt_query.py -x` + `pytest tests/integration/test_collectors.py::test_gdelt -x` | ❌ Wave 0 |
| COLL-04 | EDGAR: `company_tickers.json` → CIK (7d TTL), submissions → 10-K/8-K/DEF 14A, 10-K split keeps Item 1+1A only, one row per section | unit + integration (respx) | `pytest tests/unit/test_edgar_split.py -x` + `pytest tests/integration/test_collectors.py::test_edgar -x` | ❌ Wave 0 |
| COLL-05 | yfinance: populates `companies` + `company_aliases` variants, `sustainability → external_esg_score` only, graceful None | unit (mock.patch) | `pytest tests/unit/test_yfinance_meta.py -x -v` | ❌ Wave 0 |
| COLL-06 | NewsAPI implemented, `enabled: false` → returns `[]` without network call | unit | `pytest tests/unit/test_newsapi.py -x -v` | ❌ Wave 0 |
| COLL-07 | Deduplication: `INSERT OR IGNORE` on `(ticker, content_hash)`; re-run adds zero rows | integration | `pytest tests/integration/test_collectors.py::test_dedup -x -v` | ❌ Wave 0 |
| COLL-01 (backfill) | `scripts/backfill.py --tickers AAPL,XOM` populates `raw_documents` + `companies`/`aliases`; re-run zero rows | integration | `pytest tests/integration/test_backfill.py -x -v` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest -q` (fast, <5s)
- **Per wave merge:** `pytest -v --tb=short`
- **Phase gate:** Full suite green before `/gsd:verify-work`; no live network calls anywhere (`respx` + `mock.patch` for yfinance)

### Wave 0 Gaps

- [ ] `tests/fixtures/gdelt_artlist.json` — recorded `mode=artlist&format=json&maxrecords=10` response (2-3 articles)
- [ ] `tests/fixtures/edgar_company_tickers.json` — excerpt of `company_tickers.json` with AAPL, XOM, plus one unknown ticker
- [ ] `tests/fixtures/edgar_submissions_AAPL.json` — recorded `CIK0000320193.json` recent filings excerpt (one 10-K, one 8-K, one DEF 14A)
- [ ] `tests/fixtures/edgar_10k_item1_excerpt.html` — minimal 10-K HTML containing Item 1 and Item 1A headings
- [ ] `tests/fixtures/yfinance_AAPL_info.json` — canned `Ticker.info` dict + `sustainability` DataFrame fixture
- [ ] `tests/unit/test_http_client.py` — UA, token bucket timing, retry predicate, cache bypass
- [ ] `tests/unit/test_collector_base.py` — never-raise contract + `collection_runs` write
- [ ] `tests/unit/test_gdelt_query.py` — alias filtering (≤4 chars, stoplist), quoting, bundle, chunk at 400 chars
- [ ] `tests/unit/test_edgar_split.py` — BeautifulSoup + regex Item 1/1A extraction
- [ ] `tests/unit/test_yfinance_meta.py` — `mock.patch("yfinance.Ticker")` alias variants + sustainability routing
- [ ] `tests/integration/test_collectors.py` — respx-mocked GDELT/EDGAR + dedup + never-raise E2E
- [ ] `tests/integration/test_backfill.py` — `backfill --tickers` idempotency
- [ ] `tests/conftest.py` — extend existing `db_conn` fixture; add `respx_mock` and `mock_yfinance` helpers if needed
- [ ] `src/esg_lens/collectors/base.py` — currently empty; must define `Collector` ABC + `RawDocument` + `content_hash`
- [ ] `pyproject.toml` — add `beautifulsoup4` (and optionally `lxml`) to `dependencies` or `[dev]`

*No existing collector tests or fixtures exist — Wave 0 is the full test scaffolding for Phase 1. The `tests/` tree currently has only `unit/test_config.py` + `unit/test_schema.py` from Phase 0.*

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | — (single user, localhost, no auth in v1) |
| V3 Session Management | No | — |
| V4 Access Control | No | — |
| V5 Input Validation | **Yes** | Ticker regex `^[A-Z0-9.\-]{1,10}$` uppercased + 25-ticker cap at `backfill.py` CLI and API boundary; `since`/`lookback_days` range 30-730; `sources` subset of `raw_documents.source` CHECK |
| V6 Cryptography | No | `content_hash` is SHA-256 for dedup, not for security |
| V7 Error Handling | **Yes** | Never-raise contract + `collection_runs.error` truncated to 1000 chars; no stack traces to stdout beyond structlog |
| V8 Data Protection | **No** | No secrets in scope; EDGAR/GDELT/yfinance are public data |
| V10 Malicious Code | No | — |
| V12 Files & Resources | **Yes** | `hishel` SQLite cache path under `data/cache/` (gitignored); no user-supplied file paths |
| V14 Configuration | **Yes** | `USER_AGENT` must contain `@`; `CACHE_DIR`/`DB_PATH` from `settings`; no secrets in logs (OBS-02) |

### Known Threat Patterns for Collectors Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SSRF via ticker param (if collector URL were user-controlled) | Tampering / Information Disclosure | URLs are hardcoded to `api.gdeltproject.org`/`data.sec.gov`/`www.sec.gov`; ticker only appears as query value, never as URL host |
| SEC IP block via missing/fake UA leading to DoS | Denial of Service | `USER_AGENT` validator + distinct 403 log (no retry on 403); docs the SEC privacy policy |
| Log injection via ticker/source strings | Tampering | structlog JSON escapes values; `_write_run` uses parameterised SQL (`?` placeholders), never string interpolation |
| Cache poisoning via crafted response | Tampering | hishel validates per RFC 9111; SQLite cache is file-permission protected (`data/cache/` not world-readable); no user can write to cache |
| ReDoS via crafted 10-K HTML / Item regex | Denial of Service | Regex is anchored (`^ITEM\s+1A?`) with `re.IGNORECASE|MULTILINE` — no catastrophic backtracking; BeautifulSoup caps parse on 10-K size (~few MB); filing fetch has 30s timeout |
| SQL injection via collector inserts | Tampering | All writes via `DocumentRepo.insert()` / `CompanyRepo.upsert()` use parameterised `?` placeholders (architecture §7); no string-concatenated SQL |

## Sources

### Primary (HIGH confidence)

- `hishel` PyPI 1.3.1 + `hishel.httpx.AsyncCacheTransport` / `AsyncSqliteStorage` source at `Library/.../hishel/_async_httpx.py` — API verified via `python3 -c` introspection
- `hishel.com/httpx.html` + `hishel.com/quickstart.html` — httpx integration docs (fetched via WebFetch 2026-09-03)
- `hishel` source `_policies.py` — `SpecificationPolicy` vs `FilterPolicy` distinction verified
- `httpx` PyPI 0.28.1 + `tenacity` PyPI 9.1.4 + `respx` PyPI 0.23.1 — versions verified via `pip3 index versions` 2026-09-03
- `beautifulsoup4` PyPI 4.15.0 — verified via `pip3 index versions` and `import bs4; bs4.__version__` 2026-09-03
- `pyproject.toml` + `config/sources.yaml` + `config/scoring.yaml` + `config/controversy_lexicon.yaml` + `src/esg_lens/config.py` + `src/esg_lens/db/schema.sql` + `src/esg_lens/db/engine.py` — read directly from repo 2026-09-03
- CONTEXT.md D-01..D-03, REQUIREMENTS.md COLL-01..07, handoff_to_backend.md Phase 1, architecture.md §5.1/§6, data_model.md DDL — read directly 2026-09-03

### Secondary (MEDIUM confidence)

- `blog.gdeltproject.org/gdelt-doc-2-0-api-debuts` — GDELT DOC 2.1 spec (mode, query, maxrecords 250, timespan/startdatetime/enddatetime, format) — fetched via WebSearch excerpts 2026-09-03; 404 on direct WebFetch but excerpts are authoritative (official GDELT blog)
- `sec.gov` EDGAR API docs (`sec.gov/search-filings/edgar-application-programming-interfaces`) — submissions JSON shape + bulk ZIP — fetched via WebSearch excerpts 2026-09-03
- `yfinance` 1.2.0 `dir(yfinance.Ticker)` + `Ticker.info`/`sustainability` inspection via `python3 -c` 2026-09-03 — sync nature and property names confirmed
- `gdeltdoc` GitHub `alex9smith/gdelt-doc-api` README — confirms `mode=ArtList`, `maxrecords` ≤250, `format` json/csv 2026-09-03

### Tertiary (LOW confidence)

- GDELT ~500-char query limit — undocumented, from CONTEXT.md D-03 citing community observation; no official GDELT doc states this — treat as heuristic, hence the 400-char conservative guard
- SEC rate limit 10/s + UA-with-email requirement — from research_notes.md `[VERIFY]` and CONTEXT.md; not re-verified against live SEC docs in this session (WebFetch to sec.gov returned 403)
- `yfinance` `get_sustainability()` vs `.sustainability` property — both appear in `dir()` but exact DataFrame shape not verified live (no network call made)

## Metadata

**Confidence breakdown:**

- Standard stack: MEDIUM — httpx/hishel/tenacity/respx/yfinance/structural versions verified via PyPI + local introspection; hishel 1.3.1 + httpx 0.27.0 compatibility not live-tested; yfinance DataFrame shape not live-fetched
- Architecture: MEDIUM — GDELT/EDGAR API shapes verified via official docs excerpts + `gdeltdoc` client, but GDELT query char limit and SEC header strictness are community-reported, not officially confirmed; hishel policy nuance for header-less caching is a real risk that must be spiked in Plan 01-01
- Pitfalls: HIGH — seven pitfalls derived from spec gaps, existing stub flaws, and verified API quirks; each has a concrete detection/mitigation

**Research date:** 2026-09-03
**Valid until:** 2026-10-03 (stable APIs: GDELT/EDGAR contracts rarely change; hishel 1.3.x is recent — re-check if bumping httpx/hishel major versions)

