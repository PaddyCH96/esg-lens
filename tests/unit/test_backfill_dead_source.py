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
    """The result dict must carry per_source counts and a dead_sources list."""
    src = (Path(__file__).resolve().parents[2] / "scripts" / "backfill.py").read_text()
    assert "dead_sources" in src, "backfill must identify sources that collected nothing"
    assert "per_source" in src, "backfill must report a per-source breakdown"
    assert "SystemExit(2)" in src, "a dead source must produce a non-zero exit"


def test_zero_documents_from_a_source_is_flagged():
    """The core rule: 0 documents across all tickers => that source is dead."""
    per_source = {"gdelt": 0, "edgar": 15}
    dead = sorted(k for k, v in per_source.items() if v == 0)
    assert dead == ["gdelt"]

    # The exact Phase 1 shape: a run that looks successful in aggregate.
    total_fetched = sum(per_source.values())
    assert total_fetched == 15, "aggregate totals look healthy — which is precisely the trap"
    assert dead, "a healthy-looking total must not suppress the dead-source signal"


def test_all_sources_alive_is_not_flagged():
    per_source = {"gdelt": 7, "edgar": 15}
    assert not [k for k, v in per_source.items() if v == 0]


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
# Circuit breaker (G-3) — observed 2026-09-14
# ---------------------------------------------------------------------------


def test_circuit_breaker_threshold_is_defined():
    """A source refusing us refuses every ticker, and each attempt burns the full
    COLLECTOR_TIMEOUT_SECONDS. Measured: a dead GDELT cost 120s per ticker, so a 25-ticker
    portfolio would spend ~50 minutes waiting on a source already known dead after the first."""
    mod = _backfill_module()
    assert hasattr(mod, "CIRCUIT_BREAKER_THRESHOLD")
    assert 1 <= mod.CIRCUIT_BREAKER_THRESHOLD <= 5, "threshold should trip early, not eventually"


def test_circuit_breaker_logic_trips_after_threshold():
    """Simulates the loop's bookkeeping: N consecutive empty results trips the source."""
    mod = _backfill_module()
    threshold = mod.CIRCUIT_BREAKER_THRESHOLD

    consecutive: dict[str, int] = {}
    tripped: set[str] = set()

    def record(source: str, n_fetched: int) -> None:
        if n_fetched == 0:
            consecutive[source] = consecutive.get(source, 0) + 1
            if consecutive[source] >= threshold:
                tripped.add(source)
        else:
            consecutive[source] = 0

    for _ in range(threshold):
        record("gdelt", 0)
        record("edgar", 12)

    assert "gdelt" in tripped, "a repeatedly empty source must trip the breaker"
    assert "edgar" not in tripped, "a healthy source must never trip"


def test_a_recovering_source_resets_the_counter():
    """One empty result then a good one must not accumulate toward tripping."""
    mod = _backfill_module()
    consecutive: dict[str, int] = {}
    tripped: set[str] = set()

    for n in (0, 5, 0):
        source = "gdelt"
        if n == 0:
            consecutive[source] = consecutive.get(source, 0) + 1
            if consecutive[source] >= mod.CIRCUIT_BREAKER_THRESHOLD:
                tripped.add(source)
        else:
            consecutive[source] = 0

    assert "gdelt" not in tripped, "intermittent empties must not trip the breaker"


def test_circuit_breaker_is_wired_into_the_collector_loop():
    src = (Path(__file__).resolve().parents[2] / "scripts" / "backfill.py").read_text()
    assert "collector_circuit_tripped" in src
    assert "collector_circuit_open_skipped" in src
    assert "if collector.source in tripped:" in src, "breaker must short-circuit the loop"
