"""Export the Phase 2 NLP test fixture from the live corpus in ``data/esg_lens.db``.

The fixture is EXPORTED, never invented. Every record is a real row collected from RSS or
EDGAR, and every record carries its ``raw_documents.id`` so a failing test can be traced back
to the document that produced it.

WHAT IS SELECTED
----------------
1. A ticker-balanced **stride slice**: 25 news records per ticker (AAPL, MSFT, XOM) taken by
   ``rows[::max(1, len(rows) // 25)][:25]`` over ``ORDER BY id``.

   The stride is not decoration. This corpus is ordered such that controversy headlines cluster
   in the high-id tail, so a plain ``ORDER BY id LIMIT 25`` measures ~9% controversy density
   where the stride slice measures ~32%. Measured on the live DB on 2026-09-14 with the
   ``lex@2026-09b`` triggers matched as plain substrings (a lower bound — lemma matching can
   only find more):

       stride slice, 75 records
           trigger hits against the title AS STORED   7  (9.3%)
           trigger hits against title.lower()        24  (32.0%)
           ratio                                          3.4x

   02-03's differential Title-Case regression test is calibrated against exactly these counts
   (its floors are ``lower >= 2.0 * stored`` and ``lower >= 0.25``). If you change the slice,
   you are invalidating that test — re-measure and update both.

2. A **landmark union** on top of the stride slice. Each landmark is looked up by a
   parameterised ``LIKE`` against the live DB; none is hard-coded as data. If ANY landmark
   matches zero rows the script raises :class:`FixtureLandmarkMissing` naming EVERY missing
   landmark and exits non-zero WITHOUT writing the file.

   That loud failure is the entire point. RSS returns different articles on every collection,
   and a corpus refresh that silently dropped a landmark would leave a fixture that still
   passes while proving nothing. This has already happened once: the headline
   "Lawsuit Against Apple To Remain In State Court" was quoted as a landmark in 02-CONTEXT.md
   D-2-01, the 2026-09-13 AAPL corpus was destroyed by a smoke test, and re-collection on
   09-14 returned different articles. That title now matches 0 rows and is deliberately NOT a
   landmark here.

DETERMINISM
-----------
``build_records(conn)`` performs all selection, landmark union and ordering and returns plain
data sorted by ``(ticker, id)``. ``main()`` only opens the DB, calls ``build_records`` and
writes ``json.dump(records, f, sort_keys=True, indent=2)`` — no timestamps, no run ids, no set
iteration leaking into the output. That split is what lets the test prove determinism by
calling ``build_records`` twice in-process and comparing canonical JSON, which it must do
because the Phase 2 G-4 test-quality gate forbids a test from reading files.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "esg_lens.db"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "nlp_corpus.json"

TICKERS = ("AAPL", "MSFT", "XOM")
NEWS_PER_TICKER = 25

RECORD_FIELDS = (
    "id",
    "ticker",
    "doc_type",
    "title",
    "body",
    "domain",
    "published_at",
    "source",
    "raw_json",
)

# (landmark name, ticker, LIKE pattern) — every one verified present on 2026-09-14.
# Names are stable identifiers: tests refer to landmarks by name, never by title text.
NEWS_LANDMARKS: tuple[tuple[str, str, str], ...] = (
    # -- The MSFT near-duplicate clusters (D-2-02). Law firms syndicate the same deadline
    #    alert across prnewswire/businesswire/globenewswire under slightly different titles,
    #    so content_hash dedup cannot catch them by design.
    #    Each of these patterns is scoped to ONE cluster: tests assert that all rows sharing a
    #    landmark name land in the same cluster, so a pattern that spans two distinct clusters
    #    would make that assertion false. `MSFT Shareholder Alert%` unscoped, for example, also
    #    catches a short globenewswire deadline stub that does not cluster with the rest.
    ("msft_shareholder_alert_class_action_group", "MSFT", "MSFT Shareholder Alert%Class Action%"),
    (
        "msft_investors_lead_plain_group",
        "MSFT",
        "%Investors Have Opportunity to Lead Microsoft Corporation Securities Fraud Lawsuit",
    ),
    (
        "msft_investors_lead_schall_group",
        "MSFT",
        "%Securities Fraud Lawsuit with the Schall Law Firm",
    ),
    ("msft_lost_money_join_group", "MSFT", "Lost Money on Microsoft Corporation%"),
    ("msft_deadline_alert_group", "MSFT", "%Deadline Alert%Reminds Microsoft Corporation%"),
    (
        "msft_shareholders_lost_money_group",
        "MSFT",
        "Microsoft Corporation (MSFT) Shareholders Who Lost Money Have Opportunity%",
    ),
    (
        "msft_deadline_alert_shareholders_group",
        "MSFT",
        "Deadline Alert: Microsoft Corporation (MSFT) Shareholders%",
    ),
    (
        "msft_kahn_swick_foti_pair",
        "MSFT",
        "Microsoft Investigation Initiated: Kahn Swick & Foti%",
    ),
    # -- The pair that must NOT collapse. Two genuinely distinct Microsoft lawsuits. Measured
    #    Jaccard varies 0.41-0.50 depending on the tokenizer, so tests assert only that these
    #    two land in DIFFERENT clusters and never assert a similarity value.
    ("msft_wisconsin_data_center_suit", "MSFT", "%class-action lawsuit in Wisconsin over data center%"),
    (
        "msft_copilot_noise_class_actions",
        "MSFT",
        "%Faces Two New Class Actions Over Copilot And Data Center Noise%",
    ),
    # -- Title Case controversy exemplars (Pitfall 1). All of these MISS when the lexicon is
    #    matched against the title as stored and HIT when matched lowercased.
    ("msft_shareholder_lawsuit_td_cowen", "MSFT", "Microsoft (MSFT) Faces Shareholder Lawsuit; TD Cowen%"),
    (
        "aapl_icloud_uk_lawsuit",
        "AAPL",
        # Two severity tiers in one headline: `lawsuit` (tier 2) and `class action` (tier 3).
        "Apple (AAPL) Faces%UK iCloud Lawsuit As Class Action Moves Ahead",
    ),
    ("aapl_ai_trade_secret_injunction", "AAPL", "Apple (AAPL) Seeks Injunction In AI Trade Secret Lawsuit"),
    ("aapl_att_uk_lawsuit", "AAPL", "Apple (AAPL) Draws New%UK ATT Lawsuit With Wider Europe Stakes"),
    # -- The Pitfall 2 negation-guard regression case: bare "dropped" used to fire here and
    #    silently demote a tier-3 class action on a price-movement headline.
    ("msft_stock_drop_copilot", "MSFT", "MSFT Stock Drop: Microsoft Copilot Functionality Issues%"),
    # -- Positive FINANCIAL sentiment on a negative ESG event (research_notes.md §3 caveat).
    ("msft_carbon_emissions_rose", "MSFT", "Microsoft carbon emissions rose 25%"),
)

# Filing sections with real, long bodies. Both exceed 512 tokens (8000 characters) so the
# truncation path in registry's pipelines and the sentence cap in clean.split_sentences are
# both exercised against real EDGAR text, and both contain "Table of Contents" page furniture
# for strip_boilerplate to remove.
FILING_LANDMARKS: tuple[tuple[str, str], ...] = (
    ("msft_long_filing_section", "MSFT"),
    ("aapl_long_filing_section", "AAPL"),
)
FILING_MIN_BODY_CHARS = 4000


class FixtureLandmarkMissing(RuntimeError):
    """Raised when one or more landmark lookups match zero rows in the live corpus.

    Lists EVERY missing landmark, not just the first — a corpus refresh usually loses several
    at once, and reporting them one run at a time turns a five-minute fix into an afternoon.
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(missing)
        detail = "\n".join(f"  - {name}" for name in self.missing)
        super().__init__(
            f"{len(self.missing)} fixture landmark(s) matched zero rows in {DB_PATH}:\n{detail}\n"
            "The corpus has been refreshed or re-collected. Re-verify each landmark against the "
            "live DB and update scripts/export_nlp_fixtures.py — do NOT relax the check."
        )


