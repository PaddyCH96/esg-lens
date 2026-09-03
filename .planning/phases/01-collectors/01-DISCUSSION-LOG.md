# Phase 1: Collectors - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-03
**Phase:** 1-collectors
**Areas discussed:** Query construction

---

## Query construction

| Option | Description | Selected |
|--------|-------------|----------|
| Broad (Recommended) | Category + controversy tiers 1-3 triggers (~30 terms), max recall | ✓ |
| Tight | Only 8 FinBERT-ESG categories, minimal query length |  |
| Hybrid | Tight for filings, broad for news; length-capped at 400 chars |  |

**User's choice:** Broad (Recommended)
**Notes:** User wants incident/controversy recall over precision; category-only would miss severe penalties.

## Query construction — aliases

| Option | Description | Selected |
|--------|-------------|----------|
| All aliases verbatim | Simple OR-group of every alias; rely on entity gate downstream to filter |  |
| Filter short/ambiguous | Drop aliases ≤4 chars or in stoplist; quoted phrases only (recommended) | ✓ |
| Let user decide | No filtering in query; entity gate is the only filter |  |

**User's choice:** Filter short/ambiguous
**Notes:** Aware of "Apple the fruit" noise; prefers filtering before GDELT call.

## Query construction — syntax

| Option | Description | Selected |
|--------|-------------|----------|
| Quoted + chunk if needed | Quote multi-word phrases ("oil spill", "Apple Inc"), split into 2 queries if >400 chars, merge results | ✓ |
| No quotes, truncate | Single query, unquoted terms, truncate bundle to fit 400 chars |  |
| Quoted, strict cap | Quoted phrases, truncate bundle to fit, log warning when truncated |  |

**User's choice:** Quoted + chunk if needed
**Notes:** Wants auditability when query exceeds GDELT limit; expects log warning, never silent truncation.

---

## Claude's Discretion

EDGAR extraction, Failure & observability, Caching & refresh — left to standard approaches per docs/handoff_to_backend.md Phase 1 and REQUIREMENTS.md COLL-01..07. Also domain normalization details, token-bucket tuning, hishel TTL, yfinance fallback handling.

## Deferred Ideas

None — discussion stayed within phase scope.
