"""Tests for the SQLite store."""

from __future__ import annotations

from pathlib import Path

from remarkable_ea.store import SCHEMA_VERSION, connect


EXPECTED_TABLES = {"pages", "commitments", "meetings_proposed", "sync_state", "schema_version"}


def _tables(conn) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def test_schema_initializes(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    conn = connect(db)
    try:
        assert EXPECTED_TABLES.issubset(_tables(conn))
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()


def test_schema_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    connect(db).close()
    conn = connect(db)
    try:
        # Still exactly one schema_version row after reopening.
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_foreign_keys_cascade(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    conn = connect(db)
    try:
        conn.execute(
            "INSERT INTO pages(page_id, notebook, captured_date, summary, raw_json, extracted_at)"
            " VALUES ('p1', 'nb', '2026-04-12', 's', '{}', '2026-04-12T18:30:00')"
        )
        conn.execute(
            "INSERT INTO commitments(id, page_id, who, what, due, confidence, status, created_at)"
            " VALUES ('c1', 'p1', 'self', 'ship v1', NULL, 0.9, 'open', '2026-04-12T18:30:00')"
        )
        conn.commit()

        conn.execute("DELETE FROM pages WHERE page_id='p1'")
        conn.commit()

        remaining = conn.execute(
            "SELECT COUNT(*) FROM commitments WHERE id='c1'"
        ).fetchone()[0]
        assert remaining == 0
    finally:
        conn.close()
