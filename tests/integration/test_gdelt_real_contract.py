"""GDELT's REAL response contract — task 01b-01.

WHY THIS FILE EXISTS
--------------------
Every pre-existing GDELT test mocks a 200 response with a JSON body. The live API refuses the
queries Phase 1 constructs and answers with `HTTP 200, content-type: text/html`, body
"Your query was too short or too long." — so the whole suite asserted a contract GDELT does not
honour, stayed green for three days, and could not go red on the defect that made the phase fail
verification (finding N3).

These fixtures are captured from live responses on 2026-09-05/06, not invented:
  - gdelt_query_rejected.txt  — the 200 text/html rejection
  - gdelt_rate_limited.txt    — the 429 plain-text body
  - gdelt_live_artlist.json   — a genuine successful artlist payload

The rule this file enforces: **a source that collected nothing because the API refused every
request must report failure, not success.** Phase 1 recorded `XOM|gdelt|ok|0|0` in
collection_runs — total failure disguised as success — which is precisely how this survived.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
import respx

from esg_lens.collectors.gdelt import GdeltCollector

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GDELT_RE = r".*api\.gdeltproject\.org.*"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def conn(tmp_path):
    """Real schema, so collection_runs assertions exercise the actual DDL."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript((Path("src/esg_lens/db/schema.sql")).read_text())
    yield db
    db.close()


# ---------------------------------------------------------------------------
# The rejection GDELT actually sends
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_rejected_query_is_not_parsed_as_json():
    """200 + text/html must not reach .json(). That threw
    'Expecting value: line 1 column 1 (char 0)' and killed the collector."""
    respx.get(url__regex=GDELT_RE).mock(
        return_value=httpx.Response(
            200,
            text=_text("gdelt_query_rejected.txt"),
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    col = GdeltCollector()
    with pytest.raises(RuntimeError, match="rejected all"):
        await col.fetch("AAPL", aliases=["Apple Inc"], force_refresh=True)


@respx.mock
@pytest.mark.asyncio
async def test_total_rejection_records_failed_not_ok(conn):
    """THE regression guard. safe_fetch must write status=failed, never ok/n_fetched=0.

    Phase 1 shipped `XOM|gdelt|ok|0|0` to the database. An operator reading collection_runs would
    conclude GDELT worked and returned nothing newsworthy, when in fact every request was refused.
    """
    respx.get(url__regex=GDELT_RE).mock(
        return_value=httpx.Response(
            200,
            text=_text("gdelt_query_rejected.txt"),
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    col = GdeltCollector()
    docs = await col.safe_fetch(conn, "XOM", force_refresh=True)

    assert docs == [], "never-raise contract: safe_fetch still returns [] to the caller"
    row = conn.execute(
        "SELECT status, n_fetched, error FROM collection_runs WHERE ticker='XOM' AND source='gdelt'"
    ).fetchone()
    assert row is not None, "a refused collection must still be recorded"
    assert row["status"] == "failed", (
        f"total rejection recorded as {row['status']!r} — this is the N2 regression: "
        "a source that collected nothing because the API refused every query is a failure"
    )
    assert row["n_fetched"] == 0
    assert row["error"] and "rejected" in row["error"].lower()


# ---------------------------------------------------------------------------
# The 429 GDELT actually sends
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_rate_limit_body_is_plain_text_not_json(conn):
    """429 bodies are plain text. Parsing one as JSON is what produced the original crash."""
    respx.get(url__regex=GDELT_RE).mock(
        return_value=httpx.Response(429, text=_text("gdelt_rate_limited.txt"))
    )
    col = GdeltCollector()
    docs = await col.safe_fetch(conn, "AAPL", force_refresh=True)

    assert docs == []
    row = conn.execute(
        "SELECT status, error FROM collection_runs WHERE ticker='AAPL' AND source='gdelt'"
    ).fetchone()
    assert row["status"] == "failed"
    assert "Expecting value" not in (row["error"] or ""), (
        "a JSONDecodeError here means the 429 text body reached .json()"
    )


def test_documented_rate_limit_matches_gdelt_own_message():
    """GDELT's 429 states one request every 5 seconds. Config must not claim faster.

    research_notes.md §2.3 guessed 1/s and marked it [VERIFY]. It was 5x too fast, and the
    grep-based token-bucket test could not catch it.
    """
    import yaml

    rates = yaml.safe_load(Path("config/sources.yaml").read_text())["rate_limits"]
    gdelt_rate = rates["api.gdeltproject.org"]
    assert gdelt_rate <= 0.2, (
        f"config allows {gdelt_rate}/s but GDELT's own 429 says one request every 5 seconds "
        f"(<= 0.2/s): {_text('gdelt_rate_limited.txt')[:60]}"
    )


# ---------------------------------------------------------------------------
# The success path, against a genuine payload
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_live_shaped_success_is_parsed(conn):
    """A real artlist payload must still parse. url_mobile and socialimage are present in live
    responses and absent from the hand-written fixture — the parser must ignore them."""
    respx.get(url__regex=GDELT_RE).mock(
        return_value=httpx.Response(
            200,
            text=(FIXTURES / "gdelt_live_artlist.json").read_text(),
            headers={"content-type": "application/json; charset=utf-8"},
        )
    )
    col = GdeltCollector()
    docs = await col.safe_fetch(conn, "AAPL", force_refresh=True)

    assert len(docs) >= 1
    d = docs[0]
    assert d.source == "gdelt" and d.doc_type == "news"
    assert d.domain == "mactech.com"
    assert d.published_at and d.published_at.endswith("Z")
    assert d.content_hash

    row = conn.execute(
        "SELECT status, n_fetched FROM collection_runs WHERE ticker='AAPL' AND source='gdelt'"
    ).fetchone()
    assert row["status"] == "ok" and row["n_fetched"] >= 1


@respx.mock
@pytest.mark.asyncio
async def test_partial_rejection_keeps_what_succeeded(conn):
    """One refused chunk must not discard documents an earlier chunk already returned, and must
    not fail the whole collection. This is the half of the guard that was right."""
    responses = [
        httpx.Response(
            200,
            text=(FIXTURES / "gdelt_live_artlist.json").read_text(),
            headers={"content-type": "application/json; charset=utf-8"},
        ),
        httpx.Response(
            200,
            text=_text("gdelt_query_rejected.txt"),
            headers={"content-type": "text/html; charset=utf-8"},
        ),
    ]
    respx.get(url__regex=GDELT_RE).mock(side_effect=responses)

    col = GdeltCollector()
    # Two aliases force two queries under the current construction.
    docs = await col.safe_fetch(conn, "AAPL", force_refresh=True)

    row = conn.execute(
        "SELECT status FROM collection_runs WHERE ticker='AAPL' AND source='gdelt'"
    ).fetchone()
    if docs:
        assert row["status"] == "ok", "partial success must not be recorded as total failure"
    else:
        assert row["status"] == "failed"
