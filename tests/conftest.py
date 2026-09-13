"""Test fixtures — in-memory SQLite + config overrides."""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest
from hishel import AsyncSqliteStorage

from esg_lens.config import settings

# Every network-touching test mocks httpx with respx, so real token-bucket waits buy nothing and
# cost the suite roughly two minutes (0.2 req/s x 3 GDELT pillar queries per test). Disable the
# limiter process-wide for tests. tests/unit/test_rate_limit_behaviour.py constructs
# TokenBucketTransport directly with enabled defaulting to True, so the limiter is still tested.
settings.RATE_LIMIT_ENABLED = False
# Mocked 429/503 responses otherwise cost ~16s each in tenacity backoff. Retry logic itself is
# covered by test_http_client.py::test_retry_predicate_only_retries_429_and_5xx.
settings.HTTP_MAX_RETRY_ATTEMPTS = 1


@pytest.fixture()
def hishel_temp_storage(tmp_path):
    """Temporary hishel AsyncSqliteStorage with 24h TTL for unit tests."""
    storage = AsyncSqliteStorage(database_path=tmp_path / "http.sqlite", default_ttl=24 * 3600)
    return storage


@pytest.fixture()
def db_conn():
    """In-memory DB with schema applied and required pragmas."""
    from esg_lens.db.engine import get_connection

    # Use temp file so WAL/foreign_keys pragmas are testable
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = Path(tf.name)

    # Import here to avoid circular
    from pathlib import Path as _P

    schema_path = _P(__file__).resolve().parents[1] / "src" / "esg_lens" / "db" / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.executescript(schema_sql)
    conn.commit()
    yield conn
    conn.close()
    db_path.unlink(missing_ok=True)
    # WAL files
    for suffix in ("-wal", "-shm"):
        (Path(str(db_path) + suffix)).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Phase 2 NLP fixtures (02-01 Task 2b)
#
# NOTE ON THE G-4 GATE: this module legitimately reads tests/fixtures/nlp_corpus.json. It is
# fixture plumbing, not assertions, and is the one deliberate carve-out from the Phase 2 rule
# that a test file may not open files. It is never passed to the gate.
# ---------------------------------------------------------------------------

# `scripts/` is not an installed package. Putting it on sys.path here (conftest is the G-4
# carve-out for plumbing) lets tests `import export_nlp_fixtures` without constructing a path
# inside the test module, which the gate forbids.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
NLP_CORPUS_PATH = FIXTURE_DIR / "nlp_corpus.json"

# The eight ESG categories carried by config/scoring.yaml `category_weights`, plus the
# ninth label FinBERT-ESG emits for everything else. Hard-coding the order here is fine:
# the stubs only need a stable, complete label set to distribute residual probability over.
ESG_LABELS = (
    "Climate Change",
    "Natural Capital",
    "Pollution & Waste",
    "Human Capital",
    "Product Liability",
    "Community Relations",
    "Corporate Governance",
    "Business Ethics & Values",
    "Non-ESG",
)
SENTIMENT_LABELS = ("positive", "negative", "neutral")


class _RecordingStub:
    """A stand-in for a transformers text-classification pipeline.

    Reproduces the shape verified against transformers 4.44 with ``top_k=None``::

        list[list[dict]]    # one inner list per input text
                            # each inner list sorted DESCENDING by score
                            # each element {"label": str, "score": float}

    Every call appends ``len(texts)`` to :attr:`batch_sizes`, so a test can prove the pipeline
    was actually called in batches rather than one document at a time — the NLP-01 failure mode
    that a plain "was it called?" assertion cannot see.
    """

    def __init__(self, labels, top_label, top_score):
        self.labels = tuple(labels)
        self.top_label = top_label
        self.top_score = float(top_score)
        self.batch_sizes: list[int] = []
        self.calls: list[list[str]] = []

    def _scores(self):
        others = [lbl for lbl in self.labels if lbl != self.top_label]
        residual = (1.0 - self.top_score) / len(others) if others else 0.0
        scored = [{"label": self.top_label, "score": self.top_score}]
        scored += [{"label": lbl, "score": residual} for lbl in others]
        return sorted(scored, key=lambda d: d["score"], reverse=True)

    def __call__(self, texts, **kwargs):
        single = isinstance(texts, str)
        batch = [texts] if single else list(texts)
        self.batch_sizes.append(len(batch))
        self.calls.append(batch)
        return [self._scores() for _ in batch]


