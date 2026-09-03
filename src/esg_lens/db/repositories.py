"""Minimal repository helpers — raw SQL, parameterised, no ORM."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


class CompanyRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, ticker: str, name: str, **fields: Any) -> None:
        cols = ["ticker", "name", "fetched_at"] + list(fields.keys())
        vals = [ticker.upper(), name, fields.get("fetched_at", "")] + list(fields.values())
        placeholders = ",".join("?" for _ in cols)
        # SQLite UPSERT
        set_clause = ",".join(f"{c}=excluded.{c}" for c in cols if c != "ticker")
        sql = f"INSERT INTO companies ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(ticker) DO UPDATE SET {set_clause}"
        self.conn.execute(sql, vals)
        self.conn.commit()

    def get(self, ticker: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM companies WHERE ticker = ?", (ticker.upper(),))
        return cur.fetchone()


class DocumentRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, **doc: Any) -> int | None:
        try:
            cols = list(doc.keys())
            placeholders = ",".join("?" for _ in cols)
            sql = f"INSERT OR IGNORE INTO raw_documents ({','.join(cols)}) VALUES ({placeholders})"
            cur = self.conn.execute(sql, list(doc.values()))
            self.conn.commit()
            return cur.lastrowid if cur.rowcount else None
        except sqlite3.IntegrityError:
            return None
