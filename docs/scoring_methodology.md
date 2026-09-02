# Scoring Methodology — ESG Lens

**Design premise:** every number must be traceable back to the documents that produced it.
No learned end-to-end model. All weights live in `config/scoring.yaml`, are versioned, and are
stamped onto every stored score as `methodology_version`.

---

## 0. Notation

| Symbol | Meaning |
|---|---|
| `d` | a document (news headline or filing section) |
| `P ∈ {E, S, G}` | pillar |
| `c` | fine-grained ESG category (9 of them) |
| `rel(d)` | ESG relevance probability from FinBERT-ESG |
| `pol(d) ∈ [-1, 1]` | ESG polarity |
| `w_src(d)` | source-credibility weight |
| `w_rec(d)` | recency decay weight |
| `w_cat(c)` | category materiality weight |
| `S_P` | pillar sub-score, 0–100 |
| `S` | composite score, 0–100 |

---

## 1. Pipeline overview

```
raw doc
  ├─ 1. Entity gate      → is this actually about our company?   (spaCy NER + alias match)
  ├─ 2. Relevance gate   → is this ESG at all?                   (FinBERT-ESG: class != Non-ESG)
  ├─ 3. Category         → which of 9 categories → which pillar  (FinBERT-ESG argmax)
  ├─ 4. Polarity         → ESG-direction, not market-direction   (FinBERT sentiment + controversy rules)
  ├─ 5. Weighting        → source × recency × category × confidence
  └─ 6. Aggregation      → per-pillar → composite → sector-relative percentile
```

Documents failing gates 1 or 2 are **stored with `included=false` and a reason**, never silently
dropped. The API can then show "142 documents seen, 38 ESG-relevant".

---

## 2. Category → pillar map and materiality weights

`finbert-esg-9-categories` outputs map as follows. Weights are *within-pillar* and sum to 1.0
per pillar. Defaults are uniform-ish with a deliberate tilt toward categories with hard
financial consequences; they are a **starting point to be tuned, not a claim of truth**.

| Category | Pillar | `w_cat` |
|---|---|---|
| Climate Change | E | 0.45 |
| Natural Capital | E | 0.25 |
| Pollution & Waste | E | 0.30 |
| Human Capital | S | 0.40 |
| Product Liability | S | 0.35 |
| Community Relations | S | 0.25 |
| Corporate Governance | G | 0.45 |
| Business Ethics & Values | G | 0.55 |
| Non-ESG | — | dropped at gate 2 |

**Pillar weights in the composite** (`config/scoring.yaml`, sector-overridable):

```yaml
pillar_weights:
  default: {E: 0.34, S: 0.33, G: 0.33}
  overrides:
    Energy:                 {E: 0.50, S: 0.25, G: 0.25}
    Utilities:              {E: 0.45, S: 0.25, G: 0.30}
    Financial Services:     {E: 0.15, S: 0.25, G: 0.60}
    Technology:             {E: 0.25, S: 0.35, G: 0.40}
    Healthcare:             {E: 0.20, S: 0.45, G: 0.35}
    Consumer Defensive:     {E: 0.35, S: 0.40, G: 0.25}
```
Rationale: materiality is sector-dependent (the SASB principle). An oil major's E exposure
is not comparable to a bank's; a bank's G exposure is where its risk actually lives.

---

## 3. Polarity: combining sentiment with controversy

FinBERT sentiment is *market*-directional. Convert to ESG-directional:

```
sent(d)  = P(positive) - P(negative)          # ∈ [-1, 1], from ProsusAI/finbert
ctrl(d)  = controversy severity ∈ {0, 1, 2, 3} from the YAML lexicon (§4)

if ctrl(d) > 0:
    pol(d) = -min(1.0, 0.3 * ctrl(d) + 0.3 * max(0, -sent(d)))   # forced negative
else:
    pol(d) = 0.6 * sent(d)                                        # damped: praise is cheap
```

The asymmetry is intentional and is the core editorial judgment of this methodology:
**a detected controversy overrides sentiment and forces a negative polarity**, while positive
sentiment is damped by 0.4× because favourable coverage is frequently company-sourced PR.

---

## 4. Controversy lexicon (`config/controversy_lexicon.yaml`)