def _esg_stub(label="Climate Change", score=0.9):
    """Counting stub for the FinBERT-ESG 9-category classifier."""
    if label not in ESG_LABELS:
        raise ValueError(f"{label!r} is not a FinBERT-ESG label; expected one of {ESG_LABELS}")
    return _RecordingStub(ESG_LABELS, label, score)


def _sent_stub(label="negative", score=0.9):
    """Counting stub for the ProsusAI/finbert sentiment head."""
    if label not in SENTIMENT_LABELS:
        raise ValueError(f"{label!r} is not a sentiment label; expected one of {SENTIMENT_LABELS}")
    return _RecordingStub(SENTIMENT_LABELS, label, score)


@pytest.fixture()
def stub_pipelines(monkeypatch):
    """Replace both HF accessors on the registry MODULE with counting stubs.

    Patching the module attribute — not an imported name — is what makes this work; see the
    consumption note in ``esg_lens/nlp/registry.py``.

    Yields a mutable dict so a test can swap in a differently-configured stub mid-test::

        def test_x(stub_pipelines):
            stub_pipelines["esg"] = _esg_stub("Corporate Governance", 0.95)
    """
    from esg_lens.nlp import registry

    stubs = {"esg": _esg_stub(), "sentiment": _sent_stub()}
    monkeypatch.setattr(registry, "get_esg_classifier", lambda: stubs["esg"])
    monkeypatch.setattr(registry, "get_sentiment", lambda: stubs["sentiment"])
    return stubs


@pytest.fixture(scope="session")
def spacy_nlp():
    """The REAL en_core_web_sm pipeline, loaded once per session.

    spaCy is deliberately NOT stubbed. The Pitfall 1 Title Case failure is a lemmatizer
    behaviour, so a stubbed spaCy would hide precisely the bug this phase exists to prevent.
    """
    from esg_lens.nlp import registry

    return registry.get_spacy()


@pytest.fixture()
def capture_logs():
    """Yield the list of structlog events emitted inside the fixture's scope.

    02-02 asserts `entity_gate_skipped_no_aliases` and 02-03 asserts the per-document
    `ticker`/`document_id` binding; both plans are forbidden from editing this file, so the
    fixture lives here rather than being reinvented twice.
    """
    import structlog.testing

    with structlog.testing.capture_logs() as records:
        yield records


@pytest.fixture(scope="session")
def nlp_corpus():
    """The exported real-corpus fixture. Regenerate with scripts/export_nlp_fixtures.py."""
    import json

    if not NLP_CORPUS_PATH.exists():
        pytest.fail(
            f"{NLP_CORPUS_PATH} is missing. Regenerate it with:\n"
            "    PYTHONPATH=src .venv/bin/python scripts/export_nlp_fixtures.py"
        )
    return json.loads(NLP_CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def signals_conn(db_conn, nlp_corpus):
    """`db_conn` pre-loaded with the three companies and the whole fixture corpus.

    Real foreign keys, real ids: a record's `id` here is its `raw_documents.id` in
    `data/esg_lens.db`, so a failing pipeline test can be traced straight back to the source
    document. `content_hash` is synthesised — the export does not carry it and the UNIQUE
    constraint only needs it to be stable and distinct.
    """
    names = {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation", "XOM": "Exxon Mobil Corporation"}
    for ticker, name in names.items():
        db_conn.execute(
            "INSERT INTO companies (ticker, name, sector, fetched_at, updated_at) "
            "VALUES (?, ?, ?, '2026-09-14T00:00:00Z', '2026-09-14T00:00:00Z')",
            (ticker, name, "Technology" if ticker != "XOM" else "Energy"),
        )
    for record in nlp_corpus:
        db_conn.execute(
            "INSERT INTO raw_documents "
            "(id, ticker, source, doc_type, url, domain, title, body, published_at, "
            " content_hash, collected_at, raw_json) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, '2026-09-14T00:00:00Z', ?)",
            (
                record["id"],
                record["ticker"],
                record["source"],
                record["doc_type"],
                record["domain"],
                record["title"],
                record["body"],
                record["published_at"],
                f"fixture-{record['id']}",
                record["raw_json"],
            ),
        )
    db_conn.commit()
    return db_conn
