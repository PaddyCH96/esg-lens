# ESG Lens — Portfolio Sustainability Analyzer

> Transparent, reproducible ESG signal aggregation for public equities — built entirely on free
> data sources and open-source models.

**Status:** 📋 Planning complete, implementation not started.

---

## Overview

ESG Lens takes a list of stock tickers, collects public data (news via GDELT, filings via SEC
EDGAR, metadata via Yahoo Finance), runs open-source NLP over it (FinBERT-ESG for classification,
FinBERT for sentiment, a rule-based controversy lexicon), and computes a transparent
Environmental / Social / Governance score where **every point is traceable back to a specific
document**.

It is not a rating agency replacement. It is an auditable alternative-data signal, built to be
read and disagreed with.

### What makes it different
- **Fully auditable** — every score cites the documents that produced it, with per-document weights.
- **Weights live in YAML**, are versioned, and are stamped onto every stored score.
- **Honest about gaps** — companies with thin coverage return `insufficient_data`, not a made-up 50.
- **Free to run** — no paid API keys, no GPU required.

---

## Quick start

_Not yet implemented. See `docs/handoff_to_backend.md`._

```bash
git clone <repo> && cd esg-lens
pip install --index-url https://download.pytorch.org/whl/cpu torch==2.6.0
pip install -r requirements.txt
python -m spacy download en_core_web_sm
python scripts/init_db.py
python scripts/warm_models.py
uvicorn esg_lens.api.main:app --reload
# → http://localhost:8000/docs
```

---

## Documentation

| Document | Contents |
|---|---|
| [Research notes](docs/research_notes.md) | Data source and model evaluation, trade-offs, limitations |
| [Scoring methodology](docs/scoring_methodology.md) | The algorithm, weights, pseudocode, worked example |
| [Architecture](docs/architecture.md) | Components, data flow, directory layout, tech choices |
| [Data model](docs/data_model.md) | SQLite schema DDL |
| [API design](docs/api_design.md) | Endpoint contracts |
| [Backend handoff](docs/handoff_to_backend.md) | Build order for the implementing agent |
| [OpenCode routing](docs/opencode_model_routing.md) | Which free model does which task |

---

## Architecture at a glance

```
tickers → collectors (GDELT / EDGAR / yfinance) → SQLite raw_documents
        → NLP pipeline (entity gate → ESG classify → sentiment → controversy) → esg_signals
        → scoring (weights → pillars → composite) → esg_scores
        → FastAPI (async jobs + read endpoints)
```

## Roadmap

- [ ] **Phase 0** — Scaffold, config, DB
- [ ] **Phase 1** — Collectors + caching
- [ ] **Phase 2** — NLP pipeline
- [ ] **Phase 3** — Scoring engine
- [ ] **Phase 4** — API + job runner
- [ ] **Phase 5** — Validation, sensitivity analysis, docs
- [ ] **Phase 6** — Dashboard (separate repo)

## Limitations

_See [research notes §4](docs/research_notes.md). Short version:_ English-only, US-listed bias,
headline-level analysis, unvalidated weights, and **not investment advice**.

## Contributing / License

TBD — target: MIT.

## Disclaimer

ESG Lens is an educational and research tool. It does not provide investment advice, and its
scores are not assured, audited, or validated against any standard.
