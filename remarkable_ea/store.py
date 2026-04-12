"""SQLite store for extracted pages, commitments, and meeting proposals.

The store is a cache, not a source of truth. It can be fully rebuilt from the
page PNGs on disk plus the notebook whitelist in ``config.yaml``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS pages (
    page_id       TEXT PRIMARY KEY,
    notebook      TEXT NOT NULL,
    captured_date TEXT NOT NULL,   -- YYYY-MM-DD
    summary       TEXT NOT NULL,
    raw_json      TEXT NOT NULL,   -- full extractor response, source of truth for reprocessing
    extracted_at  TEXT NOT NULL    -- ISO8601
);

CREATE INDEX IF NOT EXISTS idx_pages_captured_date ON pages(captured_date);
CREATE INDEX IF NOT EXISTS idx_pages_notebook      ON pages(notebook);

CREATE TABLE IF NOT EXISTS commitments (
    id            TEXT PRIMARY KEY,
    page_id       TEXT NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    who           TEXT NOT NULL,  -- 'self' or a person name
    what          TEXT NOT NULL,
    due           TEXT,           -- YYYY-MM-DD or NULL
    confidence    REAL NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('open','closed','cancelled','snoozed')),
    created_at    TEXT NOT NULL,
    closed_at     TEXT,
    closed_note   TEXT,
    snoozed_until TEXT
);

CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status);
CREATE INDEX IF NOT EXISTS idx_commitments_due    ON commitments(due);
CREATE INDEX IF NOT EXISTS idx_commitments_page   ON commitments(page_id);

CREATE TABLE IF NOT EXISTS meetings_proposed (
    id               TEXT PRIMARY KEY,
    page_id          TEXT NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    with_whom        TEXT NOT NULL,  -- 'with' is a SQL keyword; store under with_whom
    topic            TEXT NOT NULL,
    proposed_when    TEXT,           -- free text, e.g. 'next Tuesday afternoon'
    duration_minutes INTEGER,
    status           TEXT NOT NULL CHECK (status IN ('pending','drafted','sent','dismissed')),
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings_proposed(status);
CREATE INDEX IF NOT EXISTS idx_meetings_page   ON meetings_proposed(page_id);

CREATE TABLE IF NOT EXISTS sync_state (
    notebook_id    TEXT PRIMARY KEY,
    last_synced_at TEXT NOT NULL    -- ISO8601
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection to the store, creating & migrating the schema as needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
        )
        conn.commit()
        return
    current = int(row["version"])
    if current == SCHEMA_VERSION:
        return
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Store schema version {current} is newer than supported {SCHEMA_VERSION}"
        )
    # Future migrations will live here, each guarded by the current version.
    raise RuntimeError(f"No migration path from schema version {current}")
