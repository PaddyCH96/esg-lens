# Phase 2: NLP Pipeline - Research

**Researched:** 2026-09-14
**Domain:** CPU transformer inference (HF `transformers` 4.44.0), spaCy 3.7.5 rule matching, deterministic near-duplicate detection, SQLite persistence
**Confidence:** HIGH for every API claim (all executed against the pinned `.venv` in this session); MEDIUM for the near-duplicate threshold (empirically tuned on a 101-headline corpus, single ticker)

## Summary

Everything the planner needs to know that is *not* already in `02-CONTEXT.md` reduces to five
things, all established by running code in this session rather than by recalling documentation.

**First, and most important: `PhraseMatcher(attr="LEMMA")` silently fails on Title Case
headlines.** spaCy tags Title Case nouns as `PROPN` and the lemmatizer returns the *capitalised
surface form*, so the lemma of `Investigation` is `Investigation`, not `investigation`, and the
match never fires. Measured on the real corpus: **6 of 101 headlines produce a controversy hit as
written; 39 of 101 produce one when the text is lowercased first.** NLP-06 implemented literally
would detect controversies on roughly 6% of news and the test suite would never notice, because
stub-model tests do not exercise the lemmatizer. The fix is one line in `clean.py` and it belongs
in the plan as an explicit, tested requirement.

**Second, the near-duplicate question has an uncomfortable but well-evidenced answer:** no
deterministic lexical measure separates *rewritten* syndication from genuinely distinct stories.
The Tata pair the phase context cites scores J=0.40 while a pair of genuinely different Microsoft
lawsuits scores J=0.46 — the bands overlap, and they overlap for token Jaccard, token-set ratio,
IDF-weighted containment and character trigram Dice alike. The recommendation is therefore a
**high threshold (J >= 0.70) that catches verbatim and near-verbatim syndication at 100% precision
on the labelled set, and explicitly does not chase rewrites.** On the live corpus that collapses
12 of 101 documents into 7 clusters — and every one of those clusters is the same securities
class action re-issued by a different law firm, which is exactly the `Σ w_ev` inflation D-2-02
exists to stop.

**Third, three verified API facts that would otherwise become Phase-1-style spec failures:**
FinBERT-ESG's nine labels match `config/scoring.yaml`'s `category_weights` keys byte-for-byte
(including `Business Ethics & Values` and `Pollution & Waste`); `ProsusAI/finbert` emits lowercase
`positive|negative|neutral`; and `pipeline(..., truncation=True)` **without** `max_length=512`
raises `RuntimeError: The size of tensor a (1402) must match the size of tensor b (512)` on a long
filing section, because `tokenizer.model_max_length` is the `1e30` sentinel for both models.