Deterministic, auditable, versioned. Severity tiers:

| Severity | Meaning | Example triggers |
|---|---|---|
| 3 | Severe / systemic | `oil spill`, `bribery`, `fraud`, `child labor`, `fatality`, `class action`, `criminal probe` |
| 2 | Material | `fine`, `penalty`, `lawsuit`, `recall`, `strike`, `investigation`, `data breach`, `layoffs` |
| 1 | Minor / allegation | `criticized`, `alleged`, `scrutiny`, `protest`, `complaint`, `downgrade` |
| 0 | None | — |

Matching is spaCy `PhraseMatcher` over lemmas, with a negation guard
(`cleared of`, `dismissed`, `settled without`, `dropped`) that demotes severity by 1.

---

## 5. Weights

**Recency** — exponential decay, half-life 90 days for news, 365 days for filings:
```
w_rec(d) = 0.5 ** (age_days(d) / half_life)
```
Documents older than `max_age_days` (default 730) are excluded entirely.

**Source credibility** — static tier map in `config/sources.yaml`:

| Tier | `w_src` | Examples |
|---|---|---|
| 1 — wire/major | 1.00 | reuters.com, apnews.com, bloomberg.com, ft.com, wsj.com |
| 2 — reputable | 0.80 | national broadsheets, established trade press |
| 3 — unknown | 0.50 | anything not listed (the default) |
| 4 — promotional | 0.25 | prnewswire.com, businesswire.com, globenewswire.com, company IR domains |
| filing | 0.70 | SEC EDGAR — authoritative but self-reported |

Tier 4 exists specifically so press releases cannot inflate a score.

**Confidence** — the classifier's own certainty:
```
w_conf(d) = rel(d) if rel(d) >= tau else 0      # tau = 0.60 relevance threshold
```

**Final document weight:**
```
w(d) = w_src(d) * w_rec(d) * w_conf(d) * w_cat(category(d))
```

---

## 6. Aggregation

### 6.1 Pillar sub-score
For pillar `P` with document set `D_P`:

```
raw_P    = Σ_{d∈D_P} w(d) * pol(d)  /  Σ_{d∈D_P} w(d)        # ∈ [-1, 1], weighted mean polarity
```

Map to 0–100 with a neutral midpoint of 50:
```
base_P   = 50 + 50 * raw_P
```

**Controversy penalty** — a weighted mean lets a flood of mild-positive PR bury one severe
incident. Apply an explicit, separately-reported penalty so severe events cannot be averaged away:
```
pen_P    = min(pen_cap, Σ_{d∈D_P, ctrl(d)>0} k[ctrl(d)] * w_rec(d) * w_src(d))
           where k = {1: 1.0, 2: 4.0, 3: 12.0}, pen_cap = 40
S_P      = clamp(base_P - pen_P, 0, 100)
```

**Insufficient data:** if `Σ w(d) < min_evidence` (default 1.5, roughly 2–3 solid recent
documents), do **not** emit a score. Emit `null` with `status = "insufficient_data"`.
This is a hard rule — a fabricated 50 is worse than an honest gap.

### 6.2 Composite
```
S = Σ_P pillar_weight[sector][P] * S_P          # over pillars with a non-null S_P,
                                                # weights renormalised over those present
```

### 6.3 Confidence score (reported alongside, never folded into `S`)
```
confidence = clamp( 0.4 * min(1, n_docs/30)
                  + 0.3 * min(1, Σw / 10)
                  + 0.3 * pillar_coverage_fraction , 0, 1)
```

### 6.4 Sector-relative percentile
Absolute scores are only meaningful within a peer group. Where ≥5 peers in the same
sector have been scored, also report `sector_percentile`. Below 5, report `null` — not a
percentile computed from three companies.

---

## 7. Pseudocode

