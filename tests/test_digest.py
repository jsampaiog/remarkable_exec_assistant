"""Tests for the digest module (sections 1–3).

All tests use dry_run=True and pre-populated stores — no SMTP, no secrets.
"""

from __future__ import annotations

from pathlib import Path

from remarkable_ea.config import (
    ClaudeConfig,
    Config,
    EmailConfig,
    NotebookConfig,
    RemarkableConfig,
    StorageConfig,
)
from remarkable_ea.digest import (
    DigestNumbering,
    _aging_commitments,
    _build_body,
    _today_commitments,
    _today_pages,
    run,
)
from remarkable_ea.store import connect


TODAY = "2026-04-27"


def _make_config(tmp_path: Path) -> Config:
    return Config(
        remarkable=RemarkableConfig(rmapi_path="rmapi", notebooks=()),
        claude=ClaudeConfig(model="claude-sonnet-4-6"),
        email=EmailConfig(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            imap_host="imap.gmail.com",
            imap_port=993,
            from_addr="a@b.com",
            to_addr="c@d.com",
        ),
        storage=StorageConfig(
            db_path=tmp_path / "state.db",
            pages_dir=tmp_path / "pages",
        ),
    )


def _seed_db(conn, *, pages=None, commitments=None):
    for p in pages or []:
        conn.execute(
            "INSERT INTO pages(page_id, notebook, captured_date, summary, raw_json, extracted_at)"
            " VALUES (?, ?, ?, ?, '{}', '2026-04-27T18:30:00')",
            (p["page_id"], p["notebook"], p["captured_date"], p["summary"]),
        )
    for c in commitments or []:
        conn.execute(
            "INSERT INTO commitments"
            "(id, page_id, who, what, due, confidence, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                c["id"],
                c["page_id"],
                c["who"],
                c["what"],
                c.get("due"),
                c.get("confidence", 0.9),
                c.get("status", "open"),
                c.get("created_at", "2026-04-27T18:30:00+00:00"),
            ),
        )
    conn.commit()


# ---------- query helpers ----------


def test_today_pages(tmp_path: Path) -> None:
    conn = connect(tmp_path / "state.db")
    _seed_db(
        conn,
        pages=[
            {"page_id": "p1", "notebook": "Daily", "captured_date": TODAY, "summary": "Summary 1"},
            {"page_id": "p2", "notebook": "Meetings", "captured_date": TODAY, "summary": "Summary 2"},
            {"page_id": "old", "notebook": "Daily", "captured_date": "2026-04-20", "summary": "Old"},
        ],
    )
    pages = _today_pages(conn, TODAY)
    conn.close()

    assert len(pages) == 2
    assert pages[0]["page_id"] == "p1"
    assert pages[1]["page_id"] == "p2"


def test_today_commitments(tmp_path: Path) -> None:
    conn = connect(tmp_path / "state.db")
    _seed_db(
        conn,
        pages=[
            {"page_id": "p1", "notebook": "Daily", "captured_date": TODAY, "summary": "S1"},
            {"page_id": "p0", "notebook": "Daily", "captured_date": "2026-04-20", "summary": "Old"},
        ],
        commitments=[
            {"id": "c1", "page_id": "p1", "who": "self", "what": "Do X", "due": "2026-05-01"},
            {"id": "c2", "page_id": "p1", "who": "Pedro", "what": "Do Y"},
            {"id": "c-old", "page_id": "p0", "who": "self", "what": "Old task"},
            {"id": "c-closed", "page_id": "p1", "who": "self", "what": "Done", "status": "closed"},
        ],
    )
    comms = _today_commitments(conn, TODAY)
    conn.close()

    ids = [c["id"] for c in comms]
    assert "c1" in ids
    assert "c2" in ids
    assert "c-old" not in ids  # different page date
    assert "c-closed" not in ids  # closed


def test_aging_commitments_past_due(tmp_path: Path) -> None:
    conn = connect(tmp_path / "state.db")
    _seed_db(
        conn,
        pages=[{"page_id": "p1", "notebook": "D", "captured_date": "2026-04-15", "summary": "S"}],
        commitments=[
            {"id": "past-due", "page_id": "p1", "who": "self", "what": "Overdue",
             "due": "2026-04-20"},
            {"id": "not-yet", "page_id": "p1", "who": "self", "what": "Future",
             "due": "2026-05-10"},
        ],
    )
    aging = _aging_commitments(conn, TODAY)
    conn.close()

    assert len(aging) == 1
    assert aging[0]["id"] == "past-due"


