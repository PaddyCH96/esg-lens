# Research Notes — ESG Lens

**Status:** planning-phase research. Every external quota/pricing claim below is marked
`[VERIFY]` and must be re-checked at implementation time — free tiers change often.

---

## 1. How ESG scoring with alternative data actually works

### 1.1 The problem with incumbent ESG ratings
Commercial ESG ratings (MSCI, Sustainalytics, Refinitiv, S&P) are the baseline this project
implicitly competes with. Their well-documented weaknesses are also our design constraints:

| Problem | Evidence in literature | Implication for ESG Lens |
|---|---|---|
| **Rater disagreement** | Correlation between major raters is ~0.4–0.6 (Berg, Kölbel & Rigobon, *Aggregate Confusion*, MIT). Credit ratings correlate ~0.99 by comparison. | Do not claim a "true" ESG score. Ship a *transparent, reproducible* score with every input auditable. |
| **Disclosure bias** | Large caps disclose more → score higher. Score partly measures reporting capacity, not performance. | Normalise by data volume; expose `coverage` and `confidence` fields so low-data tickers are visibly low-confidence, not silently low-scored. |
| **Greenwashing** | Self-reported filings are promotional. | Weight *third-party* signals (news) separately from *self-reported* signals (filings), and never let filings alone raise a score much. |
| **Lag** | Ratings update annually/semi-annually. | News-driven signals refresh daily — this is our actual differentiator. |

### 1.2 The three families of approaches
1. **Disclosure-based (self-reported)** — parse 10-K/20-F/sustainability reports, count/classify
   ESG statements. Cheap, structured, but greenwash-prone.
2. **Incident/controversy-based (third-party)** — mine news for negative events (spills, lawsuits,
   labour violations, board scandals). This is the highest-signal, lowest-bias family and is what
   RepRisk built a business on. **ESG Lens leans here.**
3. **Quantitative-metric-based** — emissions intensity, board diversity %, etc. Hard to get free
   and comparable. Out of scope for v1 beyond what yfinance exposes.

### 1.3 Design consensus from the literature
- **Controversies dominate.** Negative events move the needle far more than positive PR.
  Asymmetric weighting (penalties > rewards) is standard practice and empirically justified.
- **Sentiment alone is not ESG.** A stock-price-negative article is not an ESG-negative article.
  Every document needs a two-stage pass: *is this ESG-relevant, and in which pillar?* then
  *what is its polarity?* Skipping the relevance gate is the #1 failure mode of naive builds.
- **Recency decay** is near-universal — an exponential half-life on document age.
- **Source quality weighting** matters (Reuters ≠ a press-release wire). Cheap proxy: a static
  domain tier map.

---

## 2. Free data sources

### 2.1 yfinance (Yahoo Finance)
- **Gives:** company metadata (name, sector, industry, country, market cap), price history, and
  — sometimes — `Ticker.sustainability` with Sustainalytics-derived ESG risk scores. `[VERIFY]`
- **Use in ESG Lens:** company metadata + sector assignment (needed for sector-relative scoring)
  + market cap (needed for the disclosure-bias normalisation). Also `Ticker.news` as a
  supplementary headline feed.
- **Limits/risks:** unofficial scraper of an undocumented endpoint. Breaks without notice, has no
  SLA, and Yahoo's ToS discourage bulk use. `sustainability` has been intermittently empty for
  long stretches.
- **Mitigation:** wrap every call in a `MetadataProvider` interface; cache aggressively (metadata
  TTL 7 days); the system must degrade gracefully when it returns `None`. **Never** consume
  Yahoo's own ESG score as an input to our score — if present, store it only as a
  *benchmark for validation*, in a separate column. Mixing it in destroys the "transparent,
  self-computed" claim.

### 2.2 SEC EDGAR
- **Gives:** full-text 10-K, 10-Q, 8-K, DEF 14A. `data.sec.gov` submissions API + full-text search.
  Genuinely free, no key, official.
- **Rate limit:** 10 req/sec and a **declared `User-Agent` with contact email is mandatory** —
  omitting it gets you blocked. `[VERIFY]`
- **Use in ESG Lens:**
  - **10-K Item 1A (Risk Factors)** and **Item 1 (Business)** → E and S signals.
  - **DEF 14A (proxy)** → the single best free **G** source: board composition, exec comp,
    say-on-pay, auditor. Without proxies, G scoring is nearly empty.
  - **8-K** → material events; good controversy trigger.