```python
def score_company(ticker, config) -> CompanyScore:
    meta = metadata_repo.get(ticker)                 # sector drives weights
    docs = doc_repo.fetch(ticker, since=now - config.max_age_days)

    signals = []
    for d in docs:
        # --- Gate 1: entity ---
        if not entity_matches(d, meta.aliases):
            record_excluded(d, "entity_mismatch"); continue

        # --- Gate 2 + 3: relevance and category ---
        cat, rel = finbert_esg.classify(d.text)
        if cat == "Non-ESG" or rel < config.tau:
            record_excluded(d, "not_esg_relevant"); continue
        pillar = CATEGORY_TO_PILLAR[cat]

        # --- Gate 4: polarity ---
        sent = finbert_sentiment.score(d.text)       # P(pos) - P(neg)
        ctrl = controversy.severity(d.text)          # 0..3, lexicon + negation guard
        pol  = (-min(1.0, 0.3*ctrl + 0.3*max(0, -sent))) if ctrl > 0 else 0.6*sent

        # --- Gate 5: weights ---
        w = (source_weight(d.domain, d.doc_type)
             * 0.5 ** (age_days(d) / half_life(d.doc_type))
             * rel
             * config.category_weights[cat])

        signals.append(Signal(d.id, pillar, cat, pol, ctrl, w, rel))

    # --- Gate 6: aggregate ---
    pillars = {}
    for P in ("E", "S", "G"):
        sp = [s for s in signals if s.pillar == P]
        W  = sum(s.w for s in sp)
        if W < config.min_evidence:
            pillars[P] = None; continue
        base = 50 + 50 * (sum(s.w * s.pol for s in sp) / W)
        pen  = min(config.pen_cap,
                   sum(config.k[s.ctrl] * recency(s) * source(s)
                       for s in sp if s.ctrl > 0))
        pillars[P] = clamp(base - pen, 0, 100)

    pw      = config.pillar_weights.get(meta.sector, config.pillar_weights["default"])
    present = {P: w for P, w in pw.items() if pillars[P] is not None}
    if not present:
        return CompanyScore(ticker, composite=None, status="insufficient_data")
    total     = sum(present.values())
    composite = sum(pillars[P] * w / total for P, w in present.items())

    return CompanyScore(
        ticker=ticker, composite=round(composite, 1), pillars=pillars,
        confidence=confidence(signals, pillars),
        n_documents=len(docs), n_signals=len(signals),
        top_contributors=top_k_by_abs_contribution(signals, k=5),
        methodology_version=config.version, status="ok",
    )
```

---

## 8. Worked example (illustrative)

`XOM`, 3 documents, sector `Energy` → pillar weights E .50 / S .25 / G .25.

| Doc | Cat | src | age | rel | sent | ctrl | pol | w |
|---|---|---|---|---|---|---|---|---|
| "Regulator fines XOM over refinery leak" | Pollution & Waste | reuters (1.00) | 10d | 0.95 | -0.7 | 2 | -0.81 | 1.00·0.93·0.95·0.30 = 0.265 |
| "XOM announces net-zero 2050 pledge" | Climate Change | prnewswire (0.25) | 30d | 0.90 | +0.8 | 0 | +0.48 | 0.25·0.79·0.90·0.45 = 0.080 |
| "XOM board faces say-on-pay revolt" | Corporate Governance | ft (1.00) | 45d | 0.88 | -0.5 | 1 | -0.45 | 1.00·0.71·0.88·0.45 = 0.281 |

- **E:** ΣW = 0.345, weighted pol = (0.265·−0.81 + 0.080·0.48)/0.345 = **−0.51** →
  base 24.5; penalty = 4.0·0.93·1.00 = 3.7 → **S_E ≈ 20.8**
- **G:** single doc, ΣW = 0.281 < min_evidence 1.5 → **null, insufficient_data**
- **S:** no docs → **null**
- **Composite:** only E present → renormalised to 1.0 → **20.8, confidence ≈ 0.14**

The low confidence is the honest headline here, and the API surfaces it as prominently as
the score. Note also how the tier-4 net-zero press release barely moves E — by design.

---

## 9. Known weaknesses (document these in the README)
- Weights are **expert-judgment priors, not fitted**. No ground truth exists to fit them to.
- Sentiment→ESG-polarity mapping is heuristic; see research notes §3.2.
- Lexicon-based controversy detection misses novel phrasings and non-English coverage.
- Headline-only analysis truncates nuance.
- The score is **not comparable across sectors** except via `sector_percentile`.
- Sensitivity analysis is required before publishing: perturb each weight ±25% and report
  rank stability. Ship this as `scripts/sensitivity.py` and put the results in the README.
