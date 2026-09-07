---
phase: 01b-gdelt-rework
status: planned
created: 2026-09-07
depends_on: [01-collectors]
blocks: [02-nlp-pipeline]
requirements: [COLL-03 (rework), COLL-01 (defects), COLL-07 (defects)]
---

# Phase 1b — GDELT Rework

## Why this phase exists

Phase 1 failed verification. GDELT collects zero documents, and GDELT is the primary news source.
Without it the system has only SEC filings: annual cadence, self-reported, one source tier. The
project's stated differentiator — "news-driven signals refresh daily" (`research_notes.md` §1.1) —
does not currently work at all.

This is a rework phase, not a new feature phase. Its only job is to make Phase 1's goal true:
*free data flows into `raw_documents` reliably and idempotently*.

## Root causes (evidence in `01-UAT.md`, `01-VERIFICATION.md`)

1. **Queries are rejected.** GDELT answers `HTTP 200, content-type text/html`, body
   `"Your query was too short or too long."` The D-01 bundle (~30 ESG terms) OR-ed against the
   D-02 alias group exceeds what the endpoint will serve, even under D-03's 400-char chunking.
   A short hand-built query against the same endpoint returns valid JSON, so the API is healthy
   and the fault is ours. **Construction matches the spec; the spec is wrong.**
2. **Rate limit is 5x too fast.** Config assumed 1/s. GDELT's own 429 body states
   *"Please limit requests to one every 5 seconds."* Corrected to 0.2/s in Phase 1, still
   insufficient in practice.
3. **Our IP is now under an extended cooldown.** As of 2026-09-07, even a 25-character query at
   11-second spacing returns 429. Duration unknown. This was self-inflicted by burst probing
   during UAT and constrains how this phase can be developed and tested.

## Design decision: use GDELT's own themes instead of a term bundle

`research_notes.md` §2.3 already identified this and it was never implemented:

> GDELT's `V2Themes` include ready-made ESG-adjacent themes (`ENV_*`, `HUMAN_RIGHTS`,
> `CORRUPTION`, `LABOR_*`) which give a free, cheap pre-filter *before* you spend GPU on BERT.

Replacing a 30-term OR bundle with a handful of `theme:` operators is the core of this rework:

- **Queries get short.** `("Apple Inc") (theme:ENV_CLIMATECHANGE OR theme:ENV_POLLUTION)` is well
  under any plausible length limit, which directly fixes root cause 1.
- **Precision improves.** GDELT's themes are assigned by its own classifier over full article
  text. Our bundle matched the literal word "fine" — a catastrophically ambiguous term that would
  have flooded the corpus with noise for the NLP pipeline to reject in Phase 2.
- **It costs nothing.** Same endpoint, same free tier.

The tradeoff, stated plainly: theme coverage is GDELT's editorial choice, not ours. Some ESG
incidents will carry no matching theme and will be missed. Accept that for v1 — a smaller, cleaner
corpus is worth more to Phase 2 than a large noisy one, and recall can be revisited once there is
a scored baseline to measure it against.

## Tasks

### 01b-01 — Offline first: capture the real GDELT contract as fixtures

**This task must come first.** The IP cooldown means live GDELT calls are unreliable right now,
and the existing fixtures assert a contract the live API refuses (finding N3), so the suite cannot
go red on the real defect.

- [ ] Add a fixture reproducing the real rejection: `HTTP 200`, `content-type: text/html`,
      body `"Your query was too short or too long.\n"`.
- [ ] Add a fixture for the real 429: plain-text body, `Please limit requests to one every 5 seconds...`
- [ ] Add a fixture for a genuine successful `artlist` JSON response (one was captured live on
      2026-09-06 — reuse that shape rather than inventing one).
- [ ] Write tests asserting the collector handles all three correctly: rejection → `status=failed`
      when nothing collected, 429 → retry then give up cleanly, success → documents parsed.
- **DoD:** the test suite fails if the collector regresses to parsing a text/html body as JSON.

### 01b-02 — Theme-based query construction (replaces D-01/D-02/D-03)