- **Limits:** US-listed only (foreign issuers file 20-F, sparser). Filings are huge (10-K ≈ 100k+
  tokens) — you cannot run BERT over the whole thing per company per run. **You must section-split
  first and only NLP the relevant items.** Filing cadence is annual, so filings set a slow-moving
  *baseline*, news provides the *delta*.
- **Ticker→CIK:** use SEC's `company_tickers.json`, cached locally. Do not scrape.

### 2.3 GDELT
- **Gives:** GDELT 2.1 DOC API — global news, 65+ languages, 15-min update cadence, with tone
  scores and themes already attached. No API key.
- **Use in ESG Lens:** the **primary news backbone**. Query by company name + ESG keyword bundles.
  GDELT's `V2Themes` include ready-made ESG-adjacent themes (`ENV_*`, `HUMAN_RIGHTS`,
  `CORRUPTION`, `LABOR_*`) which give a free, cheap pre-filter *before* you spend GPU on BERT.
- **Limits:** returns **titles + URLs + metadata, not full article text**. Undocumented soft rate
  limits; be polite (≤1 req/sec, backoff on 429). Entity matching is noisy — "Apple" matches
  fruit-industry news; you need a disambiguation layer (company name + ticker + domain terms, or
  spaCy NER confirmation that the ORG entity is actually the subject).
- **Trade-off:** headline-only analysis is weaker than full-text, but avoids scraping paywalled
  sites. **v1 decision: score on titles + GDELT metadata only.** Full-text fetching is deferred
  (legal + reliability cost, marginal accuracy gain).

### 2.4 NewsAPI (free tier)
- **Free tier:** development-only, ~100 requests/day, **articles delayed ~24h**, and results are
  truncated to a short `content` snippet. Commercial use prohibited on the free plan. `[VERIFY]`
- **Verdict:** **supplementary only, behind a feature flag.** Its licence makes it unsuitable as
  the backbone of an open-source project other people will run. GDELT carries v1.
- **Alternatives worth evaluating in the same slot:** RSS feeds from major outlets (free, legal,
  full headlines), and Yahoo's `Ticker.news`.

### 2.5 Source summary

| Source | Key needed | Cost | Role in v1 | Refresh |
|---|---|---|---|---|
| GDELT DOC 2.1 | No | Free | Primary news signal | daily |
| SEC EDGAR | No (UA required) | Free | Filings baseline, G pillar | quarterly |
| yfinance | No | Free | Metadata, sector, benchmark | weekly |
| NewsAPI free | Yes | Free/limited | Optional supplement (flagged off) | daily |

---

## 3. Open-source NLP model evaluation

All candidates are Hugging Face hosted, permissively licensed, and CPU-runnable.

### 3.1 `yiyanghkust/finbert-esg` and `finbert-esg-9-categories`
- **What:** FinBERT (BERT pre-trained on financial corpora) fine-tuned for ESG classification.
  The 4-class variant emits `Environmental | Social | Governance | None`; the 9-category variant
  emits fine-grained themes (Climate Change, Natural Capital, Pollution & Waste, Human Capital,
  Product Liability, Community Relations, Corporate Governance, Business Ethics & Values, Non-ESG).
- **Why it matters:** the `None`/`Non-ESG` class **is our relevance gate** — the single most
  important component in the pipeline. The 9-category output maps directly onto the
  category-weight table in the scoring methodology.
- **Limits:** trained on ~2k annotated sentences → it is a *sentence-level* classifier. Feed it
  sentences or headlines, not paragraphs. Domain is corporate-report English; headline register is
  slightly out of domain. Small training set means calibration is mediocre — treat the softmax as
  a ranking, not a probability.
- **Decision: adopt `finbert-esg-9-categories` as the primary classifier.**

### 3.2 `ProsusAI/finbert` (sentiment)
- **What:** 3-class financial sentiment — positive / negative / neutral.
- **Critical caveat:** it predicts sentiment *from an investor's point of view*, not an ESG point
  of view. "Company settles pollution lawsuit for less than expected" is financially positive and
  environmentally negative. **Do not read FinBERT polarity as ESG polarity.**
- **Decision: adopt, but only as one feature.** ESG-direction must come from combining
  FinBERT polarity with a controversy detector (§3.5). Document this in the methodology as a known
  limitation with a worked counter-example.

### 3.3 ClimateBERT (`climatebert/*`)
- **Useful heads:** `distilroberta-base-climate-detector` (is this climate-related?),
  `...-climate-sentiment`, `...-climate-commitment` (commitment vs. action — a greenwashing
  detector), `...-climate-specificity` (vague vs. specific claims).
