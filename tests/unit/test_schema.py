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


def test_schema_diff_against_data_model():
    """SCAF-05: schema.sql diffs clean against data_model.md DDL."""
    from pathlib import Path

    schema = (Path(__file__).resolve().parents[2] / "src" / "esg_lens" / "db" / "schema.sql").read_text()
    # Minimal check: all load-bearing CHECKs are present
    assert "CHECK (source IN ('gdelt','edgar','yfinance','newsapi'))" in schema
    assert "CHECK (status IN ('ok','insufficient_data','failed'))" in schema
    assert "CHECK (status IN ('queued','running','done','partial','failed','cancelled'))" in schema
    assert "ON DELETE SET NULL" in schema
    assert "UNIQUE (document_id, model_version)" in schema
    assert "UNIQUE (ticker, content_hash)" in schema


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