def _row_to_record(row: sqlite3.Row) -> dict:
    return {field: row[field] for field in RECORD_FIELDS}


def _stride_ids(conn: sqlite3.Connection) -> dict[str, list[int]]:
    """25 news ids per ticker by fixed stride over ``ORDER BY id``. See the module docstring."""
    out: dict[str, list[int]] = {}
    for ticker in TICKERS:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM raw_documents WHERE ticker = ? AND doc_type = 'news' ORDER BY id",
                (ticker,),
            )
        ]
        step = max(1, len(ids) // NEWS_PER_TICKER)
        out[ticker] = ids[::step][:NEWS_PER_TICKER]
    return out


def _landmark_ids(conn: sqlite3.Connection) -> tuple[dict[int, list[str]], list[str]]:
    """Resolve every landmark to concrete row ids. Returns (id -> landmark names, missing)."""
    by_id: dict[int, list[str]] = {}
    missing: list[str] = []

    for name, ticker, pattern in NEWS_LANDMARKS:
        rows = conn.execute(
            "SELECT id FROM raw_documents "
            "WHERE ticker = ? AND doc_type = 'news' AND title LIKE ? ORDER BY id",
            (ticker, pattern),
        ).fetchall()
        if not rows:
            missing.append(f"{name} [{ticker}] LIKE {pattern!r}")
            continue
        for row in rows:
            by_id.setdefault(row["id"], []).append(name)

    for name, ticker in FILING_LANDMARKS:
        rows = conn.execute(
            "SELECT id FROM raw_documents "
            "WHERE ticker = ? AND doc_type = 'filing_section' "
            "AND body LIKE ? AND LENGTH(body) >= ? ORDER BY id LIMIT 1",
            (ticker, "%Table of Contents%", FILING_MIN_BODY_CHARS),
        ).fetchall()
        if not rows:
            missing.append(f"{name} [{ticker}] filing_section >= {FILING_MIN_BODY_CHARS} chars")
            continue
        by_id.setdefault(rows[0]["id"], []).append(name)

    return by_id, missing


def build_records(conn: sqlite3.Connection) -> list[dict]:
    """Pure selection + ordering. Deterministic: same connection in, byte-identical JSON out.

    Raises :class:`FixtureLandmarkMissing` if any landmark has vanished from the corpus.
    """
    conn.row_factory = sqlite3.Row

    stride = _stride_ids(conn)
    landmarks, missing = _landmark_ids(conn)
    if missing:
        raise FixtureLandmarkMissing(missing)

    stride_ids = {doc_id for ids in stride.values() for doc_id in ids}
    wanted = sorted(stride_ids | set(landmarks))

    records: list[dict] = []
    for doc_id in wanted:
        row = conn.execute(
            f"SELECT {', '.join(RECORD_FIELDS)} FROM raw_documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:  # pragma: no cover - ids came from this same DB
            continue
        record = _row_to_record(row)
        record["is_stride"] = doc_id in stride_ids
        # Sorted so the serialised list is order-stable regardless of dict/set iteration.
        record["landmarks"] = sorted(landmarks.get(doc_id, []))
        records.append(record)

    records.sort(key=lambda r: (r["ticker"], r["id"]))
    return records


def main() -> int:
    if not DB_PATH.exists():
        print(f"corpus DB not found: {DB_PATH}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(DB_PATH))
    try:
        records = build_records(conn)
    except FixtureLandmarkMissing as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        conn.close()

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FIXTURE_PATH.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, sort_keys=True, indent=2)

    news = [r for r in records if r["doc_type"] == "news"]
    stride_news = [r for r in news if r["is_stride"]]
    print(
        f"wrote {FIXTURE_PATH.relative_to(PROJECT_ROOT)}: {len(records)} records "
        f"({len(stride_news)} stride news, {len(news) - len(stride_news)} landmark-only news, "
        f"{len(records) - len(news)} filing sections)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
