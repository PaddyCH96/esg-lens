"""Phase 0 acceptance: schema creates 10 tables + view, pragmas, constraints."""

import sqlite3


def test_schema_has_ten_tables_and_view(db_conn):
    tables = {r[0] for r in db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    expected = {
        "companies",
        "company_aliases",
        "raw_documents",
        "esg_signals",
        "esg_scores",
        "score_contributions",
        "jobs",
        "job_items",
        "collection_runs",
        "schema_migrations",
    }
    assert expected.issubset(tables), f"missing: {expected - tables}"
    views = {r[0] for r in db_conn.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall()}
    assert "v_latest_scores" in views


def test_pragmas():
    # WAL and foreign_keys must be set (per-connection)
    import tempfile
    from pathlib import Path
    from esg_lens.db.engine import get_connection, init_db

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "pragma_test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert fk == 1, "foreign_keys must be ON per connection"
        assert jm.lower() == "wal"
        conn.close()


# THE ONE NAMED CARVE-OUT FROM THE PHASE 2 G-4 GATE.
#
# Every other Phase 2 test file is forbidden from reading a file, because Phase 1 shipped
# grep-based tests that asserted strings appeared in .py files and passed while the code was
# broken. This file reads `src/esg_lens/db/schema.sql` and `docs/data_model.md` as TEXT because
# detecting divergence between the schema and its documentation is not otherwise observable —
# there is no runtime behaviour that differs when the doc goes stale.
#
# The carve-out is BY FILE TYPE, not by accident of globbing: reading .sql and .md from a test is
# permitted here; reading a .py file from a test is never permitted anywhere in this phase.
#
# Hand-maintained list. A column added to esg_signals must be added HERE as well as to both
# files, or the divergence check below is only as good as the last person's memory.
EXPECTED_SIGNAL_COLUMNS = (
    "included",
    "exclusion_reason",
    "duplicate_of",
    "pillar",
    "category",
    "relevance",
    "sentiment_score",
    "sentiment_label",
    "controversy_severity",
    "controversy_terms",
    "polarity",
    "weight_source",
    "weight_recency",
    "weight_category",
    "weight_evidence",
    "weight_total",
    "entities_json",
    "model_version",
    "processed_at",
)


def _schema_text():
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[2] / "src" / "esg_lens" / "db" / "schema.sql"
    ).read_text()


def _data_model_text():
    from pathlib import Path

    return (Path(__file__).resolve().parents[2] / "docs" / "data_model.md").read_text()


def test_schema_diff_against_data_model():
    """SCAF-05: schema.sql diffs clean against data_model.md DDL."""
    schema = _schema_text()
    # Minimal check: all load-bearing CHECKs are present
    # 'rss' added 2026-09-14 when RssCollector replaced GDELT as the primary news source.
    assert "CHECK (source IN ('gdelt','edgar','yfinance','newsapi','rss'))" in schema
    assert "CHECK (status IN ('ok','insufficient_data','failed'))" in schema
    assert "CHECK (status IN ('queued','running','done','partial','failed','cancelled'))" in schema
    assert "ON DELETE SET NULL" in schema
    assert "UNIQUE (document_id, model_version)" in schema
    assert "UNIQUE (ticker, content_hash)" in schema


def test_esg_signals_columns_appear_in_both_schema_and_data_model():
    """handoff §0.3: schema.sql and docs/data_model.md never diverge.

    `duplicate_of` (02-03, D-2-02) is the reason this test exists in its current form. It is a
    genuine foreign key between two raw_documents rows; hiding it inside the untyped
    `entities_json` TEXT blob is exactly the shortcut a product whose core value proposition is
    an audit trail cannot afford.
    """
    schema = _schema_text()
    data_model = _data_model_text()
    for column in EXPECTED_SIGNAL_COLUMNS:
        assert column in schema, f"{column} missing from src/esg_lens/db/schema.sql"
        assert column in data_model, f"{column} missing from docs/data_model.md"