**Fourth, two data-layer gaps block requirements as written.** `company_aliases` currently has
**zero rows** and `companies.name` for MSFT is literally `'MSFT'` (yfinance 404'd), so the NLP-03
entity gate has nothing to match against and would reject 100% of `google_news` documents. And the
RSS collector records no feed identifier — Yahoo rows get `raw_json = NULL`, Google rows get
`{"_rss_pillar": ...}` — so D-2-03's per-feed trust has nothing reliable to key on.

**Primary recommendation:** replace `nlp/clean.py` and `nlp/registry.py` wholesale; lowercase news
text before lemma matching and prove it with a corpus-derived test; use token-set Jaccard >= 0.70
with union-find clustering per ticker; pass `max_length=512` everywhere; add `_rss_feed` to
`raw_json` and an `entity_gate` key per feed; and write `scoring/weights.py`'s three arithmetic
functions in this phase while leaving `polarity` NULL for Phase 3.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Model loading / lifetime | Process singleton (`nlp/registry.py`) | FastAPI `lifespan` (Phase 4) | Load is 1.8s and must happen once; registry owns the cache, lifespan only triggers warm-up |
| Text normalisation, sentence split | `nlp/clean.py` | — | Pure string transform; no model inference beyond spaCy |
| Near-duplicate collapse | `nlp/clean.py` | — | D-2-02 locks this upstream of the gates so Phase 3 never sees the inflation |
| Entity identity | `nlp/entity.py` + `config/rss_feeds.yaml` | `company_aliases` table | Trust is a *source* property (config); matching is a *document* operation (code) |
| ESG relevance + category | `nlp/classify.py` | `config/scoring.yaml` (`tau_*`) | Model emits label+score; thresholds are config per D-015 |
| Financial sentiment | `nlp/sentiment.py` | — | Emits `P(pos)-P(neg)` only. **Not** ESG polarity |
| Controversy severity | `nlp/controversy.py` + `config/controversy_lexicon.yaml` | — | Deterministic, auditable, versioned — no model |
| Weight arithmetic (`w_src`,`w_rec`,`w_cat`,`w_ev`,`w`) | `scoring/weights.py` (pure) | `nlp/pipeline.py` calls it | NLP-07 must persist these; `scoring/` owns the formulas so Phase 3 does not redefine them |
| Polarity `pol(d)` | **Phase 3** (`scoring/weights.polarity`) | — | Left NULL by Phase 2. See "The Phase 2 / Phase 3 line" below |
| Persistence to `esg_signals` | `nlp/pipeline.py` | `db/engine.py` | Owns the `UNIQUE(document_id, model_version)` no-op contract |

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-2-01: Per-doc-type relevance threshold.** `tau_news = 0.45`, `tau_filing_section = 0.60`. Both
in `config/scoring.yaml` per D-015.

**D-2-02: Near-duplicates collapsed in cleaning, best kept.** Detect near-duplicate documents **per
ticker** by normalised-title similarity. Keep the one with the highest source-credibility tier;
store the others with `included=false` and `exclusion_reason='duplicate'`. Left to the planner: the
similarity measure and threshold. Must be deterministic and cheap — no model inference. Record the
retained document's id on the excluded rows if practical.

**D-2-03: Per-source entity-gate trust.** Skip the entity gate for feeds where the provider asserts
the ticker (`yahoo_ticker` RSS, EDGAR filings); apply the full spaCy NER + alias match to
`google_news`. Trust is a **per-feed property declared in `config/rss_feeds.yaml`**, not hard-coded
in the gate.

**D-2-04: Filing sections — sentence-split, classify, aggregate to one signal.** spaCy splits the
section into sentences; FinBERT-ESG classifies them batched; the section produces **one**
`esg_signals` row. Category = dominant ESG label across relevant sentences. Relevance = mean
confidence over ESG-relevant sentences only. Left to the planner: sentence cap per section, and how
sentiment/controversy aggregate across sentences — controversy should almost certainly take the
**max** severity, not the mean, consistent with `scoring_methodology.md` §6.1.

### Claude's Discretion

Governed by `docs/handoff_to_backend.md: Phase 2` and `REQUIREMENTS.md: NLP-01..08`:
- Lazy process-wide singletons in `nlp/registry.py`; models load once at FastAPI `lifespan`, never
  inside a batch loop. Warm load is 1.8s — worth enforcing with a test.
- Batched inference (`batch_size=16` from config), `device=-1`, `torch.set_num_threads` from settings.
- `HF_HOME=data/models` so weights stay inside the project (gitignored).
- Controversy via spaCy `PhraseMatcher` over lemmas from `config/controversy_lexicon.yaml` with the
  negation guard; **always persist matched terms** — they are the audit trail.
- Every signal row records `model_version`; re-running at the same version is a no-op.
- Excluded documents are always stored with an `exclusion_reason`, never silently dropped.
- Never-raise contract, structlog JSON with `ticker`/`document_id` context (Phase 1 pattern).

### Deferred Ideas (OUT OF SCOPE)

- **`_rss_pillar` hint** in `raw_json` as a classifier prior — available, deliberately unused.
- **IR subdomain source tiering** (`investor.exxonmobil.com`, `ir.*`) — Phase 3 concern.
- **R-1: `--since` is a silent no-op** for RSS and EDGAR — collector bug, not NLP.
- **XOM query recall** — `_primary_company` picks the longest alias. Collector-side.
- **ClimateBERT greenwashing signals** — post-v1 per D-018.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| NLP-01 | Lazy, process-wide model singletons loaded once at lifespan; no model load inside any loop | Registry pattern + call-counting test (§ "Asserting models load exactly once"). Verified warm-load path with `HF_HOME=data/models`, `HF_HUB_OFFLINE=1` |
| NLP-02 | Cleaning — whitespace/boilerplate normalisation, spaCy sentence split, near-duplicate detection | § "Open Question 1" gives the measure, threshold, clustering algorithm and measured yield. § "Pitfall 1" gives the lowercasing requirement |
| NLP-03 | Entity gate — spaCy `en_core_web_sm` NER, ORG fuzzy-match or alias substring; reject `entity_mismatch` | § "Pitfall 4" — alias table is **empty**; needs seeding + fail-open. § "Open Question 4" — feed provenance missing from `raw_json` |
| NLP-04 | ESG classification, `batch_size=16`, `(category, probability)`; `Non-ESG` or `prob < tau` excluded | Verified label set matches `scoring.yaml` exactly; verified `top_k=None` output shape; D-2-01 needs a `scoring.yaml` schema change |
| NLP-05 | Sentiment `P(pos) - P(neg)`, one feature only | Verified labels `positive/negative/neutral`. § "The Phase 2 / Phase 3 line" fixes what Phase 2 may and may not compute |
| NLP-06 | Controversy 0–3 via `PhraseMatcher` over lemmas + negation guard; matched terms always stored | § "Open Question 3" — working spaCy 3.7.5 code, verified. § "Pitfall 1" (Title Case), § "Pitfall 2" (`dropped` false negation) |
| NLP-07 | One `esg_signals` row per document with all four persisted weights; skip rows at current `model_version` | § "The Phase 2 / Phase 3 line" — Phase 2 writes `scoring/weights.py` and leaves `polarity` NULL. Verified this does not activate the Phase 3 acceptance fixture |
| NLP-08 | Gate failures stored with `included=false` and an `exclusion_reason` | § "Gate precedence" gives the deterministic ordering; `exclusion_reason` CHECK values confirmed from `schema.sql` |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

No `./CLAUDE.md` exists in this repository. The binding project-level directives are instead
carried by `docs/handoff_to_backend.md` §4 ("Traps") and `docs/architecture.md` §5.2, both of which
are treated here with the same authority:

- **No magic numbers in `src/`.** Every threshold, weight and half-life resolves from
  `config/*.yaml` (Phase 0 success criterion 4 greps for violations). `tau_news`, `tau_filing_section`,
  the near-duplicate threshold and the sentence cap must all be config keys.
- **Never treat FinBERT sentiment as ESG polarity** (`research_notes.md` §3.2).
- **Never load a transformer inside a loop** — stated as a 100x error.
- **Never gate sufficiency on `w`** — `w_ev` only.
- **`bart-large-mnli` stays off the hot path.**
- **`PRAGMA foreign_keys` is per-connection.**
- **Never-raise contract** — mirror `collectors/base.py`.

## Assessment of Existing `nlp/` Code

Asked plainly: **is any of it salvageable? No. Replace both files.**

`src/esg_lens/nlp/registry.py` (43 lines) — `from src.esg_lens.config import settings` is the only
import of that shape in the repository and does not resolve under `PYTHONPATH=src`, so the module
cannot be imported by the test suite at all. It uses `logging`, not `structlog`. `batch_size=16` is
hard-coded (violates the no-magic-numbers rule). It never sets `HF_HOME`. It has no
`truncation`/`max_length`, which is a guaranteed `RuntimeError` on filing sections (verified
below). Its `get_pipeline(task, model_name)` signature is generic where the phase needs exactly two
named, typed accessors that a test can monkeypatch.

`src/esg_lens/nlp/clean.py` (48 lines) — constructs `TextCleaner()` at **import time** and calls
`spacy.load` in `__init__`, which is the NLP-01 failure mode literally written into module scope.
`split_into_sentences` silently degrades to `text.split(". ")` when the model is missing, which
would make a green test suite meaningless. `> 20` character sentence filter is an unexplained magic
number. `normalize` does not lowercase, does not strip the Google News `" - Publisher"` suffix, and
does nothing about near-duplicates. There is no near-duplicate function at all.

`classify.py`, `controversy.py`, `entity.py`, `pipeline.py`, `sentiment.py` are **0 bytes**.

No test imports `esg_lens.nlp` (verified: `grep -r "esg_lens.nlp" tests/` returns nothing; the
`tests/unit` and `tests/integration` listings contain no NLP file). Coverage is zero. Delete and
rewrite; the only thing worth carrying forward is the *idea* of a registry object.

## Standard Stack

Nothing new is needed. Every dependency this phase requires is already pinned in `pyproject.toml`
and already installed in `.venv`. **Do not add a package.**

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `transformers` | 4.44.0 | `pipeline("text-classification")` for both FinBERT heads | Already pinned; `architecture.md` §6 chose it over ONNX explicitly [VERIFIED: `pyproject.toml`, `.venv` import] |
| `torch` | 2.4.0 | CPU inference backend, `set_num_threads` | Pinned CPU-only [VERIFIED: `.venv` import] |
| `spacy` | 3.7.5 | NER, sentence split, `PhraseMatcher` | Pinned [VERIFIED: `spacy.__version__` printed in session] |
| `en_core_web_sm` | 3.7.1 | Pipeline `['tok2vec','tagger','parser','attribute_ruler','lemmatizer','ner']` | [VERIFIED: `nlp.meta["version"]` printed in session] |
| `pyyaml` | 6.0.1 | Lexicon + config loading | Already used by `config.py` |
| `difflib` / `re` (stdlib) | 3.12 | Near-duplicate similarity | Zero new dependency; deterministic |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `structlog` | 24.4.0 | JSON logs with `ticker`/`document_id` bound | Every stage, per the Phase 1 pattern |
| `pytest` | 8.3.0 | Test runner, `monkeypatch` for stubs | All Phase 2 tests |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| stdlib token-set Jaccard | `rapidfuzz` | 2-5x faster and has `token_set_ratio` built in, but **not installed** (verified `MISSING` in `.venv`) and adds a dependency for a 101x101 comparison that runs in milliseconds. Reject. |
| stdlib Jaccard | `datasketch` MinHash/LSH | The right answer at 10^5 documents. At 10^2 per ticker it is pure overhead and a new dependency. **Not installed.** Reject for v1; revisit if the corpus grows past ~5k docs/ticker. |
| Lexical near-dup | Sentence embeddings | Would actually catch the Tata-style rewrite — and is explicitly forbidden by D-2-02 ("no model inference"). Record as a documented limitation instead. |
| `transformers` pipeline | ONNX Runtime | 2-4x faster on CPU; `architecture.md` §6 marks it post-v1. Performance is not a risk here (4.4x headroom). Reject. |

**Installation:** none required. Confirm with:
```bash
PYTHONPATH=src .venv/bin/python -c "import torch,transformers,spacy;print(torch.__version__,transformers.__version__,spacy.__version__)"
# expect: 2.4.0 4.44.0 3.7.5
.venv/bin/python -m spacy validate   # en_core_web_sm 3.7.1 must be compatible
```

## Package Legitimacy Audit

**Not applicable — this phase installs no external packages.** Every library it uses is already
present in `pyproject.toml` and was installed during Phase 0. `slopcheck` was therefore not run.

If the planner nevertheless proposes adding `rapidfuzz` or `datasketch` (both recommended against
above), that addition must be gated behind a `checkpoint:human-verify` task and the legitimacy gate
run at that point.

## Verified API Reference

Every claim in this section was produced by executing code in `/Users/paddykadamuthuri/projects/esg-lens/.venv`
on 2026-09-14 against the models already cached in `data/models/hub`. Commands are reproducible.

### FinBERT-ESG label set — matches `scoring.yaml` exactly

```python
from transformers import AutoConfig
sorted(AutoConfig.from_pretrained("yiyanghkust/finbert-esg-9-categories").id2label.values())
```
```
['Business Ethics & Values', 'Climate Change', 'Community Relations',
 'Corporate Governance', 'Human Capital', 'Natural Capital', 'Non-ESG',
 'Pollution & Waste', 'Product Liability']
```
[VERIFIED: executed in session] These are byte-identical to the eight keys of
`scoring.yaml: category_weights` plus `Non-ESG`. **No label mapping table is needed** — the model's
`label` string is a direct dict key into `category_weights` and `category_to_pillar`. A plan task
should assert this equality so a model swap fails loudly rather than silently producing `KeyError`
or, worse, a default weight.

### Sentiment label set

```python
AutoConfig.from_pretrained("ProsusAI/finbert").id2label
# {0: 'positive', 1: 'negative', 2: 'neutral'}
```
[VERIFIED: executed in session] Lowercase, and the `sentiment_label` CHECK in `esg_signals` is
`('positive','negative','neutral')` — they match, no case conversion needed.

### Pipeline output shape

```python
p = pipeline("text-classification",
             model="yiyanghkust/finbert-esg-9-categories",
             device=-1, top_k=None, batch_size=16,
             truncation=True, max_length=512)
p(["Microsoft carbon emissions rose 25% in 2025 amid AI data center boom"])
```
Returns `list[list[dict]]` — one inner list per input, **sorted descending by score**, each element
`{"label": str, "score": float}`. For the headline above: `{'label': 'Climate Change', 'score': 0.9953}`.
[VERIFIED: executed in session, transformers 4.44.0]

Consequences for the implementation:
- `out[i][0]` is the argmax. No manual sorting needed, but a plan that relies on ordering should
  say so rather than assume it.
- `top_k=None` is the 4.44 spelling. `return_all_scores=True` is deprecated. Use `top_k=None`.
- The stub in tests must return this exact nested shape (see § "Stubbing HF pipelines").

### `truncation=True` alone is a runtime error

```python
p = pipeline("text-classification", model="yiyanghkust/finbert-esg-9-categories",
             device=-1, top_k=None, truncation=True)   # NOTE: no max_length
p(["The company is committed to sustainability. " * 200])
```
```
Asking to truncate to max_length but no maximum length is provided and the model has no
predefined maximum length. Default to no truncation.
RuntimeError: The size of tensor a (1402) must match the size of tensor b (512) at
non-singleton dimension 1
```
[VERIFIED: executed in session] Cause: `p.tokenizer.model_max_length` is
`1000000000000000019884624838656` (the "unset" sentinel) for **both** models. **`max_length=512`
must be passed explicitly.** This is exactly the class of failure that sank Phase 1 — a warning
that reads like advice, followed by a hard crash only on long input, which headline-only fixtures
would never reach. A filing-section test with a >512-token input is mandatory.

### spaCy `PhraseMatcher` over lemmas

```python
nlp = spacy.load("en_core_web_sm")                       # full pipeline — lemmatizer required
m = PhraseMatcher(nlp.vocab, attr="LEMMA")
m.add("SEV2", list(nlp.pipe(["data breach", "lawsuit"])))  # patterns via nlp.pipe, NOT nlp.make_doc
```
[VERIFIED: executed in session] Two things confirmed by running it:
- Patterns **must** be built with `nlp.pipe(...)` (or `nlp(...)`), not `nlp.make_doc(...)`. The
  tokenizer-only path leaves `Token.lemma_` empty and every match silently fails. The usual spaCy
  performance advice ("use `nlp.make_doc` for PhraseMatcher patterns") is correct for `attr="LOWER"`
  and **wrong** for `attr="LEMMA"`.
- Inflection works and is symmetric because both sides go through the same lemmatizer:
  `"data breaches"` lemmatizes to `datum breach` and the pattern `"data breach"` lemmatizes to
  `datum breach` — so they match. Likewise `"fined"` -> `fine` matches the `fine` trigger,
  `"investigations"` -> `investigation`, `"alleges"` -> `allege` matches the `alleged` trigger.

## Open Question 1 — Near-duplicate similarity measure and threshold

### The honest finding first

I built a labelled pair set from the live corpus (`data/esg_lens.db`, 101 news rows) plus the Tata
pair given in the brief, and scored it with four deterministic measures: token-set Jaccard (`J`),
`difflib` ratio over sorted token sets (`TS`), IDF-weighted containment (`IDF`) and character
trigram Dice (`G3`). All titles normalised (lowercase, strip `(NASDAQ: XXX)`, strip tickers, strip
punctuation, drop a small stopword list).

| Pair | Truth | J | TS | IDF | G3 |
|---|---|---|---|---|---|
| Tata breach (the brief's example) | DUPLICATE | 0.40 | 0.74 | 0.66 | 0.59 |
| MSFT carbon emissions 25% (qz vs gurufocus) | DUPLICATE | 0.40 | 0.63 | 0.49 | 0.51 |
| MSFT carbon emissions 25% (mlq vs parameter) | DUPLICATE | 0.32 | 0.58 | 0.56 | 0.51 |
| MSFT carbon removal 80% (biggo vs carboncredits) | DUPLICATE | 0.29 | 0.52 | 0.46 | 0.46 |
| MSFT carbon removal 80% (biggo vs stocktwits) | DUPLICATE | 0.39 | 0.58 | 0.72 | 0.58 |
| Chevron data-center deal (cnbc vs seekingalpha) | DUPLICATE | 0.29 | 0.60 | 0.40 | 0.49 |
| 400 newspapers copyright suit (shacknews vs benzinga) | DUPLICATE | 0.33 | 0.72 | 0.50 | 0.51 |
| Michael Burry bet (247wallst vs yahoo) | DUPLICATE | 0.46 | 0.71 | 0.70 | 0.56 |
| **Wisconsin data-center suit vs Copilot/noise suits** | **DISTINCT** | **0.46** | **0.76** | **0.64** | **0.61** |
| Azure lawsuit (tradingview vs cryptonomist) | DISTINCT | 0.30 | 0.60 | 0.69 | 0.47 |
| IXUS/NZAC vs VEA/NZAC ETF comparisons | DISTINCT | 0.37 | 0.62 | 0.52 | 0.57 |
| "Stock flat in 2026" vs "Buy or hold?" | DISTINCT | 0.11 | 0.48 | 0.11 | 0.34 |
| Italy antitrust vs UK watchdog | DISTINCT | 0.23 | 0.50 | 0.32 | 0.42 |

**On every measure the rewritten-duplicate band and the distinct-story band overlap.** The worst
case is stark: the Tata pair the brief asks us to catch scores *below* the Wisconsin/Copilot pair
we must not collapse, on J, TS and G3 alike. There is no threshold that catches the former without
destroying the latter. [VERIFIED: computed in session over the live DB + the brief's titles]

This is a property of the problem, not of the measure. "India's Tata Electronics hit by cyber
breach claiming to expose Apple, Tesla trade secrets" and "Tata Electronics Cyber Breach Exposes
Apple and Tesla's Sensitive Files" share seven content words out of seventeen distinct; a human
knows they are the same event from world knowledge, not from lexical overlap. Recovering that
needs embeddings, which D-2-02 forbids.

### The recommendation

**Scope the feature to what it can do reliably: collapse verbatim and near-verbatim syndication.**

- **Measure:** token-set Jaccard over normalised content words. Cheapest of the four, easiest to
  explain in an audit trail, and no worse than the others on the labelled set.
- **Threshold:** `near_duplicate_jaccard: 0.70` in `config/scoring.yaml`.
- **Blocking:** compare only within the same `ticker` (D-2-02 already says per-ticker). **Do not add
  a publication-date window** — see the measured evidence below.
- **Clustering:** union-find over all pairs above threshold, so a syndicated triple collapses to one
  cluster rather than three overlapping pairs.
- **Retention:** within a cluster keep the document with the lowest source tier number (tier 1 beats
  tier 4) resolved from `config/sources.yaml`; break ties by earliest `published_at`, then lowest
  `raw_documents.id`, so the choice is **deterministic and reproducible**. `filing` never enters a
  cluster with news because `doc_type` differs — compare within `doc_type` too.
- **Audit:** write the retained document's id onto the excluded rows. `esg_signals` has no column
  for it; put it in the `entities_json` blob under a `"duplicate_of"` key, or add a nullable
  `duplicate_of INTEGER` column — the planner should pick one and state it. **Recommend the column**;
  overloading `entities_json` is the kind of shortcut this project's audit promise cannot afford.

### Measured yield on the live corpus

Union-find clustering over all 5,050 pairs of the 101 news rows:

| Threshold | Clusters | Docs excluded | % of corpus | Any false merge? |
|---|---|---|---|---|
| 0.85 | 6 | 8 | 7.9% | no |
| **0.75** | **7** | **11** | **10.9%** | **no** |
| **0.70** | **7** | **12** | **11.9%** | **no** |
| 0.65 | 7 | 13 | 12.9% | no |
| 0.60 | 6 | 14 | 13.9% | no (largest cluster reaches 8 docs via chaining) |
| 0.50 | 6 | 16 | 15.8% | risk — approaches the 0.46 distinct-pair band |

At 0.70 the seven clusters are: three separate Levi & Korsinsky / SueWallSt "deadline alert"
groups, two verbatim prnewswire re-posts, one globenewswire re-post, and the Kahn Swick & Foti
pair. Every one is the same MSFT securities class action re-issued under a different law firm's
byline. **0.70 is the recommendation** — it is 0.24 above the highest observed distinct-story pair
(0.46), which is a real margin, and it avoids the transitive chaining that appears at 0.60.

Note the corpus rate here (11.9%) is nearly four times the 3.1% quoted in `02-CONTEXT.md`. That
figure came from the earlier AAPL/XOM corpus; this MSFT corpus is saturated with litigation PR.
Both are real. The planner should not treat 3.1% as a target.

### Why not a date window

Intuition says syndicated copies appear within a day of each other. On this corpus that is false:
of the pairs above the 0.75 threshold, **7 are more than three days apart** (law firms re-issue the
same deadline alert weekly for a month). A same-day constraint would break the feature. Measured,
not assumed. [VERIFIED: computed in session]

### Documented limitation for the README

"Near-duplicate collapse uses lexical title similarity and therefore catches syndicated re-posts
but not independently rewritten coverage of the same event. Repeated reporting of one incident by
different outlets still contributes multiple documents to `Σ w_ev`."

## Open Question 2 — Cross-sentence aggregation for filing sections

### Controversy: MAX, confirmed

`scoring_methodology.md` §6.1 is explicit about *why* the controversy penalty is a separate term:
"a weighted mean lets a flood of mild-positive PR bury one severe incident. Apply an explicit,
separately-reported penalty so severe events cannot be averaged away." `handoff_to_backend.md` §4
lists "Averaging away controversies" as a named trap. Averaging severity across the sentences of a
10-K Item 1A — which is *by construction* mostly boilerplate risk language with a handful of
material disclosures — would reproduce exactly the failure the separate penalty term exists to
prevent, one layer lower down. [CITED: `docs/scoring_methodology.md` §6.1; `docs/handoff_to_backend.md` §4]

**Rule:** `controversy_severity = max(severity over all sentences in the section)`, computed on
**post-negation-guard effective severity** (demote first, then take the max — otherwise a negated
severity-3 outranks a genuine severity-2).

**Matched terms:** the **union** of matched terms across all sentences, deduplicated,
order-stable (first-appearance order), serialised as a JSON array. Not just the terms from the
max-severity sentence — NLP-06 says the terms are the audit trail, and an auditor reading
"severity 3, terms: ['oil spill']" for a section that also said "fine" and "investigation" is being
shown less than the system knew. Cap the array (suggest 20 terms) so a pathological section cannot
write a megabyte into a TEXT column.

### Sentiment: weighted mean over ESG-relevant sentences only

D-2-04 already locks relevance to "mean confidence over ESG-relevant sentences only, not over all
sentences, which would let boilerplate drag a genuinely relevant section below `tau`". The identical
argument applies to sentiment, and the symmetry is worth preserving — a reader should not have to
remember that two fields on the same row were computed over different sentence sets.

**Rule:** `sentiment_score = Σ(rel_i * sent_i) / Σ(rel_i)` over sentences where
`label_i != "Non-ESG"` and `rel_i >= tau_filing_section`; the same sentence set that produces
`relevance` and `category`. Weighting by relevance (rather than a flat mean) means a sentence the
classifier was barely sure about does not move the section's sentiment as much as one it was
certain about — consistent with the `w_conf` philosophy in §5. If the set is empty the section is
excluded as `not_esg_relevant` and `sentiment_score` is NULL.

**`sentiment_label`:** derive from the aggregate score, not from a vote:
`positive` if `> 0.15`, `negative` if `< -0.15`, else `neutral`. Put the 0.15 band in
`scoring.yaml` (`sentiment_label_band`) — it is a threshold and the no-magic-numbers rule applies.

### Category: dominant label, weight-by-confidence

D-2-04 says "dominant ESG label across relevant sentences". Make it precise: **argmax over
`Σ rel_i` grouped by label**, not a raw sentence count. Two confidently-Climate-Change sentences
should beat three marginally-Human-Capital ones. Tie-break deterministically by the label with the
single highest individual `rel_i`, then alphabetically.

### Sentence cap

`en_core_web_sm` parses roughly 3ms per short document; a 10-K Item 1A can exceed 2,000 sentences,
which at 30ms/sentence of FinBERT-ESG is ~60s for one section against a <10s phase criterion.

**Rule:** `max_sentences_per_section: 400` in `scoring.yaml`. 400 sentences is ~12s of FinBERT-ESG
per section in the worst case, and Item 1A sections in this corpus are far below it. Take the
**first 400** after dropping sentences shorter than a minimum token count. Record truncation:
log `filing_section_truncated` with the original sentence count, and the plan should include a
test that a 500-sentence input processes exactly 400. Do **not** sample randomly — reproducibility
at the same `model_version` is NLP-07's contract.

Also drop degenerate sentences before counting: `len(sent.text.strip()) < 25 chars` or
`< 4 alphabetic tokens`. EDGAR section text is full of table fragments and page furniture that are
neither ESG-relevant nor harmless (they get classified, they carry a softmax score, and they dilute
nothing only because the relevant-only mean excludes them — but they cost 30ms each).

## Open Question 3 — The negation guard, working spaCy 3.7.5 syntax

Verified end-to-end in this session against `spacy 3.7.5` / `en_core_web_sm 3.7.1`.

```python
# Source: executed against .venv in this session, 2026-09-14
import spacy, yaml
from spacy.matcher import PhraseMatcher

nlp = spacy.load("en_core_web_sm")
lex = yaml.safe_load(open("config/controversy_lexicon.yaml"))

# Trigger matcher: one PhraseMatcher key per severity tier, so the key IS the severity.
trig = PhraseMatcher(nlp.vocab, attr="LEMMA")
for sev, blk in lex["tiers"].items():                 # keys are ints 3, 2, 1
    trig.add(str(sev), list(nlp.pipe(blk["triggers"])))   # nlp.pipe, NOT nlp.make_doc

# Negation matcher: separate matcher, same vocab, same attr.
neg = PhraseMatcher(nlp.vocab, attr="LEMMA")
neg.add("NEG", list(nlp.pipe(lex["negation_guard"]["phrases"])))
W = lex["negation_guard"]["window"]                   # 4

def severity(text: str) -> tuple[int, list[str]]:
    doc = nlp(text.lower())                           # LOWERCASE — see Pitfall 1
    neg_spans = [(s, e) for _, s, e in neg(doc)]
    best, terms = 0, []
    for match_id, s, e in trig(doc):
        sev = int(nlp.vocab.strings[match_id])
        # symmetric +/- W token window: overlap test between [s-W, e+W) and [ns, ne)
        negated = any(not (ns >= e + W or ne <= s - W) for ns, ne in neg_spans)
        eff = max(0, sev - 1) if negated else sev
        terms.append(doc[s:e].text)
        best = max(best, eff)
    return best, terms
```

Measured behaviour [VERIFIED: executed in session]:

| Input | Result |
|---|---|
| `"Apple was cleared of the fraud allegations"` | `fraud` 3 -> **2** (negated), `allegations` 1 -> 0 |
| `"The securities fraud lawsuit was dismissed"` | `fraud` 3 -> **2**, `lawsuit` 2 -> 1 |
| `"Microsoft settled without admitting the bribery charges"` | `bribery` 3 -> **2** |
| `"Charges of corruption dropped against the CEO"` | `corruption` 3 -> **2** |
| `"Microsoft faces securities fraud class action lawsuit"` | **3** (no negation) |
| `"Lawsuit Against Apple To Remain In State Court"` | **2** (no negation) |

The negation phrases lemmatize to `['clear of', 'dismiss', 'settle without', 'drop', 'acquit of',
'exonerate']` — note that `"cleared of"` becomes the two-token lemma sequence `clear of`, which is
why patterns must go through the full pipeline.

**The window must be symmetric, not "4 tokens before".** `config/controversy_lexicon.yaml` says
"if any appears within 4 tokens **before** a match". That is wrong for the commonest English
construction: in `"The lawsuit was dismissed"` the negator follows the trigger. A before-only window
would miss it. The code above uses a `+/- W` token overlap test and catches both orders. **The
planner should also fix the comment in the YAML file** so config and code agree — a stale comment
about a security-relevant rule is a future bug.

## Pitfalls

### Pitfall 1 (SEVERE): `attr="LEMMA"` silently fails on Title Case headlines

**What goes wrong:** controversy detection fires on ~6% of news instead of ~39%, and no test
notices.

**Why it happens:** spaCy's tagger labels capitalised nouns in headline-register text as `PROPN`,
and the lemmatizer returns `PROPN` surface forms unchanged, preserving capitalisation. `LEMMA`
matching is case-sensitive. Verified token dumps:

```
"Microsoft Faces Antitrust Investigation in Italy"
  -> Investigation | lemma='Investigation' | POS=PROPN     # does NOT match trigger 'investigation'
"Layoffs announced amid investigations"
  -> Layoffs       | lemma='Layoffs'       | POS=PROPN     # does NOT match 'layoffs'
  -> investigations| lemma='investigation' | POS=NOUN      # DOES match
"Microsoft announced layoffs"
  -> layoffs       | lemma='layoff'        | POS=NOUN      # DOES match
```
[VERIFIED: executed in session]

**Measured impact on the live corpus** (101 news rows, full `controversy_lexicon.yaml`):

| Input handling | Headlines with >= 1 controversy hit |
|---|---|
| As stored (Title Case) | **6 / 101** |
| Lowercased first | **39 / 101** |

[VERIFIED: computed in session] Examples recovered by lowercasing include
`"Microsoft (MSFT) Faces Shareholder Lawsuit; TD Cowen Maintains Buy Rating"`,
`"MSFT Court News: Microsoft Class Action Lawsuit Alleges Securities Fraud..."` and
`"Microsoft (MSFT) Faces Two New Class Actions Over Copilot And Data Center Noise"` — i.e. the
entire litigation cluster, which is the dominant ESG-G signal in this corpus.

**How to avoid:** `nlp/clean.py` emits a `text_for_matching` field alongside the display text, and
`nlp/controversy.py` operates exclusively on the lowercased form. Filing-section body text is
sentence-case and largely unaffected, but lowercasing it too costs nothing and keeps one code path.

**Do not lowercase the text the entity gate sees.** `en_core_web_sm`'s NER depends heavily on
capitalisation; lowercasing destroys `ORG` recall. The gate reads the original cased title. Two
spaCy passes per news document, ~6ms total against a 4.4x headroom — affordable, and the planner
should say so explicitly so a later "optimisation" does not re-merge them.

**Warning signs:** a corpus-wide controversy rate under ~15% on news; `controversy_terms` almost
always NULL; the G pillar showing `insufficient_data` while the headlines are visibly full of
class-action notices.

### Pitfall 2: bare `dropped` in the negation guard cancels real controversies

`"dropped"` lemmatizes to `drop`, which also matches stock-price language. On the live corpus this
fires exactly once, and destructively:

```
"MSFT Stock Drop: Microsoft Copilot Functionality Issues Lead to 10% Stock Drop
 and Securities Fraud Class Action for Investors"
  -> fraud 3 -> 2 (NEGATED), class action 3 -> 2 (NEGATED)
```
[VERIFIED: computed in session — 1 negation firing in 101 headlines, and it is this false one]

**How to avoid:** replace the bare `dropped` trigger with multi-token forms so the object is
constrained. Because matching is over lemmas, write them in base form and let the lemmatizer
normalise the corpus side:
```yaml
negation_guard:
  phrases:
    - "cleared of"
    - "dismissed"
    - "settled without"
    - "acquitted of"
    - "exonerated"
    - "charges dropped"      # lemma: charge drop
    - "drop charges"         # lemma: drop charge
    - "case dropped"
    - "dropped the lawsuit"
    - "dropped its lawsuit"
    - "lawsuit dropped"
  window: 4
```
The planner should include a regression test pinning that headline at severity 3.

### Pitfall 3: `fine` is a severity-2 trigger and an English adjective

`config/controversy_lexicon.yaml` tier 2 contains `fine`. `config/rss_feeds.yaml` already
documents `"fine"` as "catastrophically ambiguous" and excluded it from query terms for exactly
this reason — but it survived in the lexicon. Verified: `"The weather was fine"` tags `fine` as
`ADJ` with lemma `fine`; `"Everything is doing fine"` tags it `NOUN`. Either would match.

It happened to produce **zero** false positives on this 101-headline corpus, so it is a latent
rather than an active bug. Two options, planner's choice, but the decision should be recorded:
(a) drop `fine` and keep `fined`, `penalty`, `penalties` (mirrors the `rss_feeds.yaml` precedent
and is the consistent choice); or (b) keep it and add a POS filter rejecting `ADJ` matches, which
requires the matcher loop to inspect `doc[s:e][0].pos_`. **Recommend (a)** — `fined` lemmatizes to
`fine` anyway, so "Company fined $5M" still matches via the `fined` trigger... which means dropping
`fine` alone does not actually remove the ambiguity. So: **(b), with a POS filter that skips a
single-token match whose POS is `ADJ`.** Verified that `"Company fined for pollution violations"`
tags `fined` as `VERB` (lemma `fine`) and still matches under that filter.

### Pitfall 4 (BLOCKING): `company_aliases` is empty and `companies.name` is the ticker

```sql
SELECT COUNT(*) FROM company_aliases;   -- 0
SELECT ticker, name, sector FROM companies;   -- MSFT | MSFT | (null)
```
[VERIFIED: queried `data/esg_lens.db` in session]

yfinance returns 404 for this ticker (Phase 1 verification already recorded the sustainability
endpoint as dead; metadata is evidently in the same state), so `companies.name` fell back to the
ticker and no aliases were seeded. NLP-03 as written — "accept when an `ORG` entity fuzzy-matches
an alias or an alias appears as an exact substring of the title" — would then compare every
`google_news` headline against the single alias-ish string `"MSFT"`, and reject nearly all of them
as `entity_mismatch`. Applied to a corpus of headlines that say "Microsoft" 60 times, that is a
total loss of the primary news source.

**The gate must fail open, loudly.** If a ticker has fewer than N usable aliases (suggest N=1
excluding the bare ticker), skip the gate for that ticker, write `included=true`, and emit a
`entity_gate_skipped_no_aliases` warning log with the ticker bound. A gate that cannot function
must not silently reject — that violates the project's "honest refusal over fabricated confidence"
principle just as much as emitting 50 for no data does.

**Additionally, seed aliases deterministically** so the gate has something to work with without
depending on yfinance: `companies.name`, the ticker itself, and — for EDGAR-covered tickers — the
`company_tickers.json` title the EDGAR collector already fetches. This is arguably collector work,
but the entity gate is the only consumer, so Phase 2 should own at least the fallback. The planner
should decide and record which.

### Pitfall 5: `sentiment_score` is financial, not ESG — verified with a live counter-example

`research_notes.md` §3.2 warns about this abstractly. Here it is concretely, from this corpus:

```
"Microsoft carbon emissions rose 25% in 2025 amid AI data center boom"
  FinBERT-ESG : Climate Change (0.995)
  ProsusAI    : positive 0.931 | neutral 0.058 | negative 0.012
  -> sentiment_score = P(pos) - P(neg) = +0.919
```
[VERIFIED: executed in session]

A 25% emissions increase scores **+0.92 positive** because it is financially bullish (AI demand).
Persisting that number is correct — NLP-05 says so. Interpreting it as ESG polarity would put a
worsening-emissions story on the positive side of the E pillar. This is the single worked
counter-example the README limitations section should use; it is better than the hypothetical one
in the methodology doc because it is real data from the system's own corpus.

### Pitfall 6: `max_length` on the sentiment pipeline too

`ProsusAI/finbert` has the same `model_max_length` sentinel. Filing-section *sentences* will
usually be short, but an EDGAR table row that spaCy fails to split can exceed 512 tokens. Pass
`truncation=True, max_length=512` to both pipelines, not just the classifier.

### Pitfall 7: `HF_HOME` must be set before `transformers` is imported

`HF_HOME=data/models` is a discretion item in CONTEXT.md, but `transformers` reads it at import
time to resolve its cache constants. Setting `os.environ["HF_HOME"]` inside a function that runs
after `import transformers` is a no-op. Set it in `esg_lens/config.py` (which every module imports
first) or in a `esg_lens/nlp/__init__.py` above the transformers import, and add it to `Settings`
as `HF_HOME: str = Field(default=str(DATA_DIR / "models"))` so it is configurable. Verified working
via `HF_HOME=data/models HF_HUB_OFFLINE=1` in this session — the cached models resolved offline.

### Anti-patterns to avoid

- **Merging the entity-gate spaCy pass with the controversy pass to "save time".** They need
  different casing (Pitfall 1). There is no time to save.
- **`nlp.make_doc` for PhraseMatcher patterns.** Standard spaCy performance advice; silently breaks
  `attr="LEMMA"`.
- **Instantiating anything at module import time** (the existing `clean.py` and `registry.py` both
  do this). It makes `import esg_lens.nlp.clean` load spaCy, which makes every unrelated test slow
  and makes NLP-01 untestable.
- **`return_all_scores=True`.** Deprecated in favour of `top_k=None` in transformers 4.44.
- **Computing `pol(d)` in Phase 2.** See below.

## The Phase 2 / Phase 3 line

`esg_signals` has columns that belong to both phases. Getting the line wrong either blocks NLP-07
or pulls Phase 3 forward. The verified resolution:

| Column | Phase 2 writes it? | Why |
|---|---|---|
| `pillar` | **yes** | `scoring.yaml: category_to_pillar[category]` — a dict lookup, not scoring logic. The `idx_signals_ticker_pillar` index exists so Phase 3 can query by pillar without a join |
| `category`, `relevance` | yes | Direct model output |
| `sentiment_score`, `sentiment_label` | yes | Direct model output. NLP-05 |
| `controversy_severity`, `controversy_terms` | yes | Lexicon output. NLP-06 |
| `weight_source` | yes | `sources.yaml` tier lookup from `domain` / `source='edgar'` |
| `weight_recency` | yes | `0.5 ** (age_days / half_life)` — `weights.recency_weight` |
| `weight_category` | yes | `scoring.yaml: category_weights[category]` |
| `weight_evidence` (`w_ev`) | yes | `weights.evidence_weight(w_src, w_rec, relevance)` |
| `weight_total` (`w`) | yes | `weights.scoring_weight(w_ev, w_cat)` |
| **`polarity` (`pol(d)`)** | **NO — leave NULL** | `scoring/weights.polarity(sentiment, controversy, cfg)` is named in the Phase 3 acceptance contract. The column is nullable. Phase 3 computes it from `sentiment_score` + `controversy_severity`, both of which Phase 2 persists |
| `entities_json`, `model_version`, `processed_at`, `included`, `exclusion_reason` | yes | Phase 2 |

NLP-07 requires persisting `w_ev` and `w`, which means Phase 2 needs
`recency_weight`, `evidence_weight` and `scoring_weight`. **Write them in `scoring/weights.py`, not
in `nlp/`** — that is where Phase 3's acceptance fixture expects them, and duplicating the formulas
in two places is how `w`/`w_ev` confusion comes back.

**Verified safe:** `tests/unit/test_scoring_fixture.py` gates its skip on *all nine* callables
across `weights`, `aggregate` and `composite` being present. Adding three functions to
`weights.py` leaves `weights.polarity`, `aggregate.*` and `composite.*` missing, so the fixture
stays skipped:

```
$ PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_scoring_fixture.py -q
17 skipped in 0.02s
```
[VERIFIED: executed in session; skip predicate read from the file at lines 63-80]

`scoring/` must remain pure — `nlp/pipeline.py` imports `scoring.weights`, never the reverse.
SCOR-01's "no I/O in `scoring/`" is unaffected.

## Gate precedence

NLP-08 requires every rejected document to carry exactly one `exclusion_reason`. The CHECK
constraint permits `'entity_mismatch' | 'not_esg_relevant' | 'too_old' | 'low_confidence' |
'duplicate' | 'unsupported_language'` [VERIFIED: `src/esg_lens/db/schema.sql` via `docs/data_model.md`].
Order matters — it determines which reason an auditor sees. Recommended, cheapest-and-most-certain
first:

1. **`too_old`** — `age_days > max_age_days` (730). Pure arithmetic on `published_at`; no reason to
   spend model time on a document that cannot contribute. NULL `published_at` should **not** be
   treated as too old; treat it as age 0 and log it.
2. **`duplicate`** — D-2-02 clustering in `clean.py`. Before the gates, so a duplicate never costs
   inference and the retained copy alone is classified.
3. **`entity_mismatch`** — only for feeds with `entity_gate: apply` (D-2-03), and only when the
   ticker has usable aliases (Pitfall 4).
4. **`not_esg_relevant`** — FinBERT-ESG top label is `Non-ESG`.
5. **`low_confidence`** — top label is an ESG category but `score < tau` for the doc type.

4 and 5 are distinct reasons for distinct conditions, and ROADMAP success criterion 3 names both
separately ("`Non-ESG`, and `prob < 0.60` each produce the correct `exclusion_reason`"). Do not
collapse them.

`'unsupported_language'` has no producer in v1 (assumption 3: English-only). Leave it unused
rather than inventing a detector; note it in the plan so its absence is deliberate.

## Config changes this phase requires

`config/scoring.yaml` currently has a single `tau: 0.60`. D-2-01 needs per-doc-type thresholds, and
several new keys are implied above. Proposed diff — **bump `version`**, which changes `config_hash`
and is stamped on every score row:

```yaml
version: "0.2.0"          # was 0.1.0

# tau: 0.60               # REMOVED — replaced by per-doc-type thresholds (D-2-01)
tau:
  news: 0.45              # headlines are out-of-domain for FinBERT-ESG; softmax runs low
  filing_section: 0.60    # long-form corporate-report register — the value §5 was written for
  press_release: 0.45     # same register as news

nlp:
  batch_size: 16
  near_duplicate_jaccard: 0.70       # D-2-02; see 02-RESEARCH.md Open Question 1
  max_sentences_per_section: 400     # D-2-04 sentence cap
  min_sentence_chars: 25
  sentiment_label_band: 0.15         # |score| below this -> 'neutral'
  min_aliases_for_entity_gate: 1     # below this, gate fails open (Pitfall 4)
```

**Backwards-compatibility trap:** `tau` changes from scalar to mapping. Grep for existing readers
before changing it (`tests/unit/test_config.py` may assert the scalar). A plan task should update
`ScoringConfig` validation and any test that pins `tau == 0.60`.

`config/rss_feeds.yaml` needs D-2-03's trust declaration — it is **not there today**:

```yaml
feeds:
  google_news:
    entity_gate: apply     # query-matched only; "Apple the fruit" is possible
  yahoo_ticker:
    entity_gate: skip      # Yahoo tags the feed to the ticker itself
```

`config/controversy_lexicon.yaml` needs the `dropped` fix (Pitfall 2) and the corrected window
comment (Open Question 3). **Bump its `version` from `"2026-09"`** — `model_version` embeds
`lex@2026-09`, so a lexicon change that does not bump the version makes NLP-07's "re-running at the
same `model_version` is a no-op" produce stale rows. Suggest `lex@2026-09b`.

## Open Question 4 — Feed provenance is not recorded

D-2-03 keys entity-gate trust on which feed a document came from. **`raw_documents` does not record
that.** Verified:

```sql
SELECT DISTINCT raw_json FROM raw_documents WHERE doc_type='news';
-- {"_rss_pillar": "E"} | {"_rss_pillar": "S"} | {"_rss_pillar": "G"} | (empty/NULL)
```
`src/esg_lens/collectors/rss.py:170` writes `raw_json=None if pillar is None else '{"_rss_pillar": ...}'`
— and the Yahoo ticker feed passes `pillar=None`. So feed identity is currently only recoverable as
"`raw_json IS NULL` implies Yahoo", which is an implicit, undocumented, fragile invariant that any
future collector change breaks silently.

Domain is not a substitute: Google News surfaces `finance.yahoo.com` articles too (row 80 is
`uk.finance.yahoo.com` from a Google query).

**Recommendation — a small collector change inside Phase 2's blast radius:**
1. `rss.py` writes `raw_json = {"_rss_pillar": <pillar or null>, "_rss_feed": "google_news"|"yahoo_ticker"}`.
2. `nlp/entity.py` reads `_rss_feed`, looks up `feeds[<key>].entity_gate` in `rss_feeds.yaml`,
   defaults to `apply` when the key is absent or unrecognised (fail closed on *trust*, fail open on
   *aliases* — the asymmetry is deliberate: an unknown feed should not inherit "skip").
3. `source == 'edgar'` -> skip unconditionally (identity is definitional, D-2-03).
4. **Backfill** existing rows, or accept that pre-existing Yahoo rows are gated. The 101-row corpus
   makes a re-collect cheap; a one-off `UPDATE ... WHERE raw_json IS NULL` is the alternative and
   should be written as a migration, not a console command.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest` 8.3.0 (+ `pytest-asyncio` 0.23.5, `asyncio_mode = "auto"`) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]`, `testpaths = ["tests"]` |
| Quick run command | `PYTHONPATH=src .venv/bin/python -m pytest tests/unit -q` |
| Full suite command | `PYTHONPATH=src .venv/bin/python -m pytest -q` |

Note the suite is run with `PYTHONPATH=src`; the existing `nlp/registry.py` `from src.esg_lens...`
import is incompatible with this and is one more reason to delete it.

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| NLP-01 | Loader invoked once across a 50-doc batch | unit | `pytest tests/unit/test_nlp_registry.py::test_models_load_once -x` | Wave 0 |
| NLP-01 | Registry returns the identical object on repeated calls | unit | `pytest tests/unit/test_nlp_registry.py::test_singleton_identity -x` | Wave 0 |
| NLP-02 | Title normalisation strips `" - Publisher"`, `(NASDAQ: X)`, collapses whitespace | unit | `pytest tests/unit/test_nlp_clean.py::test_normalize -x` | Wave 0 |
| NLP-02 | Jaccard >= 0.70 clusters the 7 known corpus clusters; the Wisconsin/Copilot pair stays split | unit | `pytest tests/unit/test_nlp_clean.py::test_near_duplicate_corpus_fixture -x` | Wave 0 |
| NLP-02 | Highest source tier retained; excluded rows carry `duplicate_of` | unit | `pytest tests/unit/test_nlp_clean.py::test_duplicate_retention_rule -x` | Wave 0 |
| NLP-03 | `google_news` + no alias match -> `entity_mismatch` | unit | `pytest tests/unit/test_nlp_entity.py::test_entity_mismatch -x` | Wave 0 |
| NLP-03 | `yahoo_ticker` and `edgar` skip the gate | unit | `pytest tests/unit/test_nlp_entity.py::test_trusted_feeds_skip_gate -x` | Wave 0 |
| NLP-03 | Empty `company_aliases` -> gate fails open, warning logged | unit | `pytest tests/unit/test_nlp_entity.py::test_fails_open_without_aliases -x` | Wave 0 |
| NLP-04 | Stub returns `Non-ESG` -> `not_esg_relevant` | unit | `pytest tests/unit/test_nlp_classify.py::test_non_esg_excluded -x` | Wave 0 |
| NLP-04 | Stub returns `Climate Change` @ 0.50 -> included for news (tau 0.45), excluded `low_confidence` for filing (tau 0.60) | unit | `pytest tests/unit/test_nlp_classify.py::test_per_doctype_tau -x` | Wave 0 |
| NLP-04 | Model label set == `scoring.yaml` category keys + `Non-ESG` | integration | `pytest tests/integration/test_nlp_models.py::test_label_contract -x` | Wave 0 |
| NLP-04 | >512-token input does not raise | integration | `pytest tests/integration/test_nlp_models.py::test_long_input_truncates -x` | Wave 0 |
| NLP-05 | `P(pos)-P(neg)` arithmetic + label band from a stub | unit | `pytest tests/unit/test_nlp_sentiment.py -x` | Wave 0 |
| NLP-06 | **Title Case headline from the real corpus yields severity >= 2** (Pitfall 1 regression) | unit | `pytest tests/unit/test_nlp_controversy.py::test_title_case_headline_detected -x` | Wave 0 |
| NLP-06 | Negation demotes exactly one tier; `"cleared of fraud"` -> 2 | unit | `pytest tests/unit/test_nlp_controversy.py::test_negation_demotes_one_tier -x` | Wave 0 |
| NLP-06 | `"MSFT Stock Drop... Securities Fraud Class Action"` stays severity 3 (Pitfall 2 regression) | unit | `pytest tests/unit/test_nlp_controversy.py::test_stock_drop_does_not_negate -x` | Wave 0 |
| NLP-06 | Matched terms persisted as a JSON array, non-empty on every hit | unit | `pytest tests/unit/test_nlp_controversy.py::test_terms_always_stored -x` | Wave 0 |
| NLP-07 | All four weights non-NULL; `polarity` NULL | unit | `pytest tests/unit/test_nlp_pipeline.py::test_weights_persisted -x` | Wave 0 |
| NLP-07 | Second run at same `model_version` inserts zero rows | unit | `pytest tests/unit/test_nlp_pipeline.py::test_rerun_is_noop -x` | Wave 0 |
| NLP-08 | Every excluded doc has a row with `included=0` and a non-NULL reason | unit | `pytest tests/unit/test_nlp_pipeline.py::test_nothing_silently_dropped -x` | Wave 0 |
| ROADMAP-1 | 50 fixture headlines -> signals in <10s on CPU | integration | `pytest tests/integration/test_nlp_perf.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `PYTHONPATH=src .venv/bin/python -m pytest tests/unit -q`
- **Per wave merge:** `PYTHONPATH=src .venv/bin/python -m pytest -q`
- **Phase gate:** full suite green (including the 17 still-skipped Phase 3 fixtures) before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/fixtures/nlp_corpus.json` — 50 headlines + 2 filing sections **exported from
      `data/esg_lens.db`**, not invented. Include: the 7 duplicate clusters, the Wisconsin/Copilot
      distinct pair, at least 5 Title Case controversy headlines, the `"MSFT Stock Drop..."` headline,
      and the carbon-emissions headline that scores positive sentiment. Ship the export script
      (`scripts/export_nlp_fixtures.py`) so the fixture is regenerable and its provenance is auditable.
- [ ] `tests/unit/test_nlp_registry.py`, `test_nlp_clean.py`, `test_nlp_entity.py`,
      `test_nlp_classify.py`, `test_nlp_sentiment.py`, `test_nlp_controversy.py`, `test_nlp_pipeline.py`
- [ ] `tests/integration/test_nlp_models.py` — the only tests that touch real weights. Mark
      `@pytest.mark.slow` / skip when `data/models/hub` is absent, so CI without the cache stays green.
- [ ] `tests/integration/test_nlp_perf.py`
- [ ] `tests/conftest.py` — add `stub_pipelines` and `signals_conn` fixtures (below)
- [ ] Framework install: none — `pytest` 8.3.0 already present

## Stubbing HF pipelines in tests

This is the phase's main testability question and ROADMAP criterion 3 depends on it: *"Each gate is
unit-tested with stub models, asserting wiring rather than model accuracy."*

**The design constraint that makes it easy: the registry exposes two named, zero-argument
accessors, and every consumer calls them at call time.** Not module-level imports of the pipeline
object, not `pipeline(...)` constructed inside `classify()`.

```python
# src/esg_lens/nlp/registry.py — shape, not full implementation
_esg_pipe = None
_sent_pipe = None
_spacy_nlp = None
_load_calls = {"esg": 0, "sentiment": 0, "spacy": 0}   # test-observable counters

def get_esg_classifier():
    global _esg_pipe
    if _esg_pipe is None:
        _load_calls["esg"] += 1
        _esg_pipe = pipeline("text-classification",
                             model=ESG_MODEL, device=-1, top_k=None,
                             batch_size=cfg.nlp.batch_size,
                             truncation=True, max_length=512)
    return _esg_pipe
```

### The stub must match the verified output shape exactly

```python
# tests/conftest.py
import pytest

def _esg_stub(label: str, score: float):
    """Mimic transformers 4.44 text-classification with top_k=None:
    list[list[dict]], one inner list per input, sorted desc by score.
    Verified shape — see 02-RESEARCH.md 'Verified API Reference'."""
    def _call(texts, **kwargs):
        if isinstance(texts, str):
            texts = [texts]
        rest = (1.0 - score) / 8
        return [
            sorted(
                [{"label": label, "score": score}]
                + [{"label": l, "score": rest} for l in _OTHER_LABELS if l != label],
                key=lambda d: -d["score"],
            )
            for _ in texts
        ]
    return _call

@pytest.fixture()
def stub_pipelines(monkeypatch):
    """Patch the registry accessors. Nothing downloads, nothing loads."""
    from esg_lens.nlp import registry
    state = {"esg": _esg_stub("Climate Change", 0.90),
             "sentiment": lambda t, **k: [[{"label": "negative", "score": 0.8},
                                           {"label": "neutral",  "score": 0.15},
                                           {"label": "positive", "score": 0.05}]
                                          for _ in ([t] if isinstance(t, str) else t)]}
    monkeypatch.setattr(registry, "get_esg_classifier", lambda: state["esg"])
    monkeypatch.setattr(registry, "get_sentiment", lambda: state["sentiment"])
    return state    # tests mutate state["esg"] to drive each gate
```

Notes the planner should not lose:
- **Patch the accessor, not the module-level name in the consumer.** `monkeypatch.setattr(registry, ...)`
  only works if `classify.py` does `from esg_lens.nlp import registry` then `registry.get_esg_classifier()`,
  never `from esg_lens.nlp.registry import get_esg_classifier`. Make that an explicit convention;
  it is the single most common way this pattern silently stops working.
- **spaCy is not stubbed.** It is 13 MB, already installed, and loads in well under a second. The
  controversy and entity tests need the *real* lemmatizer, because Pitfall 1 is precisely a
  lemmatizer behaviour — a stubbed spaCy would have hidden it. Cache it with a session-scoped
  fixture so the suite loads it once:
  ```python
  @pytest.fixture(scope="session")
  def spacy_nlp():
      import spacy
      return spacy.load("en_core_web_sm")
  ```
- **Guard the integration tests.** `pytest.mark.skipif(not (PROJECT_ROOT/"data/models/hub").exists())`
  so a fresh clone without the cache does not fail; but the phase gate must run them.
- **`HF_HUB_OFFLINE=1`** in the integration test environment guarantees no network. Verified working
  in this session — both models resolved from `data/models/hub` with the hub offline.
- **No accuracy assertions anywhere in `tests/unit/`.** The only model-behaviour assertion permitted
  is the label-set contract test, which checks *names*, not predictions.

## Asserting models load exactly once (ROADMAP criterion 2)

Three complementary assertions; the plan should include at least the first two.

**1. Load-call counter** (strongest, and it survives refactoring):
```python
def test_models_load_once(monkeypatch, fifty_headlines):
    from esg_lens.nlp import registry, pipeline as nlp_pipeline
    calls = {"n": 0}
    real = registry._build_esg_classifier          # the uncached inner builder
    def counting_builder():
        calls["n"] += 1
        return _esg_stub("Climate Change", 0.9)
    monkeypatch.setattr(registry, "_build_esg_classifier", counting_builder)
    registry.reset()                                # clears the module singletons
    nlp_pipeline.process_documents(fifty_headlines, conn)
    assert calls["n"] == 1, f"classifier built {calls['n']}x — load is inside the loop"
```
Requires the registry to separate `_build_*` (constructs) from `get_*` (caches) — a small design
requirement worth stating in the plan, because it is what makes NLP-01 mechanically checkable
rather than a code-review opinion.

**2. Identity assertion** (cheap, catches a cache that returns a fresh object):
```python
assert registry.get_esg_classifier() is registry.get_esg_classifier()
```

**3. Batch-size assertion** (catches per-document inference, which is the *other* half of the same
mistake): make the stub record `len(texts)` on every call and assert
`max(recorded) > 1` for a 50-document run. A pipeline that loads once but still calls the model
50 times with one string each passes tests 1 and 2 and is still the bug the trap warns about.

## Code Examples

### Near-duplicate clustering (`nlp/clean.py`)
```python
# Source: verified against data/esg_lens.db in this session — 7 clusters / 12 docs at 0.70
import re
from itertools import combinations

_STOP = frozenset("a an the of to in on for and or with at by from as is are was "
                  "were be been it its this that new".split())
_TICKER_RE = re.compile(r"\((?:nasdaq|nyse|nsadaq)[^)]*\)")

def normalize_title(title: str) -> str:
    t = title.lower()
    t = _TICKER_RE.sub(" ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def content_tokens(title: str) -> frozenset[str]:
    return frozenset(w for w in normalize_title(title).split()
                     if w not in _STOP and len(w) > 1)

def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

def cluster_near_duplicates(docs, threshold: float) -> dict[int, int]:
    """docs: sequence with .id, .title. Returns {doc_id: cluster_root_id} (union-find)."""
    parent = {d.id: d.id for d in docs}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    toks = {d.id: content_tokens(d.title or "") for d in docs}
    for a, b in combinations(docs, 2):
        if jaccard(toks[a.id], toks[b.id]) >= threshold:
            parent[find(a.id)] = find(b.id)
    return {d.id: find(d.id) for d in docs}
```
Complexity is O(n^2) in documents per ticker. At n=101 that is 5,050 set intersections — measured
in this session at well under 100 ms. The cap is `MAX_TICKERS_PER_JOB=25` tickers x ~150 docs, so
this stays trivial. If a ticker ever exceeds ~2,000 documents, revisit with MinHash/LSH.

### Retention rule
```python
# Source: config/sources.yaml tier map — lower tier number == more credible
def pick_survivor(cluster_docs, tier_of) -> int:
    return min(cluster_docs,
               key=lambda d: (tier_of(d.domain, d.source),   # 1 beats 4
                              d.published_at or "9999",      # then earliest
                              d.id)).id                      # then deterministic
```

### Controversy over a filing section (MAX + union of terms)
```python
def section_controversy(sentences, severity_fn, max_terms: int = 20):
    """MAX effective severity across sentences (scoring_methodology.md §6.1 —
    controversies are non-averageable). Terms are the UNION, order-stable."""
    best, terms, seen = 0, [], set()
    for s in sentences:
        sev, hits = severity_fn(s)          # already negation-adjusted
        best = max(best, sev)
        for h in hits:
            k = h.lower()
            if k not in seen:
                seen.add(k)
                terms.append(h)
    return best, terms[:max_terms]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `pipeline(..., return_all_scores=True)` | `pipeline(..., top_k=None)` | transformers 4.x | `return_all_scores` emits a deprecation warning; `top_k=None` is the supported spelling in 4.44.0 [VERIFIED: `top_k=None` executed cleanly in session] |
| `nlp.make_doc` for all PhraseMatcher patterns | `nlp.pipe` when `attr="LEMMA"` | spaCy 3.x | The `make_doc` optimisation is correct only for `ORTH`/`LOWER`; with `LEMMA` it produces empty lemmas and zero matches [VERIFIED: executed in session] |
| Rely on `tokenizer.model_max_length` | Pass `max_length=512` explicitly | always, for these two checkpoints | Both models ship the `1e30` sentinel; `truncation=True` alone is a `RuntimeError` [VERIFIED: reproduced in session] |

**Deprecated/outdated in this repo:**
- `src/esg_lens/nlp/registry.py` and `clean.py` — pre-GSD boilerplate, zero coverage, broken import
  path. Delete.
- `config/scoring.yaml: tau` as a scalar — superseded by D-2-01.
- `config/controversy_lexicon.yaml: negation_guard.phrases` containing bare `dropped`, and its
  "4 tokens before" comment.

## Security Domain

This is a single-user, localhost, no-auth tool (`handoff_to_backend.md` §2 assumption 1) and
Phase 2 exposes no network surface. The relevant controls are narrow but not zero.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | No auth in v1 by design (assumption 1) |
| V3 Session Management | no | No sessions |
| V4 Access Control | no | Local single user |
| V5 Input Validation | **yes** | Document text is untrusted third-party input. Parameterised SQL only (the repo already uses `?` placeholders throughout `collectors/base.py`); length-cap text before inference |
| V6 Cryptography | no | No secrets handled in this phase. `content_hash` is SHA-256 and is an identity key, not a security control |
| V12 File/Resource | **yes** | `HF_HOME=data/models` must be a project-relative path; model weights are third-party artefacts loaded from a pinned local cache |
| V14 Configuration | **yes** | `HF_HUB_OFFLINE=1` in tests prevents inference-time network egress |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Adversarial/garbage document text driving unbounded memory (a 10-MB filing section) | Denial of Service | `max_sentences_per_section` cap + `max_length=512` truncation. Both are already required for correctness |
| SQL injection via document title into `esg_signals` | Tampering | Parameterised queries only — never f-string SQL. Mirror `collectors/base.py` |
| Untrusted YAML loaded as config | Tampering / RCE | `yaml.safe_load` (already used in `config.py`) — never `yaml.load` |
| Model weights fetched at runtime from an unpinned remote | Tampering / Supply chain | Weights are already cached under `data/models/hub`. Pin model revisions if the planner wants to go further; at minimum set `HF_HUB_OFFLINE=1` in CI so a silent re-download cannot substitute different weights |
| `controversy_terms` written unescaped into a JSON TEXT column | Tampering | `json.dumps` the list; never string-concatenate |

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python venv | everything | yes | 3.12 (`.venv`) | — |
| `torch` | inference | yes | 2.4.0 | — |
| `transformers` | inference | yes | 4.44.0 | — |
| `spacy` | NER, split, matcher | yes | 3.7.5 | — |
| `en_core_web_sm` | NLP-02/03/06 | yes | 3.7.1 | — |
| `yiyanghkust/finbert-esg-9-categories` | NLP-04 | yes — `data/models/hub/models--yiyanghkust--finbert-esg-9-categories` | cached | — |
| `ProsusAI/finbert` | NLP-05 | yes — `data/models/hub/models--ProsusAI--finbert` | cached | — |
| `pytest` + `pytest-asyncio` | all tests | yes | 8.3.0 / 0.23.5 | — |
| `rapidfuzz` | (considered) | **no** | — | stdlib Jaccard — recommended anyway |
| `datasketch` | (considered) | **no** | — | stdlib Jaccard — recommended anyway |
| `scikit-learn` | (considered) | **no** | — | not needed |
| **AAPL / XOM corpus** | fixtures | **no** | — | see below |
| **`company_aliases` rows** | NLP-03 | **no (0 rows)** | — | fail-open gate + deterministic seeding (Pitfall 4) |

**Missing with no fallback:** none — nothing blocks execution.

**Missing with fallback:**
- **The 242-document AAPL/XOM corpus described in `02-CONTEXT.md` is not in the database.**
  `data/esg_lens.db` (last written 2026-09-14 01:25) contains **114 rows, all MSFT**: 101 `rss`/`news`
  + 13 `edgar`/`filing_section`, from exactly one `collection_runs` row per source. `companies` has
  one row: `MSFT | MSFT | (null sector)`. [VERIFIED: queried in session] The DB was evidently
  re-initialised after the benchmark described in the context. This does not invalidate the
  performance numbers (do not re-measure, per the brief) but it does mean **fixtures must be drawn
  from the MSFT corpus, or AAPL/XOM must be re-collected first.** The MSFT corpus is in several ways
  *better* for this phase — it contains a dense, unambiguous near-duplicate cluster and a large Title
  Case controversy population, both of which drove the findings above. Recommend using it and saying
  so; a re-collect is optional and should not block Wave 0.
- The 13 EDGAR sections give real filing-section fixtures for D-2-04. Only MSFT, but the sentence
  cap, MAX-severity and relevant-only-mean rules do not depend on issuer.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `near_duplicate_jaccard = 0.70` generalises beyond MSFT | Open Question 1 | A ticker whose coverage is more homogeneous could see over-collapse. **Mitigation:** log every collapse with both titles and the score, so the first real run is auditable; the threshold is a config key and appears in the Phase 5 ±25% sensitivity sweep |
| A2 | `max_sentences_per_section = 400` is a safe cap | Open Question 2 | A 10-K Item 1A exceeding 400 material sentences would be silently truncated. **Mitigation:** log truncation with the original count; only 13 real sections exist and none were measured against this cap |
| A3 | `sentiment_label_band = 0.15` | Open Question 2 | Only affects the cosmetic `sentiment_label`; `sentiment_score` is the field Phase 3 uses. Low risk |
| A4 | Sentiment should be relevance-weighted rather than flat-averaged across sentences | Open Question 2 | Reasoned by analogy to D-2-04's relevance rule and §5's `w_conf`, not derived from a doc. A flat mean is defensible. **User should confirm** |
| A5 | `entity_gate` fails **open** when a ticker has no aliases | Pitfall 4 | Lets through documents about the wrong company when metadata is missing. The alternative (fail closed) discards the entire news corpus. **User should confirm this trade** — it is the same class of judgement as `insufficient_data` vs a fabricated 50, and it deserves an explicit decision |
| A6 | `duplicate_of` should be a new nullable column rather than a key in `entities_json` | Open Question 1 | A schema change touches `data_model.md` and `schema.sql`, which Phase 0 criterion 5 diffs. **User should confirm** before the planner writes a migration |
| A7 | Bumping `controversy_lexicon.yaml` version to `lex@2026-09b` | Config changes | Forces reprocessing of all existing signals. Correct per NLP-07 semantics, but it is a deliberate cache invalidation and should be stated |
| A8 | The MSFT corpus is acceptable as the fixture source in place of AAPL/XOM | Environment Availability | If the phase is verified against AAPL/XOM expectations, fixtures will not line up. **User should confirm** |

## Open Questions

1. **Should `duplicate_of` be a schema column?**
   - What we know: `esg_signals` has no field for it; D-2-02 says "record the retained document's id
     on the excluded rows if practical"; `entities_json` is an untyped TEXT blob that could carry it.
   - What's unclear: whether a schema change is acceptable mid-milestone given Phase 0 criterion 5
     diffs `schema.sql` against `docs/data_model.md`.
   - Recommendation: add the column and update `data_model.md` in the same task. The audit trail is
     the product's core value proposition; hiding a foreign key in a JSON blob undercuts it.

2. **Fail-open or fail-closed entity gate when aliases are missing?**
   - What we know: aliases are empty today; fail-closed discards 100% of `google_news`.
   - What's unclear: whether the user prefers losing all news to risking a wrong-company document.
   - Recommendation: fail open with a loud warning, and treat empty aliases as a Phase 1 defect to
     fix separately. Surface it in the API's methodology block so a reader knows the gate was off.

3. **Re-collect AAPL/XOM before Wave 0, or build fixtures from MSFT?**
   - What we know: the DB holds only MSFT; the MSFT corpus is richer for this phase's specific
     questions; re-collecting is cheap but re-opens the GDELT/RSS collection path.
   - Recommendation: build from MSFT now; treat an AAPL/XOM re-collect as a Phase 2 verification
     step, not a Wave 0 dependency.

4. **Where does the `_rss_feed` collector change live?**
   - What we know: D-2-03 cannot be implemented reliably without it (Open Question 4).
   - Recommendation: a small Phase 2 task touching `collectors/rss.py` plus a backfill migration.
     It is collector code, but NLP is the only consumer and deferring it blocks D-2-03.

## Sources

### Primary (HIGH confidence — executed in this session against the pinned `.venv`)
- `transformers` 4.44.0 — `AutoConfig.id2label` for both checkpoints; `pipeline(text-classification, top_k=None)` output shape; `truncation`/`max_length` failure mode; `tokenizer.model_max_length` sentinel
- `spacy` 3.7.5 / `en_core_web_sm` 3.7.1 — pipeline components; `PhraseMatcher(attr="LEMMA")` semantics; `nlp.pipe` vs `make_doc` for patterns; Title Case `PROPN` lemma behaviour; negation-guard implementation and its six verified cases
- `data/esg_lens.db` — corpus shape (114 rows / MSFT only), `company_aliases` count (0), `raw_json` feed-provenance gap, the full 101-title near-duplicate and controversy analyses
- `tests/unit/test_scoring_fixture.py` — Phase 3 API contract and skip predicate; confirmed 17 skipped
- `pyproject.toml`, `.venv` imports — dependency versions; `rapidfuzz`/`datasketch`/`sklearn` confirmed absent

### Primary (HIGH confidence — repository documents)
- `docs/scoring_methodology.md` §3, §4, §5, §6.1, §8 — polarity, lexicon, weights, non-averageability, worked example
- `docs/data_model.md` — `esg_signals` DDL, `exclusion_reason` CHECK values, `UNIQUE(document_id, model_version)`
- `docs/handoff_to_backend.md` §1 Phase 2, §2, §4 — build order, assumptions, traps
- `docs/architecture.md` §5.2, §6, §7 — NLP contract, technology choices, testing policy
- `docs/research_notes.md` §3.1–§3.5 — model evaluation and the financial-vs-ESG caveat
- `config/scoring.yaml`, `config/controversy_lexicon.yaml`, `config/rss_feeds.yaml`, `config/sources.yaml`
- `src/esg_lens/collectors/base.py`, `src/esg_lens/collectors/rss.py`, `src/esg_lens/config.py`, `tests/conftest.py`, `tests/integration/test_rss.py`
- `.planning/phases/02-nlp-pipeline/02-CONTEXT.md`, `.planning/ROADMAP.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md`

### Secondary (MEDIUM confidence)
- Near-duplicate threshold of 0.70 — empirically derived from 5,050 pairs over a single-ticker corpus. The *method* is verified; the *generalisation* is an inference.

### Tertiary (LOW confidence)
- None. No claim in this document rests on unverified web search or recalled documentation.

## Metadata

**Confidence breakdown:**
- Standard stack: **HIGH** — no new packages; every version confirmed by importing it
- API shapes (transformers, spaCy): **HIGH** — every assertion produced by running code against the pinned environment, with output quoted
- Near-duplicate measure/threshold: **MEDIUM** — method and negative result are verified; the 0.70 value is tuned on 101 documents from one ticker
- Cross-sentence aggregation: **HIGH** for controversy (MAX is directly derivable from §6.1 and the named trap); **MEDIUM** for sentiment (reasoned by analogy — flagged A4)
- Negation guard: **HIGH** — working code with six verified cases, plus a measured false-positive found and fixed
- Pitfalls: **HIGH** — all seven reproduced in-session against real data
- Corpus/environment state: **HIGH** — direct SQL queries

**Research date:** 2026-09-14
**Valid until:** 2026-10-14 (30 days). Everything is version-pinned, so the only realistic
invalidator is the corpus changing — a re-collect or an AAPL/XOM backfill would require re-running
the near-duplicate threshold analysis (`scripts/export_nlp_fixtures.py` should make that a
one-liner).
