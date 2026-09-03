## Conflict Detection Report

Third synthesis pass over the same 7 planning documents, following the W-007/W-008/W-009 edits.
All 7 documents were re-read fresh from disk and the §8 worked example was recomputed again from
first principles.

Result: all ten issues raised across the three passes (W-001..W-010) are resolved and verified
against the files on disk. W-007 and W-008 were introduced by the round-1 edits and caught by the
round-2 pass; W-010 was fixed before this pass ran, but pass 3 and the roadmapper both read a
pre-fix copy of scoring_methodology.md and reported it outstanding — corrected here after a
direct re-read. Nothing is blocking.

### BLOCKERS (0)

None. No ADR-classified documents exist in the ingest set, so there are no LOCKED decisions and
no LOCKED-vs-LOCKED contradictions. No classification is UNKNOWN or low-confidence. Cycle
detection over the `cross_refs` graph (7 nodes, max observed depth 2) found no cycles.

### WARNINGS (0)

(none outstanding)

  W-010  RESOLVED. scoring_methodology.md §3 prose "damped by 0.4x" contradicted its own formula
         pol(d) = 0.6 * sent(d). The prose now reads "scaled by 0.6 — a 40% damping".
         This was fixed on disk BEFORE synthesis pass 3 and the roadmapper ran; both agents read a
         pre-fix copy and reported it outstanding. Verified: grep -rn "damped by 0.4" docs/ returns
         zero hits. The §8 fixture and the Phase 3 acceptance target are unaffected at S_E = 20.7.

### INFO (12)

[INFO] I-001 W-007 confirmed resolved — per-signal contribution is now correct and formally defined
  Note: docs/api_design.md §3 `top_contributors[0].contribution` is now -31.1.
    docs/data_model.md lines 179-183 now carry the explicit definition on the
    `score_contributions.contribution` column: "Signed points this ONE signal contributed to its
    pillar's base score: contribution = 50 * (w * pol) / Sum(w over the pillar). Contributions
    sum to (base_P - 50). The controversy penalty is NOT distributed across contributions; it is
    reported separately on esg_scores.{e,s,g}_penalty."
    Verified against the §8 fixture: Reuters signal = 50 * (0.265 * -0.81) / 0.345 = -31.11 → -31.1.
    Sum check: -31.11 + 50 * (0.080 * 0.48) / 0.345 = -31.11 + 5.57 = -25.54, and
    base_E - 50 = 24.46 - 50 = -25.54. The two agree. The penalty (3.72) is correctly absent from
    the contributions and present on the pillar's `penalty` field in the api_design.md §3 payload.
    A grep for "-25.5" across docs/ and README.md returns no hits.

[INFO] I-002 W-008 confirmed resolved — evidence source breakdown now matches the fixture
  Note: docs/api_design.md §3 now reads `"sources": {"gdelt": 3}`. This is consistent with the
    scoring_methodology.md §8 fixture, in which all three documents use the 90-day news half-life
    (0.5**(10/90) = 0.926, 0.5**(30/90) = 0.794, 0.5**(45/90) = 0.707, tabulated as 0.93 / 0.79 /
    0.71) and the Financial Times document carries the tier-1 w_src of 1.00 rather than the 0.70
    filing weight — i.e. no EDGAR filing is present. A grep for `"edgar": 1` returns no hits.
    The §8 table itself is unchanged, so no downstream figure moved.

[INFO] I-003 W-009 confirmed resolved — "Phase N" now has exactly one meaning
  Note: docs/research_notes.md §3.3 now reads "adopt for the E pillar and the greenwashing
    modifier — but post-v1", followed by an explicit disclaimer: "(Not a handoff_to_backend.md
    phase number: that scheme is Phase 0-5, all within v1. This means a later milestone, after v1
    ships.)" §3.6's stack table marks ClimateBERT "post-v1". docs/architecture.md §6 now reads
    "a documented post-v1 optimisation (not handoff_to_backend.md Phase 3, which is the v1 scoring
    engine)". Greps for "Phase 2, not v1" and "Phase-3 optimisation" both return no hits. The only
    surviving "Phase N" references outside handoff_to_backend.md §1 are in
    opencode_model_routing.md, which correctly uses the handoff numbering.

[INFO] I-004 W-001/W-004 still holding — the two-weight split remains consistent
  Note: Re-verified after this round of edits. docs/scoring_methodology.md §5 and §6.1 define
    `w_ev = w_src*w_rec*w_conf` (gates sufficiency) separately from `w = w_ev*w_cat` (drives
    aggregation); §6.1 and §7 gate on `w_ev`; §6.3 annotates `Σ w_ev` as "NOT Σ w".
    docs/data_model.md persists both as `weight_evidence` / `weight_total` and annotates
    `esg_scores.evidence_weight` as "Σ w_ev(d), NOT Σ w(d)". handoff_to_backend.md Phase 3 and §4
    restate it. No reference to gating on `Σ w`, to the old 1.5 threshold, to score 20.8, or to
    confidence 0.14 survives anywhere in docs/ or README.md.

