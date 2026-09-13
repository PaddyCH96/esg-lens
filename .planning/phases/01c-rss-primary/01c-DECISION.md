---
phase: 01c-rss-primary
status: complete
created: 2026-09-14
supersedes: 01b-gdelt-rework (01b-04 unreachable)
unblocks: [02-nlp-pipeline]
---

# ADR — RSS replaces GDELT as the primary news source

## Status
**Accepted and implemented, 2026-09-14.**

## Context

GDELT's DOC API refused this IP for 8+ days: HTTP 429 on a single short single-theme query, the
exact shape the 01b-02 rework produces. The block is endpoint-and-IP specific, not a network fault
and not GDELT-wide — `data.gdeltproject.org` returned HTTP 200 for a 2MB file in the same session
that `api.gdeltproject.org` returned 429.

Phase 1b's rework was sound: queries went from 913 characters to 159-230 using GDELT's own
verified theme names, and every one of its rate-limiter and failure-reporting fixes was confirmed
under live conditions. None of it mattered, because the endpoint would not serve us at all.

`01b-PLAN.md` anticipated this exactly: *"If 01b-04 fails, escalate as an architecture decision
(RSS fallback), do not patch further."*

## Decision

**RSS becomes the primary news source.** Two feed kinds, both probed live before any code was
written:

| Feed | Role | Probed 2026-09-14 |
|---|---|---|
| Google News search RSS | Primary — queryable, honours `when:Nd` | 100 items returned |
| Yahoo per-ticker RSS | Secondary — entity-guaranteed | 16 items returned |

Google News search RSS is the closest functional replacement for the DOC API: it takes a query,
supports a lookback window, and returns ~100 items. Yahoo's per-ticker feed is tagged to the
ticker itself, so entity identity is guaranteed and "Apple the fruit" cannot occur — in exchange
it is generic financial news with no ESG filter, which the Phase 2 relevance gate handles.

GDELT's collector is **retained, not deleted**. The code is sound and the block may not be
permanent. It is opt-in via `ESG_ENABLE_GDELT=1`.

## Consequences

**Measured, same two tickers, same machine:**

    GDELT  0 news documents   in 3m 23s   (exit 2, dead_sources=['gdelt'])
    RSS    227 news documents in 17s      (exit 0, dead_sources=[])

Corpus: 242 documents total (227 RSS + 15 EDGAR), spanning 2026-06-16 to 2026-09-13, zero null
domains, zero null titles, zero null URLs, and zero `news.google.com` leakage.

**What we gain**
- A news corpus at all, which is the entire premise of the project.
- Real publisher domains for the source-credibility tier, taken from `<source url="...">`.
- No API key, no documented rate limit, no quota to exhaust.

**What we lose**
- GDELT's V2Themes. Google News has no equivalent, so per-pillar term queries return — but
  deliberately short and high-signal, not the 30-term bundle that broke GDELT. `"fine"` is
  excluded as catastrophically ambiguous.
- GDELT's multilingual reach. RSS here is English-only, which narrows an already-stated v1 limit.
- Recall is unmeasured, as it was with GDELT, and unmeasurable until there is a scored baseline.

**New risks**
- Google News RSS is undocumented, with no stability guarantee. Treated exactly as yfinance is:
  wrapped, cached, degrades gracefully. Self-imposed 0.5 req/s — a courtesy ceiling, not an
  instruction. Being throttled off GDELT for 8+ days is what finding a real limit the hard way costs.
- ~23% of the corpus (52/227) came from the Yahoo ticker feed and is generic financial news
  ("XOM Stock Quote Price and Forecast"). By design: the FinBERT-ESG relevance gate drops it in
  Phase 2. It does mean the Phase 2 gate carries more load than it would have with GDELT themes.
- Company IR domains appear (`investor.exxonmobil.com`) that the static tier-4 promotional list in
  `sources.yaml` does not match. Phase 2 should treat `investor.*` / `ir.*` subdomains as tier 4,
  or press releases will be weighted as independent coverage.

## Implementation

- `src/esg_lens/collectors/rss.py` — RssCollector
- `config/rss_feeds.yaml` — feeds, per-pillar terms, limitations
- `tests/integration/test_rss.py` — 13 tests against fixtures captured live
- `tests/fixtures/rss_*.xml` — 3 real captured responses
- `raw_documents.source` CHECK regains `'rss'`, removed in Phase 1 (W-005) precisely because no
  collector existed. Adding the collector is what earns it back.
- `backfill.py` — RSS primary, GDELT behind `ESG_ENABLE_GDELT=1`

## Verification

UAT 1 and UAT 6, which failed under GDELT, now pass:
- **UAT 1** cold start: 242 documents, `dead_sources=[]`, exit 0.
- **UAT 2** dedup: re-run fetched 242, inserted **0**, every `n_new=0`.
- **UAT 6** query construction: budget-bounded, verified against captured fixtures.

Suite: **99 passed, 17 skipped, 6.7s.**