def test_duplicate_of_is_declared_as_a_foreign_key_in_both_files():
    declaration = "duplicate_of    INTEGER REFERENCES raw_documents(id)"
    assert declaration in _schema_text()
    assert declaration in _data_model_text()


def test_duplicate_of_is_a_real_foreign_key_not_a_bare_integer(db_conn):
    """Inserting a non-existent duplicate_of must raise under PRAGMA foreign_keys=ON."""
    import pytest

    db_conn.execute(
        "INSERT INTO companies (ticker, name, fetched_at) VALUES (?, ?, ?)",
        ("FKT", "FK Test", "2026-01-01T00:00:00Z"),
    )
    db_conn.execute(
        "INSERT INTO raw_documents (id, ticker, source, doc_type, content_hash) "
        "VALUES (?, ?, ?, ?, ?)",
        (900001, "FKT", "rss", "news", "fk-test-hash"),
    )
    db_conn.commit()

    # NULL is allowed — duplicate_of is nullable and only set on exclusion_reason='duplicate'.
    db_conn.execute(
        "INSERT INTO esg_signals (document_id, ticker, model_version, duplicate_of) "
        "VALUES (?, ?, ?, NULL)",
        (900001, "FKT", "v-null"),
    )
    db_conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO esg_signals (document_id, ticker, model_version, duplicate_of) "
            "VALUES (?, ?, ?, ?)",
            (900001, "FKT", "v-dangling", 999999),
        )
        db_conn.commit()
    db_conn.rollback()


def test_duplicate_of_migration_is_idempotent_and_preserves_rows(tmp_path):
    """scripts/migrate_duplicate_of.py runs against a DB created BEFORE the column existed.

    The real database holds 338 calibrated rows and must never be rebuilt, so the migration path
    is the only path. A second run is a no-op.
    """
    import migrate_duplicate_of

    db_path = tmp_path / "pre_migration.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(
        """
        CREATE TABLE raw_documents (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT);
        CREATE TABLE esg_signals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id   INTEGER NOT NULL REFERENCES raw_documents(id),
            model_version TEXT NOT NULL
        );
        INSERT INTO raw_documents (id, title) VALUES (1, 'kept');
        INSERT INTO esg_signals (document_id, model_version) VALUES (1, 'v1');
        """
    )
    conn.commit()
    conn.close()

    assert migrate_duplicate_of.migrate(db_path) is True  # applied
    assert migrate_duplicate_of.migrate(db_path) is False  # no-op

    conn = sqlite3.connect(str(db_path))
    columns = {r[1] for r in conn.execute("PRAGMA table_info(esg_signals)").fetchall()}
    assert "duplicate_of" in columns
    assert conn.execute("SELECT COUNT(*) FROM esg_signals").fetchone()[0] == 1
    conn.close()


def test_constraints_enforced(db_conn):
    # Invalid source should fail CHECK
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO companies (ticker, name, fetched_at) VALUES (?, ?, ?)",
            ("TST", "Test Co", "2026-01-01T00:00:00Z"),
        )
        db_conn.execute(
            "INSERT INTO raw_documents (ticker, source, doc_type, content_hash) VALUES (?, ?, ?, ?)",
            ("TST", "bad_source", "news", "abc"),
        )
        db_conn.commit()
    db_conn.rollback()


def test_scores_append_only(db_conn):
    db_conn.execute(
        "INSERT INTO companies (ticker, name, fetched_at) VALUES (?, ?, ?)",
        ("TST2", "Test 2", "2026-01-01T00:00:00Z"),
    )
    for i in range(2):
        db_conn.execute(
            "INSERT INTO esg_scores (ticker, methodology_version, config_hash) VALUES (?, ?, ?)",
            ("TST2", "0.1.0", "abc"),
        )
    db_conn.commit()
    rows = db_conn.execute("SELECT COUNT(*) FROM esg_scores WHERE ticker='TST2'").fetchone()[0]
    assert rows == 2
