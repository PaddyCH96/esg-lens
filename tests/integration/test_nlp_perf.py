"""ROADMAP criterion 1: 50 headlines end-to-end on CPU in under 10 seconds.

REAL models. Skipped when the HF cache is absent, because downloading 400MB inside a timing test
would measure the network, not the pipeline.

This is a REGRESSION GUARD, not a tight budget. Measured headroom during planning was ~2.3s
against the 10s ceiling; the number exists so that reintroducing a per-document model load — the
100x error `handoff_to_backend.md` §4 names — fails loudly instead of quietly costing a minute.
Model load time is deliberately EXCLUDED from the measured window by warming the registry first:
the singletons are loaded once per process in production (Phase 4 warms them in the FastAPI
lifespan), so charging a cold ~1.8s load to a 50-document batch would measure the wrong thing.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from esg_lens.config import settings
from esg_lens.nlp import pipeline, registry

BUDGET_SECONDS = 10.0
HEADLINES = 50

_HUB = Path(settings.HF_HOME) / "hub"

pytestmark = pytest.mark.skipif(
    not _HUB.exists(),
    reason="real model weights are not present in data/models/hub",
)


def test_fifty_headlines_process_end_to_end_within_the_budget(signals_conn):
    docs = signals_conn.execute(
        "SELECT * FROM raw_documents WHERE doc_type = 'news' ORDER BY id LIMIT ?", (HEADLINES,)
    ).fetchall()
    assert len(docs) == HEADLINES, "the fixture corpus must carry at least 50 news documents"

    # Warm the singletons — see the module docstring on why load time is outside the window.
    registry.get_spacy()
    registry.get_esg_classifier()
    registry.get_sentiment()

    started = time.perf_counter()
    result = pipeline.process_documents(docs, signals_conn, model_version="perf@v1")
    elapsed = time.perf_counter() - started

    assert result.processed == HEADLINES
    assert result.inserted == HEADLINES
    assert elapsed < BUDGET_SECONDS, (
        f"{HEADLINES} headlines took {elapsed:.2f}s against a {BUDGET_SECONDS}s budget — "
        "the usual cause is a model being constructed inside a loop"
    )
