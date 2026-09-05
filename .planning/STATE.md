---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: execution
stopped_at: Phase 1 executed (3/3 plans); UAT in progress 1/7
last_updated: "2026-09-05T00:00:00.000Z"
last_activity: 2026-09-05 — State reconciled with codebase; Phase 3 acceptance fixture written ahead of implementation
progress:
  total_phases: 6
  completed_phases: 1
  total_plans: 18
  completed_plans: 5
  percent: 28
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-09-02)

**Core value:** Every score is fully auditable and reproducible — the exact documents, weights and
formula behind any number are visible, and the system says `insufficient_data` rather than inventing one.
**Current focus:** Phase 1 — Collectors (UAT), then Phase 2 — NLP Pipeline

## Current Position

Phase: 1 of 5 (Collectors) — numbering follows docs/handoff_to_backend.md §1 (Phase 0–5)
Plan: 3 of 3 executed in current phase
Status: Phase 1 code complete and committed; **UAT 1/7 passed, 6 pending** — see
`.planning/phases/01-collectors/01-UAT.md`. Phase 1 is not verified until UAT closes.
Last activity: 2026-09-05 — planning state reconciled against the codebase (it had drifted to 0%
while Phases 0 and 1 were already built and pushed), and the Phase 3 acceptance fixture was
written ahead of any scoring code.

Progress: [██░░░░░░░░] 28% (5/18 plans)

### What actually exists on disk (verified 2026-09-05)

- **Phase 0 — Scaffold: COMPLETE.** Commit `d8902dc`. `config.py` (168 LOC) with `ScoringConfig`
  exposing `version`/`config_hash`, all three `config/*.yaml` populated, `db/schema.sql` (224 lines),
  `db/engine.py`, `scripts/init_db.py`. No `.planning/phases/00-scaffold/` directory exists — Phase 0
  was executed outside the GSD plan flow, so it has no PLAN/SUMMARY artifacts.
- **Phase 1 — Collectors: EXECUTED, awaiting UAT.** Commits `34aed79`..`854236c`. `collectors/`
  is 1,468 LOC: `http.py` (token bucket, tenacity 429/5xx, hishel 24h cache), `base.py`
  (Collector ABC, `RawDocument`, `content_hash`), `gdelt.py`, `edgar.py` (CIK 7d cache, 10-K
  Item 1/1A split), `yfinance_meta.py`, `newsapi.py` (flagged off). Plus `scripts/backfill.py`.
- **Phase 2 — NLP: NOT STARTED.** `nlp/clean.py` (48) and `nlp/registry.py` (43) exist from the
  pre-GSD boilerplate commit `778b961`, not from a Phase 2 plan. `classify.py`, `sentiment.py`,
  `entity.py`, `controversy.py`, `pipeline.py` are all 0 bytes.
- **Phases 3–5: NOT STARTED.** All files in `scoring/`, `api/`, `jobs/` are 0 bytes, except the
  Phase 3 acceptance test at `tests/unit/test_scoring_fixture.py`, which is written and skipping
  until `scoring/` exists.
- **Test suite: 66 passing** (`python3 -m pytest -q`, ~11s), plus 5 skipping Phase 3 fixtures.

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: —
- Total execution time: 0.0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**

- Last 5 plans: —
- Trend: —

*Updated after each plan completion*

## Accumulated Context

### Decisions

Full candidate list in PROJECT.md Key Decisions (D-001..D-018). **Zero are LOCKED** — the ingest set
contains no ADR-class documents. Most relevant to current and near work:

- [Ingest]: D-015 Config over constants — every weight in versioned YAML, `config_hash` stamped on
  every score row. Directly shapes Phase 0.

- [Ingest]: D-011 SQLite + raw SQL + repositories, no ORM, no Postgres in v1.
- [Ingest]: D-008 Two weights — `w_ev` gates sufficiency, `w` drives aggregation. Recommended for
  promotion to a real ADR before Phase 3.

- [Ingest]: D-009 `insufficient_data` is mandatory, never a fabricated 50. Also recommended for ADR.
- [Ingest]: D-016 Backend only — no frontend in this repo.

### Pending Todos

- **Close Phase 1 UAT** — 6 of 7 tests pending in `01-collectors/01-UAT.md`. Phase 1 cannot be
  marked verified until these pass. Tests 3 (never-raise), 4 (EDGAR User-Agent + rate limits) and
  5 (yfinance sustainability isolation) guard the three highest-risk traps in the handoff doc.
- Promote D-008 and D-009 to real ADRs (`/gsd:decide`) — recommended by INGEST-CONFLICTS.md I-001/I-012.
  D-008 (the `w_ev`/`w` split) is now enforced by `tests/unit/test_scoring_fixture.py`, but a passing
  test is not a locked decision — a later plan cannot be blocked against it until an ADR exists.
- Reconcile `src/esg_lens/models.py` (0 bytes) with `architecture.md` §4, which specifies it as the
  pydantic domain-model layer. Collectors use the `RawDocument` dataclass in `collectors/base.py`
  instead. Either move it into `models.py` or delete the empty file and amend the doc.
- Rewrite `db/repositories.py` (42 LOC, `**kwargs` dynamic SQL, only Company + Document repos)
  with explicit columns before Phase 3 — it will not survive append-only score writes and
  `score_contributions` ranking.

### Blockers/Concerns

- ~~W-010~~ **resolved before this file was generated.** The `scoring_methodology.md` §3 prose now
  reads "scaled by 0.6 — a 40% damping", matching its formula. Two agents reported it outstanding from
  a pre-fix read; `grep -rn "damped by 0.4" docs/` returns zero hits. Phase 3 target stands at 20.7.

- Free-model quota availability rotates monthly — run `opencode models` before starting each phase.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Frontend | Dashboard (separate repo) | Out of scope v1 | Ingest |
| NLP | ClimateBERT greenwashing modifier | Post-v1 | Ingest |
| Performance | ONNX Runtime inference | Post-v1 | Ingest |
| API | `DELETE /portfolio/{job_id}` cancellation | Post-v1 | Ingest |
| Collectors | yfinance news + RSS collectors | Post-v1 | Ingest |

## Session Continuity

Last session: 2026-09-03T13:20:05.242Z
Stopped at: Phase 1 context gathered
hand-authored PROJECT.md and ROADMAP.md are at /tmp/gsd-backup/.
Resume file: .planning/phases/01-collectors/01-CONTEXT.md
