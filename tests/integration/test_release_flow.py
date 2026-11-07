"""Exercise the FastAPI job, signal and score flow without live services."""

import json
import time
from contextlib import closing
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from esg_lens.api.main import create_app
from esg_lens.config import settings
from esg_lens.db.engine import get_connection, init_db
from esg_lens.jobs import runner
from esg_lens.nlp import pipeline
from scripts.sensitivity import report, rescore


class Classifier:
    tokenizer = SimpleNamespace(
        model_max_length=512, num_special_tokens_to_add=lambda: 2,
        encode=lambda text, **_: text.split(),
    )

    def __call__(self, texts, **kwargs):
        return [{"label": "Pollution & Waste", "score": .95} for _ in texts]


class Models:
    classifier = Classifier()

    def warm(self):
        pass

    @staticmethod
    def sentiment(texts, **kwargs):
        return [[{"label": "positive", "score": .1}, {"label": "negative", "score": .8},
                 {"label": "neutral", "score": .1}] for _ in texts]

    language = SimpleNamespace(pipe=lambda texts, **_: (
        SimpleNamespace(sents=[SimpleNamespace(text=text)], ents=[]) for text in texts
    ))


class StubControversies:
    def __init__(self, *_):
        pass

    def match(self, doc):
        return 2, ["fine"]


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "esg.db"))
    init_db()
    return settings.DB_PATH


def test_portfolio_to_evidence_and_repeat_analysis(database, monkeypatch):
    monkeypatch.setattr(pipeline, "Controversies", StubControversies)

    async def collect(conn, ticker, since, sources, job_id, force_refresh):
        assert sources == ["edgar"]
        conn.execute("INSERT OR IGNORE INTO companies(ticker,name,fetched_at) VALUES(?,?,?)",
                     (ticker, ticker, runner.utcnow().isoformat()))
        for i in range(3 if ticker == "XOM" else 1):
            conn.execute(
                """INSERT OR IGNORE INTO raw_documents
                   (ticker,source,doc_type,title,published_at,content_hash) VALUES(?,?,?,?,?,?)""",
                (ticker, "edgar", "filing_section", f"{ticker} reports pollution fine {i}",
                 (runner.utcnow() - timedelta(days=1)).isoformat(), str(i)),
            )
        conn.commit()
        return ["edgar delayed"] if ticker == "AAPL" else []

    monkeypatch.setattr(runner, "collect", collect)
    with TestClient(create_app(Models())) as client:
        for _ in range(100):
            if client.get("/api/v1/healthz").status_code == 200:
                break
            time.sleep(.01)
        else:
            pytest.fail("Fixture models did not initialise")
        body = {"tickers": ["xom", "AAPL", "XOM"], "sources": ["edgar"]}
        for _ in range(2):
            queued = client.post("/api/v1/portfolio/analyze", json=body)
            assert queued.status_code == 202
            assert queued.json()["tickers"] == ["XOM", "AAPL"]
            job = client.get(queued.json()["links"]["status"]).json()
            assert job["status"] == "partial"
            assert job["progress"]["percent"] == 100
            assert job["summary"]["scored"] == 1
        score = client.get("/api/v1/company/XOM/score").json()
        assert score["score"]["status"] == "ok"
        assert score["score"]["pillar_weights"] == {"E": 1, "S": 0, "G": 0}
        assert score["evidence"]["top_contributors"][0]["controversy_terms"] == ["fine"]
        assert client.get("/api/v1/company/AAPL/score").json()["score"]["composite"] is None
        assert len(client.get("/api/v1/company/XOM/history").json()["items"]) == 2
        assert len(client.get("/api/v1/company/XOM/documents?pillar=E&included=true").json()["items"]) == 3
        assert "top_contributors" not in client.get(
            "/api/v1/company/XOM/score?include_evidence=false"
        ).json()["evidence"]
        assert client.get("/api/v1/company/XOM/score?as_of=2000-01-01").status_code == 404
        assert client.get("/api/v1/methodology").json()["config_hash"] == score["methodology"]["config_hash"]
        assert "/api/v1/portfolio/analyze" in client.get("/openapi.json").json()["paths"]
        bad = client.post("/api/v1/portfolio/analyze", json={"tickers": ["$$"]})
        assert bad.status_code == 422
        assert bad.headers["content-type"] == "application/problem+json"

    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT payload,composite_score FROM esg_scores JOIN score_snapshots "
            "ON esg_scores.id=score_snapshots.score_id WHERE ticker='XOM' ORDER BY esg_scores.id DESC LIMIT 1"
        ).fetchone()
        snapshot = json.loads(row["payload"])
        assert rescore(snapshot, snapshot["sector"], snapshot["config"]) == pytest.approx(row["composite_score"])
        validation = report(conn)
        assert validation["sample_size"] == 1
        assert validation["benchmark_sample_size"] == 0
        assert validation["spearman"] is None
        assert all(case["factor"] in (.75, 1.25) for case in validation["sensitivity"])
        assert validation["sensitivity"]
        assert conn.execute("SELECT count(*) FROM esg_signals").fetchone()[0] == 4
        assert conn.execute("SELECT count(*) FROM score_snapshots").fetchone()[0] == 4


def test_concurrency_and_stale_recovery(database):
    with closing(get_connection()) as conn:
        job, _ = runner.enqueue(conn, ["AAPL"], False)
        assert runner.enqueue(conn, ["XOM"], False) is None
        with conn:
            conn.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?",
                         ((runner.utcnow() - timedelta(hours=2)).isoformat(), job))
        runner.sweep(conn)
        assert conn.execute("SELECT status,n_failed FROM jobs WHERE id=?", (job,)).fetchone()[:] == ("failed", 1)
        assert runner.enqueue(conn, ["XOM"], False)



def test_dashboard_assets_and_methodology(database):
    with TestClient(create_app(Models())) as client:
        assert client.get('/').status_code == 200
        assert 'Analyse a portfolio' in client.get('/').text
        assert client.get('/assets/app.js').status_code == 200
        assert client.get('/assets/style.css').status_code == 200
        assert client.get('/api/v1/methodology/notes').status_code == 200


def test_provider_health_reports_latest_attempt(database):
    with closing(get_connection()) as conn:
        conn.execute("INSERT INTO collection_runs(ticker,source,status) VALUES('AAPL','yfinance','failed')")
        conn.execute("INSERT INTO collection_runs(ticker,source,status) VALUES('AAPL','yfinance','cached')")
        conn.commit()
    with TestClient(create_app(Models())) as client:
        rows=client.get('/api/v1/sources/health').json()['items']
        assert len(rows)==1
        assert rows[0]['status']=='cached'