def test_aging_commitments_no_due_date_old(tmp_path: Path) -> None:
    conn = connect(tmp_path / "state.db")
    _seed_db(
        conn,
        pages=[{"page_id": "p1", "notebook": "D", "captured_date": "2026-04-10", "summary": "S"}],
        commitments=[
            {"id": "old-open", "page_id": "p1", "who": "self", "what": "Stale",
             "created_at": "2026-04-15T10:00:00+00:00"},  # 12 days ago
            {"id": "recent-open", "page_id": "p1", "who": "self", "what": "Fresh",
             "created_at": "2026-04-25T10:00:00+00:00"},  # 2 days ago
        ],
    )
    aging = _aging_commitments(conn, TODAY)
    conn.close()

    ids = [c["id"] for c in aging]
    assert "old-open" in ids
    assert "recent-open" not in ids


# ---------- numbering ----------


def test_numbering_is_sequential() -> None:
    n = DigestNumbering()
    assert n.next("c1") == 1
    assert n.next("c2") == 2
    assert n.next("c3") == 3
    assert n.mapping == {1: "c1", 2: "c2", 3: "c3"}


# ---------- body building ----------


def test_build_body_all_sections() -> None:
    pages = [
        {"page_id": "p1", "notebook": "Daily", "summary": "Did some work."},
    ]
    comms = [
        {"id": "c1", "who": "self", "what": "Review dashboard", "due": "2026-05-01", "confidence": 0.9},
        {"id": "c2", "who": "Pedro", "what": "Send proposal", "due": None, "confidence": 0.8},
    ]
    aging = [
        {"id": "c-old", "who": "self", "what": "Old task", "due": "2026-04-20",
         "confidence": 0.9, "created_at": "2026-04-10T10:00:00"},
    ]
    numbering = DigestNumbering()
    body = _build_body(TODAY, pages, comms, aging, numbering)

    assert "TODAY'S PAGES" in body
    assert "p1" in body
    assert "Did some work." in body
    assert "NEW COMMITMENTS" in body
    assert "#1 Review dashboard (due: 2026-05-01)" in body
    assert "You (J)" in body
    assert "#2 Send proposal" in body
    assert "Pedro:" in body
    assert "AGING COMMITMENTS" in body
    assert "#3" in body
    assert "past due" in body
    assert "close 3, 7" in body


def test_build_body_empty_state() -> None:
    numbering = DigestNumbering()
    body = _build_body(TODAY, [], [], [], numbering)
    assert "(no new pages today)" in body
    assert "(none)" in body


# ---------- full run (dry_run) ----------


def test_run_dry_run(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    conn = connect(cfg.storage.db_path)
    _seed_db(
        conn,
        pages=[
            {"page_id": "p1", "notebook": "Daily", "captured_date": TODAY, "summary": "Did X."},
        ],
        commitments=[
            {"id": "c1", "page_id": "p1", "who": "self", "what": "Do Y", "due": "2026-05-01"},
        ],
    )
    conn.close()

    body, mapping = run(cfg, today=TODAY, dry_run=True)

    assert "Did X." in body
    assert "#1 Do Y" in body
    assert mapping[1] == "c1"


def test_run_numbering_spans_sections(tmp_path: Path) -> None:
    """Numbers assigned to new commitments and aging commitments don't overlap."""
    cfg = _make_config(tmp_path)
    conn = connect(cfg.storage.db_path)
    _seed_db(
        conn,
        pages=[
            {"page_id": "p-today", "notebook": "D", "captured_date": TODAY, "summary": "S"},
            {"page_id": "p-old", "notebook": "D", "captured_date": "2026-04-10", "summary": "S"},
        ],
        commitments=[
            {"id": "new-1", "page_id": "p-today", "who": "self", "what": "New A"},
            {"id": "new-2", "page_id": "p-today", "who": "self", "what": "New B"},
            {"id": "aging-1", "page_id": "p-old", "who": "self", "what": "Old C",
             "due": "2026-04-20"},
        ],
    )
    conn.close()

    body, mapping = run(cfg, today=TODAY, dry_run=True)

    assert mapping == {1: "new-1", 2: "new-2", 3: "aging-1"}
    assert "#1 New A" in body
    assert "#2 New B" in body
    assert "#3" in body
    assert "Old C" in body
