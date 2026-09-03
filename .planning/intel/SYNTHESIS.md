# Synthesis — ESG Lens doc ingest

Mode: `new`. Third pass over the same 7 documents, after the W-007/W-008/W-009 source fixes.
Generated from `.planning/intel/classifications/*.json` plus a fresh read of every source document.

---

## Document inventory

7 documents, 0 UNKNOWN, 0 low-confidence.

- SPEC (4): `docs/api_design.md` (high), `docs/data_model.md` (high),
  `docs/scoring_methodology.md` (high), `docs/architecture.md` (medium)
- DOC (3): `docs/research_notes.md` (medium), `docs/handoff_to_backend.md` (medium),
  `docs/opencode_model_routing.md` (high)
- ADR (0), PRD (0)

Cycle detection: `cross_refs` graph has 7 nodes and max observed depth 2. No cycles.
Precedence: default `ADR > SPEC > PRD > DOC`; no per-doc override on any classification.
With no ADRs and no PRDs, the ordering never had to break a tie — nothing was silently overridden.

## Decisions

18 decision candidates → `intel/decisions.md` (D-001..D-018).
Locked decisions: 0 — no ADR-structured documents exist. Candidates were extracted from the
bolded "Decision:" lines in `research_notes.md` and the ADR-shaped technology-choice table in
`architecture.md` §6; per instruction, none were auto-locked.

Recommended for promotion to real ADRs: D-008 (the `w_ev` / `w` two-weight split) and D-009
(`insufficient_data` is a mandatory state, never a fabricated 50). The source text already treats
both as inviolable, and W-010 below shows how easily an authoritative formula can drift when it
lives only in prose.

## Requirements

13 requirements → `intel/requirements.md`: REQ-scaffold-config, REQ-collectors, REQ-nlp-pipeline,
REQ-scoring-engine, REQ-api-portfolio-analyze, REQ-api-job-poll, REQ-api-company-score,
REQ-api-supporting, REQ-error-contract, REQ-persistence-schema, REQ-jobs-runner,
REQ-validation-sensitivity, REQ-observability-security.

No PRDs exist, so requirements were derived from the SPEC contracts and the explicit
"Definition of done" blocks in `handoff_to_backend.md` §1. Each requirement has exactly one
acceptance source, so there are no competing acceptance variants.

## Constraints

12 constraints → `intel/constraints.md` (C-001..C-012), covering the scoring formulas, the
category/pillar/sector weight tables, source credibility tiers, the controversy lexicon, HTTP and
external-API limits, the API contract surface, the SQLite schema constraints, the CPU performance
budget, v1 scope boundaries, testing rules, and transparency requirements.

All pillar-weight and category-weight sets were arithmetically verified to sum to 1.00.
C-008 now carries the newly formalised `score_contributions.contribution` definition.

## Context

9 topics → `intel/context.md`: project positioning, approach-family selection, data source
evaluation, model evaluation detail, implementer ground rules, build phase order, assumptions to
challenge, implementation traps, and build-tooling routing.

`docs/opencode_model_routing.md` documents the authoring workflow, not the product — it should not
generate product requirements or roadmap phases.

## Conflicts

0 blockers, 1 warning, 12 info. Detail in `.planning/INGEST-CONFLICTS.md`.

### Fixes verified this pass

W-007, W-008 and W-009 all landed and are internally consistent:

- **W-007** — `api_design.md` §3 `contribution` is now -31.1, and `data_model.md` now defines the
  column as `50 * (w * pol) / Σ(w over the pillar)`, notes that contributions sum to
  `(base_P - 50)`, and states the controversy penalty is reported on `esg_scores.{e,s,g}_penalty`
  rather than distributed. Checked: `50 * (0.265 * -0.81) / 0.345 = -31.11`, and
  `-31.11 + 5.57 = -25.54 = 24.46 - 50`. Both halves agree. No "-25.5" survives anywhere.
- **W-008** — `"sources": {"gdelt": 3}`, consistent with all three fixture documents using the
  90-day news half-life and the FT document sitting at tier-1 w_src 1.00. No `"edgar": 1` survives.
- **W-009** — "post-v1" now labels ClimateBERT (`research_notes.md` §3.3, §3.6) and ONNX Runtime
  (`architecture.md` §6), each with an explicit disclaimer that this is not a
  `handoff_to_backend.md` phase number. "Phase N" now has exactly one meaning across the set.

The earlier W-001..W-006 fixes were re-checked and still hold.

### Fix that did NOT land

- **W-010** — `scoring_methodology.md` §3 was reported as changed to "scaled by 0.6 (a 40%
  damping)". It was not: line 95 still reads "damped by 0.4x", nine lines below a formula block
  reading `pol(d) = 0.6 * sent(d)`. This is the one remaining item and the only warning. The
  factor matters: under a 0.4 reading the §8 fixture's `S_E` becomes 18.9 instead of 20.7, which
  would break the Phase 3 acceptance test. The formula and the fixture are both already correct —
  only the prose line needs the edit.

The `yfinance` source-enum observation is re-recorded as INFO (I-011) at the coordinator's
direction, not as a warning.

## Files

- `.planning/intel/decisions.md`
- `.planning/intel/requirements.md`
- `.planning/intel/constraints.md`
- `.planning/intel/context.md`
- `.planning/INGEST-CONFLICTS.md` (full detail)
