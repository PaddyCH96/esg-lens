"""A source that collects nothing must fail loudly — gap G-2, task 01b-03.

Phase 1's backfill exited 0 and printed "Backfill complete: fetched=15 new=15" while GDELT — the
primary news source — returned zero documents for every ticker. Every document came from EDGAR.
That is how a total failure of the project's stated differentiator survived three days and a
"pass" mark on UAT test 1.

The never-raise collector contract is correct and stays: one dead source must not crash a run.
But "did not crash" is not "worked", and nothing was escalating the difference.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


def _backfill_module():
    """Load scripts/backfill.py as a module without executing main()."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "backfill.py"
    spec = importlib.util.spec_from_file_location("_backfill_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_dead_source_detection_is_present():
    """Structural check only — the behaviour is proven by the live-loop tests below."""
    mod = _backfill_module()
    assert hasattr(mod, "CIRCUIT_BREAKER_THRESHOLD")
    assert 1 <= mod.CIRCUIT_BREAKER_THRESHOLD <= 5, "threshold should trip early, not eventually"


def test_exit_code_is_non_zero_when_a_source_is_dead(monkeypatch, capsys):
    """SystemExit(2), not 0. CI, cron, and a skimmed terminal all read the exit code."""
    mod = _backfill_module()

    async def fake_run(*args, **kwargs):
        return {
            "n_fetched": 15,
            "n_new": 15,
            "per_source": {"gdelt": 0, "edgar": 15},
            "dead_sources": ["gdelt"],
        }

    # NOTE: patch the real name, and do NOT pass raising=False. An earlier draft of this test
    # patched "run_backfill" (wrong name) with raising=False, so the patch silently did nothing
    # and the test executed a real backfill against live SEC EDGAR — 200 seconds and real network
    # traffic from a unit test. raising=True turns a wrong name into an immediate error.
    monkeypatch.setattr(mod, "backfill_tickers", fake_run)

    args = mod.parse_args(["--tickers", "AAPL,XOM"])
    with pytest.raises(SystemExit) as exc:
        import asyncio

        asyncio.run(mod._async_main(args))
    assert exc.value.code == 2, "a dead source must exit non-zero"

    out = capsys.readouterr().out
    assert "by source" in out, "operator needs the per-source breakdown to see which died"
    assert "gdelt" in out


# ---------------------------------------------------------------------------
# Circuit breaker (G-3) — driven through the REAL backfill loop.
#
# An earlier draft of these tests reimplemented the loop's bookkeeping inside the test and never
# called backfill_tickers, and two others asserted that strings appeared in backfill.py. Both
# pass whether or not the code works (Phase 1 gap G-4: grep-as-coverage). These drive the actual
# orchestrator with stub collectors instead.
# ---------------------------------------------------------------------------


class _StubCollector:
    """Minimal Collector stand-in: records calls, returns a fixed document count."""

    def __init__(self, source: str, per_call_docs: int) -> None:
        self.source = source
        self.per_call_docs = per_call_docs
        self.tickers_seen: list[str] = []
        self.last_run_id = None

    async def safe_fetch(self, conn, ticker, since=None, *, job_id=None, force_refresh=False):
        self.tickers_seen.append(ticker)
        from esg_lens.collectors.base import RawDocument, content_hash

        out = []
        for i in range(self.per_call_docs):
            title = f"{ticker} {self.source} doc {i}"
            out.append(
                RawDocument(
                    ticker=ticker, source="rss", doc_type="news", title=title,
                    content_hash=content_hash(title, f"{ticker}-{self.source}-{i}"),
                )
            )
        return out

    def update_run_new_count(self, conn, n_new):
        return None


def _run_backfill(mod, monkeypatch, db_conn, collectors, tickers):
    """Drive the real backfill_tickers with stub collectors and no yfinance."""
    import asyncio

    async def _fake_ensure(ticker, conn, force_refresh):
        conn.execute(
            "INSERT OR IGNORE INTO companies (ticker, name, fetched_at) VALUES (?,?,?)",
            (ticker, ticker, ""),
        )
        conn.commit()
        return True

    # raising=True: a renamed seam must fail loudly, not silently no-op (the lesson from the
    # earlier run_backfill/backfill_tickers mispatch that hit live SEC EDGAR for 204 seconds).
    monkeypatch.setattr(mod, "_ensure_company_row", _fake_ensure)
    monkeypatch.setattr(mod, "build_collectors", lambda: collectors)
    return asyncio.run(mod.backfill_tickers(tickers, conn=db_conn))


def test_dead_source_is_skipped_after_threshold(monkeypatch, db_conn):
    """THE behaviour: a source returning nothing must stop being called.

    Measured 2026-09-14: a dead GDELT cost 120s per ticker, so 25 tickers would spend ~50 minutes
    waiting on a source already known dead after the first.
    """
    mod = _backfill_module()
    dead = _StubCollector("deadsource", per_call_docs=0)
    alive = _StubCollector("edgar", per_call_docs=3)

    result = _run_backfill(mod, monkeypatch, db_conn, [dead, alive], ["AAPL", "XOM", "MSFT"])

    assert len(dead.tickers_seen) == mod.CIRCUIT_BREAKER_THRESHOLD, (
        f"dead source was called {len(dead.tickers_seen)}x for 3 tickers; "
        f"breaker should stop it after {mod.CIRCUIT_BREAKER_THRESHOLD}"
    )
    assert len(alive.tickers_seen) == 3, "a healthy source must keep running"
    assert "deadsource" in (result.get("dead_sources") or [])


def test_healthy_sources_never_trip(monkeypatch, db_conn):
    mod = _backfill_module()
    a = _StubCollector("rssfeed", per_call_docs=2)
    b = _StubCollector("edgar", per_call_docs=3)

    result = _run_backfill(mod, monkeypatch, db_conn, [a, b], ["AAPL", "XOM", "MSFT"])

    assert len(a.tickers_seen) == 3 and len(b.tickers_seen) == 3
    assert not result.get("dead_sources")


def test_recovering_source_resets_the_counter(monkeypatch, db_conn):
    """One empty result followed by a good one must not accumulate toward tripping."""
    mod = _backfill_module()

    class _Flaky(_StubCollector):
        async def safe_fetch(self, conn, ticker, since=None, *, job_id=None, force_refresh=False):
            self.per_call_docs = 0 if len(self.tickers_seen) % 2 == 0 else 2
            return await super().safe_fetch(conn, ticker, since, job_id=job_id,
                                            force_refresh=force_refresh)

    flaky = _Flaky("flaky", per_call_docs=0)
    result = _run_backfill(mod, monkeypatch, db_conn, [flaky], ["AAPL", "XOM", "MSFT", "GOOG"])

    assert len(flaky.tickers_seen) == 4, "intermittent empties must not trip the breaker"
    assert not result.get("dead_sources"), "a source that keeps producing is not dead"