- **Value:** `commitment` + `specificity` together are a genuinely differentiating **greenwashing
  signal** for the E pillar: high commitment + low specificity + self-reported source = discount.
- **Limits:** climate only — covers a slice of E, none of S or G. DistilRoBERTa base, so cheap.
- **Decision: adopt for the E pillar and the greenwashing modifier — but post-v1.**
  (Not a `handoff_to_backend.md` phase number: that scheme is Phase 0–5, all within v1.
  This means a later milestone, after v1 ships.)

### 3.4 `facebook/bart-large-mnli` (zero-shot)
- **Value:** no training data needed; arbitrary label sets; excellent for prototyping and for the
  long tail of categories nobody has a fine-tuned model for.
- **Limits:** ~1.6 GB, and it runs one forward pass **per candidate label**. With 9 labels that is
  9× the compute of FinBERT-ESG — on CPU, roughly 1–3 s/document vs ~50 ms. It is the single
  biggest performance risk in the design.
- **Decision: do NOT put it on the hot path.** Use it (a) offline to generate silver labels for
  evaluating FinBERT-ESG, and (b) as a fallback for documents FinBERT-ESG scores below a
  confidence threshold. Gate behind config `nlp.zero_shot.enabled = false` by default.
  If a lighter zero-shot is wanted, evaluate `MoritzLaurer/deberta-v3-base-mnli` — far smaller.

### 3.5 spaCy (`en_core_web_sm` / `_trf`)
- **Roles:** (1) **entity disambiguation** — confirm the ORG in the doc is our company, killing
  the "Apple the fruit" class of false positives; (2) sentence segmentation before FinBERT;
  (3) rule-based `Matcher`/`PhraseMatcher` for the **controversy lexicon** (`lawsuit`, `fine`,
  `spill`, `recall`, `strike`, `probe`, `settlement`, `bribery`, `layoffs`).
- **Note:** a deterministic keyword/lexicon controversy detector is *better than a model here* —
  it is auditable, which is the whole premise of the product. Keep the lexicon in a versioned
  YAML file, not in code.
- **Decision: adopt `en_core_web_sm`. Do not use `_trf` (slow, marginal gain for this task).**

### 3.6 Model stack decision

| Stage | Model | v1? | Approx. CPU cost |
|---|---|---|---|
| Sentence split + NER disambiguation | spaCy `en_core_web_sm` | ✅ | ~5 ms/doc |
| ESG relevance + 9-category | `yiyanghkust/finbert-esg-9-categories` | ✅ | ~40 ms/sentence |
| Financial sentiment | `ProsusAI/finbert` | ✅ | ~40 ms/sentence |
| Controversy detection | spaCy rules + YAML lexicon | ✅ | negligible |
| Climate commitment/specificity | `climatebert/*` | post-v1 | ~20 ms/sentence |
| Zero-shot fallback | `bart-large-mnli` | Off by default | ~1–3 s/doc |

Total v1 budget: **~10 s per company** for ~50 headlines on a laptop CPU. Acceptable for an
async job API. This is why §Architecture makes analysis a background job, not a sync request.

---

## 4. Key limitations to state publicly in the README
1. **Not investment advice.** No licence, no assurance, no audit.
2. **English-language, US-listed bias.** EDGAR is US-only; models are English-only.
3. **Headline-level analysis**, not full-text — v1 reads titles, not article bodies.
4. **Coverage bias** — large caps generate more news, so their scores are higher-confidence.
   Small caps may return `insufficient_data` rather than a misleadingly precise number.
5. **Financial ≠ ESG sentiment** — see §3.2.
6. **Unvalidated.** There is no ground truth. The correct honest framing is
   *"a transparent, reproducible ESG signal aggregator"*, not *"an ESG rating"*.
   Validation plan: correlate against yfinance's Sustainalytics score as a sanity check and
   **report the correlation openly**, including if it is low.

---

## Sources
- [yiyanghkust/finbert-esg-9-categories](https://huggingface.co/yiyanghkust/finbert-esg-9-categories)
- [ESGBERT org on Hugging Face](https://huggingface.co/ESGBERT/models)
- [climatebert on Hugging Face](https://huggingface.co/climatebert)
- [ESG-BERT overview](https://www.aimodels.fyi/models/huggingFace/esg-bert-nbroad)
- [Bridging the gap in ESG measurement (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S1544612324000096)