- [ ] Map the 8 scoring categories in `config/scoring.yaml` to GDELT `V2Themes`. New config file
      `config/gdelt_themes.yaml`, versioned like every other weight file (D-015).
- [ ] Build **one query per pillar per ticker** — 3 queries, not chunked-by-character-count:
      `("<primary alias>") (theme:X OR theme:Y)`. Keep queries under 200 characters.
- [ ] Use the single best alias, not the full OR-group. `("ExxonMobil Holdings")` is enough; the
      Phase 2 entity gate is what disambiguates, not the search query.
- [ ] Delete the 400-char chunking path. It exists only to work around a query that should never
      have been that long.
- **DoD:** query construction unit-tested against the fixtures from 01b-01; every generated query
  under 200 chars; no query contains more than 6 `theme:` operators.

### 01b-03 — Make the rate limiter and failure reporting honest

Carries the open findings from Phase 1 verification. All are cheap and all touch this code path.

- [ ] **N4** — token buckets initialise capacity to `rate`, so at 0.2/s the bucket never holds a
      whole token and every request pays a ~4s pre-wait. Initialise to at least 1 token.
- [ ] **G-3** — add a per-collector wall-clock budget (default 120s) and stop retrying 429s after
      it expires. This is what caused a 10-minute stall with no progress and no cancellation path.
- [ ] **N1** — `safe_fetch` writes `n_new = len(docs)`, a duplicate of `n_fetched`. The DB shows
      `edgar|ok|12|12` on a re-run that inserted zero rows. Plumb the real insert count back from
      the caller, or drop the column's pretence.
- [ ] **G-2** — a source returning zero documents across every ticker must be loud: non-zero exit
      from `backfill.py` and an explicit summary line. Exiting 0 on "fetched=15 new=15" while the
      primary source collected nothing is how this failure survived three days.
- [ ] **N6** — `_get_rate_for_host` matches with `key in host` / `key.endswith(host)` over an
      unordered dict; make host→rate resolution deterministic.
- **DoD:** behavioural tests for each; `test_rate_limit_behaviour.py` extended, not duplicated.

### 01b-04 — Live re-validation (blocked on the cooldown lifting)

- [ ] Confirm GDELT serves this IP again. **One** request, then stop if it 429s. Do not probe in
      a loop — that is what caused the cooldown.
- [ ] Cold-start `backfill.py --tickers AAPL,XOM` and confirm non-zero GDELT documents land in
      `raw_documents` with `source='gdelt'`.
- [ ] Re-run and confirm zero new rows (dedup still holds).
- [ ] Re-run the full Phase 1 UAT, all 7 tests.
- **DoD:** UAT 1 and UAT 6 pass. Phase 1 verification re-run and clean.

## Explicitly out of scope

- Full article-body fetching. v1 scores headlines (`research_notes.md` §2.3).
- RSS or yfinance-news collectors. If GDELT proves unusable *after* this rework, that is a
  separate architectural decision, not a task to smuggle in here.
- Any Phase 2 NLP work.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| IP cooldown persists for days | Medium | High | 01b-01..03 are fully offline; only 01b-04 needs live access. Do not block the rework on it. |
| Theme coverage misses real ESG incidents | High | Medium | Accepted for v1 and documented. Measure recall against a scored baseline later, not now. |
| GDELT unsuitable as primary source even after rework | Low-Medium | High | If 01b-04 fails, escalate as an architecture decision (RSS fallback), do not patch further. |
| 1 req/5s makes 25-ticker portfolios slow | High | Low | 25 tickers x 3 queries x 5s ~ 6 min. Acceptable for an async job API; note it in Phase 4. |

## Definition of done for Phase 1b

1. `backfill.py --tickers AAPL,XOM` lands documents with `source='gdelt'` in `raw_documents`.
2. A source collecting zero documents fails loudly — non-zero exit and `status=failed`.
3. All 7 Phase 1 UAT tests pass.
4. `gsd-verifier` re-run on Phase 1 returns no blocking gaps.
5. Full suite green; no test asserts a contract the live API refuses.

Only then does Phase 2 start.
