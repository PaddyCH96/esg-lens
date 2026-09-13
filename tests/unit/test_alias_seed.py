"""EDGAR-cached-title alias seeding (NLP-03, 02-02 Task 1).

WHY THIS MODULE EXISTS AT ALL
-----------------------------
yfinance returns 404 for all three tickers, so on the real database `company_aliases` has 0 rows
and `companies.name` is literally 'AAPL' / 'XOM' / 'MSFT'. The entity gate has nothing to match
against in that state and would reject 100% of google_news. Seeding from the already-cached SEC
`company_tickers.json` fixes the root cause without touching the yfinance collector.

These tests never read a source file (G-4). They drive the public function and assert on the
database and on captured structlog records.
"""

from __future__ import annotations

import json

import pytest

from esg_lens.nlp import alias_seed

TICKERS = ("AAPL", "MSFT", "XOM")

# Verified against data/cache/company_tickers.json on 2026-09-14. Note MSFT's SEC title is
# ALL-CAPS 'MICROSOFT CORP', not 'Microsoft Corp' — every alias comparison downstream is
# case-insensitive, which is why that is harmless, but a test that asserted title case would
# have been quietly wrong.
SEC_TITLES = {
    "AAPL": "Apple Inc.",
    "MSFT": "MICROSOFT CORP",
    "XOM": "ExxonMobil Holdings Corp",
}


@pytest.fixture()
def companies_conn(db_conn):
    """Three companies in the state the REAL database is actually in: name == ticker."""
    for ticker in TICKERS:
        db_conn.execute(
            "INSERT INTO companies (ticker, name, fetched_at, updated_at) "
            "VALUES (?, ?, '2026-09-14T00:00:00Z', '2026-09-14T00:00:00Z')",
            (ticker, ticker),
        )
    db_conn.commit()
    return db_conn


def _fake_cache(tmp_path, mapping):
    """Write a SEC-shaped company_tickers.json containing only `mapping`."""
    payload = {
        str(i): {"cik_str": 1000 + i, "ticker": t, "title": title}
        for i, (t, title) in enumerate(mapping.items())
    }
    path = tmp_path / "company_tickers.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _aliases(conn, ticker):
    return {
        row["alias"]: row["alias_type"]
        for row in conn.execute(
            "SELECT alias, alias_type FROM company_aliases WHERE ticker = ?", (ticker,)
        )
    }


def _name(conn, ticker):
    return conn.execute("SELECT name FROM companies WHERE ticker = ?", (ticker,)).fetchone()["name"]


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
def test_bare_ticker_name_is_replaced_by_the_cached_edgar_title(companies_conn, tmp_path):
    cache = _fake_cache(tmp_path, SEC_TITLES)

    result = alias_seed.seed_from_edgar_cache(companies_conn, cache_path=cache)

    assert _name(companies_conn, "AAPL") == "Apple Inc."
    assert _name(companies_conn, "XOM") == "ExxonMobil Holdings Corp"
    assert result["names_set"] == len(TICKERS)


def test_an_existing_non_ticker_name_survives_because_yfinance_data_wins(companies_conn, tmp_path):
    companies_conn.execute("UPDATE companies SET name = ? WHERE ticker = 'AAPL'", ("Apple Inc. (yf)",))
    companies_conn.commit()
    cache = _fake_cache(tmp_path, SEC_TITLES)

    result = alias_seed.seed_from_edgar_cache(companies_conn, cache_path=cache)

    assert _name(companies_conn, "AAPL") == "Apple Inc. (yf)"
    # The other two were still bare tickers and were still set.
    assert result["names_set"] == len(TICKERS) - 1


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------
def test_legal_and_suffix_stripped_common_aliases_are_inserted(companies_conn, tmp_path):
    cache = _fake_cache(tmp_path, SEC_TITLES)

    alias_seed.seed_from_edgar_cache(companies_conn, cache_path=cache)

    aapl = _aliases(companies_conn, "AAPL")
    assert aapl["Apple Inc."] == "legal"
    assert aapl["Apple"] == "common"


