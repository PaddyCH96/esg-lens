"""Seed `companies.name` and `company_aliases` from the cached SEC company_tickers.json (NLP-03).

WHY THIS EXISTS
---------------
The entity gate (`esg_lens.nlp.entity`) matches document text against a company's known aliases.
On the real database it had nothing to match: `yfinance` returns 404 for all three tickers, so
`company_aliases` had 0 rows and `companies.name` was literally 'AAPL' / 'XOM' / 'MSFT'. A gate
with no aliases either rejects every google_news document or is forced permanently open — both
are the Phase 1 failure mode (a pipeline that runs clean and finds nothing).

`data/cache/company_tickers.json` is already on disk, written by the EDGAR collector, and is
reliable: it yields 'Apple Inc.', 'MICROSOFT CORP' and 'ExxonMobil Holdings Corp'. That is enough
to give every ticker a real name and at least one usable alias.

WHAT THIS DELIBERATELY IS NOT
-----------------------------
It is not a repair of the yfinance collector — that is a Phase 1 defect tracked separately and is
explicitly out of scope. It is not an alias-enrichment feature. It reads one cached file, sets a
name when the name is still the bare ticker, and inserts two or three alias variants.

A NOTE ON CASE
--------------
The SEC titles are inconsistently cased ('Apple Inc.' but 'MICROSOFT CORP'). Nothing downstream
depends on their case: `entity.py` lowercases both sides of every comparison. Do not "tidy" the
titles into title case here — that would invent a name the SEC does not assert.

NEVER-RAISE CONTRACT
--------------------
Mirrors `collectors/base.py`: a missing or unparseable cache logs
``alias_seed_cache_unavailable`` and returns zero counts. Seeding is an optional improvement to
the corpus, never a reason for a pipeline run to die.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import structlog

from esg_lens.config import settings

log = structlog.get_logger(__name__)

CACHE_FILENAME = "company_tickers.json"

# Trailing corporate-form tokens stripped to produce the short "common" alias. Compared
# case-insensitively with surrounding punctuation removed, so 'Inc.' also covers 'INC' and 'inc,'.
_SUFFIX_TOKENS = frozenset(
    {
        "inc",
        "corp",
        "corporation",
        "company",
        "co",
        "plc",
        "holdings",
        "holding",
        "ltd",
        "lp",
        "llc",
        "the",
    }
)

# A one- or two-character "name" ('BP', 'GE') is a matching hazard, not an alias: it collides with
# ordinary words and initialisms inside headlines. The full legal title is still inserted.
MIN_VARIANT_CHARS = 3


def _normalise_token(token: str) -> str:
    return token.strip(".,;:()").lower()


def strip_corporate_suffixes(title: str) -> str:
    """Repeatedly drop trailing corporate-form tokens.

    Repeatedly, not once: 'ExxonMobil Holdings Corp' must reach 'ExxonMobil', and stopping after
    one strip leaves 'ExxonMobil Holdings' — which no headline ever says.
    """
    tokens = (title or "").split()
    while tokens and _normalise_token(tokens[-1]) in _SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens).strip(" ,.")


def _load_cache(cache_path: Path) -> dict[str, str] | None:
    """Return ``{TICKER: title}`` from the SEC file, or ``None`` if it cannot be used."""
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("alias_seed_cache_unavailable", path=str(cache_path), error=str(exc))
        return None

    # SEC shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    records = payload.values() if isinstance(payload, dict) else payload
    try:
        mapping = {
            str(rec["ticker"]).upper(): str(rec["title"])
            for rec in records
            if isinstance(rec, dict) and rec.get("ticker") and rec.get("title")
        }
    except (AttributeError, TypeError) as exc:
        log.warning("alias_seed_cache_unavailable", path=str(cache_path), error=str(exc))
        return None

    if not mapping:
        log.warning("alias_seed_cache_unavailable", path=str(cache_path), error="no ticker records")
        return None
    return mapping


def _alias_variants(ticker: str, title: str) -> list[tuple[str, str]]:
    """``[(alias, alias_type), ...]`` — the legal title, its short form, and the bare ticker.

    The bare ticker is inserted because a headline saying only "(MSFT)" is a real identity
    signal, but it is NOT counted as a usable alias by the entity gate: every document collected
    under a ticker trivially satisfies it, so counting it would make the fail-open check a no-op.
    """
    variants: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(alias: str, alias_type: str) -> None:
        alias = (alias or "").strip()
        key = alias.upper()
        if len(alias) < MIN_VARIANT_CHARS or key in seen:
            return
        if key == ticker.upper() and alias_type != "common":
            # The ticker is added once, as 'common', below.
            return
        seen.add(key)
        variants.append((alias, alias_type))

    add(title, "legal")
    add(strip_corporate_suffixes(title), "common")
    add(ticker, "common")
    return variants


def seed_from_edgar_cache(
    conn: sqlite3.Connection, cache_path: str | Path | None = None
) -> dict[str, int]:
    """Set missing company names and insert alias variants. Never raises.

    Returns ``{"names_set": n, "aliases_inserted": m}``. Both are 0 when the cache is unusable.
    """
    path = Path(cache_path) if cache_path else Path(settings.CACHE_DIR) / CACHE_FILENAME
    empty = {"names_set": 0, "aliases_inserted": 0}

    titles = _load_cache(path)
    if titles is None:
        return empty

    try:
        rows = conn.execute("SELECT ticker, name FROM companies").fetchall()
    except sqlite3.Error as exc:  # pragma: no cover - defensive, mirrors never-raise contract
        log.warning("alias_seed_query_failed", error=str(exc))
        return empty

    names_set = 0
    aliases_inserted = 0

    for row in rows:
        ticker = str(_value(row, "ticker", 0))
        current_name = _value(row, "name", 1)
        bound = log.bind(ticker=ticker)

        title = titles.get(ticker.upper())
        if not title:
            bound.info("alias_seed_ticker_absent_from_cache")
            continue

        try:
            # yfinance data, when it exists, wins: only a name still equal to the bare ticker
            # is a placeholder worth replacing.
            if (current_name or "").strip().upper() == ticker.upper():
                conn.execute(
                    "UPDATE companies SET name = ?, updated_at = datetime('now') WHERE ticker = ?",
                    (title, ticker),
                )
                names_set += 1
                bound.info("alias_seed_name_set", name=title)

            for alias, alias_type in _alias_variants(ticker, title):
                cur = conn.execute(
                    "INSERT OR IGNORE INTO company_aliases (ticker, alias, alias_type) "
                    "VALUES (?, ?, ?)",
                    (ticker, alias, alias_type),
                )
                if cur.rowcount:
                    aliases_inserted += 1
                    bound.info("alias_seed_alias_inserted", alias=alias, alias_type=alias_type)
        except sqlite3.Error as exc:
            bound.warning("alias_seed_write_failed", error=str(exc))

    try:
        conn.commit()
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        log.warning("alias_seed_commit_failed", error=str(exc))

    log.info("alias_seed_complete", names_set=names_set, aliases_inserted=aliases_inserted)
    return {"names_set": names_set, "aliases_inserted": aliases_inserted}


def _value(row: Any, name: str, index: int) -> Any:
    """Read from a sqlite3.Row or a plain tuple, indifferently."""
    try:
        return row[name]
    except (IndexError, KeyError, TypeError):
        return row[index]
