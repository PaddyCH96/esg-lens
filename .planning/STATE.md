---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: planning
stopped_at: Phase 1 context gathered
last_updated: "2026-09-03T13:20:05.622Z"
last_activity: 2026-09-02 — Canonical PROJECT/REQUIREMENTS/ROADMAP/STATE generated from the
progress:
  total_phases: 6
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-09-02)

**Core value:** Every score is fully auditable and reproducible — the exact documents, weights and
formula behind any number are visible, and the system says `insufficient_data` rather than inventing one.
**Current focus:** Phase 0 — Scaffold

## Current Position

Phase: 0 of 5 (Scaffold) — numbering follows docs/handoff_to_backend.md §1 (Phase 0–5)
Plan: 0 of 2 in current phase
Status: Ready to plan
Last activity: 2026-09-02 — Canonical PROJECT/REQUIREMENTS/ROADMAP/STATE generated from the
7-document ingest, replacing the earlier hand-authored PROJECT.md and ROADMAP.md.

Progress: [░░░░░░░░░░] 0% (0/18 plans)

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

- Promote D-008 and D-009 to real ADRs (`/gsd:decide`) — recommended by INGEST-CONFLICTS.md I-001/I-012.
- Confirm the `[VERIFY]`-marked external quota claims in research_notes.md §2 before Phase 1.

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