[INFO] I-005 §8 worked example recomputed a second time — arithmetic still exact
  Note: w_ev: 1.00*0.93*0.95 = 0.8835 (0.883); 0.25*0.79*0.90 = 0.17775 (0.178);
    1.00*0.71*0.88 = 0.6248 (0.625). w: 0.883*0.30 = 0.265; 0.178*0.45 = 0.080;
    0.625*0.45 = 0.281. Polarities: -min(1, 0.6+0.21) = -0.81; 0.6*0.8 = +0.48;
    -min(1, 0.3+0.15) = -0.45. Pillar E gate: 0.883+0.178 = 1.061 >= min_evidence 1.0 → scored.
    Σw = 0.345; weighted polarity = -0.17625/0.345 = -0.51087; base_E = 24.456;
    pen_E = 4.0*0.93*1.00 = 3.72; S_E = 20.736 → 20.7. Pillar G: Σw_ev = 0.625 < 1.0 → null.
    Pillar S: no documents → null. Composite = 20.7 (Energy E weight 0.50 renormalised to 1.0).
    Confidence = 0.4*min(1, 3/30) + 0.3*min(1, 1.686/10) + 0.3*(1/3)
               = 0.0400 + 0.0506 + 0.1000 = 0.1906 → 0.19.
    Every published figure checks out and the gate is satisfied under the rule exactly as stated.
    The 0.6 damping factor is confirmed in both the formula block and the §3 prose.

[INFO] I-006 Fixture values propagate consistently to every consumer
  Note: docs/api_design.md §3 carries composite 20.7, confidence 0.19, E score 20.7 with penalty
    3.72 and n_signals 2, S and G `insufficient_data` (G n_signals 1, S n_signals 0),
    `pillar_weights` renormalised to E 1.0 with an explanatory note, `weight_evidence` 0.883,
    `weight` 0.265 and `contribution` -31.1. docs/handoff_to_backend.md Phase 3 Definition of Done
    names 20.7 / insufficient_data / 20.7 / 0.19 within 0.1 (0.01 for confidence). All agree.

[INFO] I-007 W-002 still holding — job cancellation coherently deferred
  Note: docs/api_design.md §4 marks `DELETE /api/v1/portfolio/{job_id}` "Deferred past v1";
    docs/architecture.md §5.4 states the job interface is only `enqueue`/`get`/`update` with
    "No `cancel` in v1"; docs/handoff_to_backend.md Phase 4 says "Do not implement". The
    `cancelled` value is deliberately retained in the `jobs.status` CHECK and in the
    api_design.md §2 status union so adding cancellation later needs no migration.

[INFO] I-008 W-003 still holding — retention has a single named owner
  Note: docs/architecture.md §5.4 owns the startup retention sweep (delete `jobs` older than
    `retention_days`, default 30, cascading `job_items`, with `esg_scores.job_id` ON DELETE SET
    NULL preserving score history) and explicitly claims "the 30-day retention promise in
    api_design.md §2". api_design.md §2 points back at architecture.md §5.4.
    handoff_to_backend.md Phase 4 lists both startup sweeps (stale >1h, and retention 30 days).

[INFO] I-009 W-005 still holding — 'rss' is not a selectable source
  Note: `raw_documents.source` CHECK is IN ('gdelt','edgar','yfinance','newsapi'). The only
    remaining mention of 'rss' is an adjacent comment instructing that it be added only alongside
    an actual collector, pointing at research_notes.md §2.4 where RSS is an unevaluated
    alternative. api_design.md §1 `sources` matches the CHECK exactly.

[INFO] I-010 W-006 still holding — /documents join and version selection specified
  Note: docs/api_design.md §4 states that `pillar` and `included` are columns on `esg_signals`
    rather than `raw_documents`, that the endpoint joins raw_documents → esg_signals, that it
    filters on the signal row whose `model_version` matches the one behind the ticker's latest
    `esg_scores` row, and that documents with no signal row at that version are returned only when
    no filter is supplied. Consistent with `esg_signals UNIQUE (document_id, model_version)`.

[INFO] I-011 'yfinance' retained as a source CHECK value with no v1 document collector
  Note: Re-recorded at the coordinator's direction as INFO, not a warning. `raw_documents.source`
    and the api_design.md §1 `sources` enum both admit 'yfinance', and research_notes.md §2.1
    names `Ticker.news` as a legitimate future headline feed, but handoff_to_backend.md Phase 1
    scopes `yfinance_meta.py` to company metadata, alias seeding and `external_esg_score` only.
    Selecting `sources: ["yfinance"]` in v1 therefore returns zero documents. The reserved enum
    value costs nothing; the roadmapper should simply not assume a yfinance news collector exists
    in v1.

[INFO] I-012 No precedence contest arose; decision-shaped DOC statements kept as candidates
  Note: The ingest set is 4 SPEC and 3 DOC, with no ADR and no PRD and no per-doc `precedence`
    override, so the ADR > SPEC > PRD > DOC ordering never had to break a tie and nothing was
    silently overridden. The bolded "Decision:" lines in research_notes.md and the ADR-shaped
    technology table in architecture.md §6 are recorded in intel/decisions.md as candidates
    (D-001..D-018), none locked. Promoting D-008 (the two-weight split) and D-009
    (`insufficient_data` is mandatory) to real ADRs remains recommended — the source text already
    treats both as inviolable, and W-010 showed how easily prose can drift from an authoritative formula.