def test_corporate_suffixes_are_stripped_repeatedly(companies_conn, tmp_path):
    """'ExxonMobil Holdings Corp' must yield 'ExxonMobil', not 'ExxonMobil Holdings'."""
    cache = _fake_cache(tmp_path, SEC_TITLES)

    alias_seed.seed_from_edgar_cache(companies_conn, cache_path=cache)

    assert "ExxonMobil" in _aliases(companies_conn, "XOM")


def test_the_bare_ticker_is_inserted_as_a_common_alias(companies_conn, tmp_path):
    cache = _fake_cache(tmp_path, SEC_TITLES)

    alias_seed.seed_from_edgar_cache(companies_conn, cache_path=cache)

    assert _aliases(companies_conn, "MSFT")["MSFT"] == "common"


def test_a_variant_equal_to_the_ticker_is_not_inserted_twice(companies_conn, tmp_path):
    """A company whose SEC title IS the ticker must not produce a duplicate or a 'legal' ticker."""
    cache = _fake_cache(tmp_path, {"MSFT": "MSFT"})

    alias_seed.seed_from_edgar_cache(companies_conn, cache_path=cache)

    assert _aliases(companies_conn, "MSFT") == {"MSFT": "common"}


def test_variants_shorter_than_three_characters_are_skipped(companies_conn, tmp_path):
    cache = _fake_cache(tmp_path, {"XOM": "BP plc"})

    alias_seed.seed_from_edgar_cache(companies_conn, cache_path=cache)

    assert "BP" not in _aliases(companies_conn, "XOM")
    assert "BP plc" in _aliases(companies_conn, "XOM")


def test_rerunning_inserts_nothing(companies_conn, tmp_path):
    cache = _fake_cache(tmp_path, SEC_TITLES)

    first = alias_seed.seed_from_edgar_cache(companies_conn, cache_path=cache)
    second = alias_seed.seed_from_edgar_cache(companies_conn, cache_path=cache)

    assert first["aliases_inserted"] > 0
    assert second["aliases_inserted"] == 0
    assert second["names_set"] == 0


# ---------------------------------------------------------------------------
# Never-raise contract (mirrors collectors/base.py)
# ---------------------------------------------------------------------------
def test_a_missing_cache_file_degrades_to_zero_counts_and_logs(companies_conn, tmp_path, capture_logs):
    result = alias_seed.seed_from_edgar_cache(companies_conn, cache_path=tmp_path / "absent.json")

    assert result == {"names_set": 0, "aliases_inserted": 0}
    assert any(r["event"] == "alias_seed_cache_unavailable" for r in capture_logs)


def test_an_unparseable_cache_file_degrades_to_zero_counts_and_logs(
    companies_conn, tmp_path, capture_logs
):
    path = tmp_path / "company_tickers.json"
    path.write_text("{not json", encoding="utf-8")

    result = alias_seed.seed_from_edgar_cache(companies_conn, cache_path=path)

    assert result == {"names_set": 0, "aliases_inserted": 0}
    assert any(r["event"] == "alias_seed_cache_unavailable" for r in capture_logs)


def test_a_ticker_absent_from_the_cache_is_skipped_without_raising(companies_conn, tmp_path):
    cache = _fake_cache(tmp_path, {"AAPL": "Apple Inc."})

    result = alias_seed.seed_from_edgar_cache(companies_conn, cache_path=cache)

    assert result["names_set"] == 1
    assert _aliases(companies_conn, "MSFT") == {}


# ---------------------------------------------------------------------------
# Against the REAL cached file — this is the one that matters
# ---------------------------------------------------------------------------
def test_every_real_ticker_gains_at_least_one_usable_alias(companies_conn):
    """`cache_path=None` resolves settings.CACHE_DIR/company_tickers.json — the real file."""
    alias_seed.seed_from_edgar_cache(companies_conn)

    for ticker in TICKERS:
        usable = [a for a in _aliases(companies_conn, ticker) if a.upper() != ticker]
        assert usable, f"{ticker} gained no non-ticker alias from the real EDGAR cache"
        assert _name(companies_conn, ticker) != ticker
