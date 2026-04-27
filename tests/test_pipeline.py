"""Tests for the sync → extract → store pipeline (step 4).

Exercises the full wiring: sync writes PNGs, extract reads them and calls
Claude (mocked), save_extraction persists the structured result to SQLite,
and a second run is a no-op because both sync and extract skip existing work.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from remarkable_ea import extract, sync
from remarkable_ea.config import (
    ClaudeConfig,
    Config,
    EmailConfig,
    NotebookConfig,
    RemarkableConfig,
    StorageConfig,
)
from remarkable_ea.extract import (
    Commitment,
    MeetingToSchedule,
    PageExtraction,
    save_extraction,
)
from remarkable_ea.store import connect


# ---------- fakes (reused from test_sync) ----------


@dataclass
class FakeRmapi:
    archive_builder: Any
    calls: list = field(default_factory=list)

    def download(self, remote_path: str, dest_dir: Path) -> Path:
        self.calls.append((remote_path, dest_dir))
        return self.archive_builder(remote_path, dest_dir)


@dataclass
class FakeRmc:
    calls: list = field(default_factory=list)

    def convert(self, rm_file: Path, dest_png: Path) -> None:
        self.calls.append((rm_file, dest_png))
        dest_png.parent.mkdir(parents=True, exist_ok=True)
        dest_png.write_bytes(b"\x89PNG-fake")


# ---------- helpers ----------


def _make_archive(
    dest_dir: Path, notebook_uuid: str, pages: list[tuple[str, int]]
) -> Path:
    archive_path = dest_dir / f"{notebook_uuid}.rmdoc"
    with zipfile.ZipFile(archive_path, "w") as zf:
        for page_uuid, last_modified_ms in pages:
            zf.writestr(f"{notebook_uuid}/{page_uuid}.rm", b"fake-rm")
            zf.writestr(
                f"{notebook_uuid}/{page_uuid}-metadata.json",
                json.dumps({"lastModified": str(last_modified_ms)}),
            )
    return archive_path


def _make_config(tmp_path: Path, notebooks: list[NotebookConfig]) -> Config:
    return Config(
        remarkable=RemarkableConfig(rmapi_path="rmapi", notebooks=tuple(notebooks)),
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


def _make_canned_response(page_id: str, captured_date: str) -> dict:
    return {
        "page_id": page_id,
        "notebook": "Daily",
        "captured_date": captured_date,
        "summary": f"Summary for {page_id}.",
        "commitments": [
            {
                "id": f"{page_id}-c1",
                "who": "self",
                "what": "Review dashboard",
                "due": "2026-05-01",
                "confidence": 0.9,
            },
            {
                "id": f"{page_id}-c2",
                "who": "Pedro",
                "what": "Send proposal",
                "due": "2026-04-28",
                "confidence": 0.95,
            },
        ],
        "meetings_to_schedule": [
            {
                "with": "design team",
                "topic": "follow-up",
                "proposed_when": "next Tuesday",
                "duration_minutes": 30,
            }
        ],
        "questions_raised": ["What is the budget?"],
        "people_mentioned": ["Pedro"],
        "references_past": None,
    }


def _fake_claude_client(*canned_responses: dict) -> MagicMock:
    """Return a mock client that returns the given responses in sequence."""
    client = MagicMock()
    side_effects = []
    for resp in canned_responses:
        text_block = MagicMock()
        text_block.text = json.dumps(resp)
        response = MagicMock()
        response.content = [text_block]
        side_effects.append(response)
    client.messages.create.side_effect = side_effects
    return client


# ---------- save_extraction ----------


def test_save_extraction_inserts_all_related_rows(tmp_path: Path) -> None:
    conn = connect(tmp_path / "state.db")
    try:
        ext = PageExtraction(
            page_id="p1",
            notebook="Daily",
            captured_date="2026-04-25",
            summary="A test page.",
            commitments=[
                Commitment(id="c1", who="self", what="do X", due="2026-04-30", confidence=0.9),
                Commitment(id="c2", who="Pedro", what="do Y", due=None, confidence=0.7),
            ],
            meetings_to_schedule=[
                MeetingToSchedule(with_whom="ops", topic="standup", proposed_when="Monday", duration_minutes=15),
            ],
            questions_raised=["Q1"],
            people_mentioned=["Pedro"],
        )

        assert save_extraction(conn, ext) is True

        page = conn.execute("SELECT * FROM pages WHERE page_id='p1'").fetchone()
        assert page is not None
        assert page["summary"] == "A test page."
        assert json.loads(page["raw_json"])["page_id"] == "p1"

        comms = conn.execute("SELECT * FROM commitments WHERE page_id='p1'").fetchall()
        assert len(comms) == 2
        assert all(c["status"] == "open" for c in comms)

        meetings = conn.execute("SELECT * FROM meetings_proposed WHERE page_id='p1'").fetchall()
        assert len(meetings) == 1
        assert meetings[0]["status"] == "pending"
        assert meetings[0]["with_whom"] == "ops"
    finally:
        conn.close()


def test_save_extraction_is_idempotent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "state.db")
    try:
        ext = PageExtraction(
            page_id="p1",
            notebook="Daily",
            captured_date="2026-04-25",
            summary="A test page.",
        )
        assert save_extraction(conn, ext) is True
        assert save_extraction(conn, ext) is False  # no-op second time

        rows = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        assert rows == 1
    finally:
        conn.close()


# ---------- full pipeline ----------


def test_full_pipeline_sync_extract_store(tmp_path: Path) -> None:
    """Sync → extract → store: PNGs created, Claude called, rows in SQLite."""
    notebook = NotebookConfig(path="/Notes/Daily", name="Daily", type="daily_log")
    cfg = _make_config(tmp_path, [notebook])

    # -- sync --
    def build(remote_path: str, dest_dir: Path) -> Path:
        return _make_archive(
            dest_dir,
            "daily-uuid",
            [("page-1", 1_700_000_000_000), ("page-2", 1_700_086_400_000)],
        )

    png_paths = sync.run(cfg, rmapi=FakeRmapi(archive_builder=build), rmc=FakeRmc())
    assert len(png_paths) == 2

    # -- extract + store --
    canned1 = _make_canned_response("page-1", "2023-11-14")
    canned2 = _make_canned_response("page-2", "2023-11-15")
    client = _fake_claude_client(canned1, canned2)

    with (
        patch("remarkable_ea.extract.anthropic_api_key", return_value="fake"),
        patch("remarkable_ea.extract.anthropic.Anthropic", return_value=client),
    ):
        extractions = extract.run(cfg)

    assert len(extractions) == 2
    assert client.messages.create.call_count == 2

    # -- verify store --
    conn = connect(cfg.storage.db_path)
    try:
        pages = conn.execute("SELECT page_id FROM pages ORDER BY page_id").fetchall()
        assert [r["page_id"] for r in pages] == ["page-1", "page-2"]

        comms = conn.execute("SELECT * FROM commitments ORDER BY id").fetchall()
        assert len(comms) == 4  # 2 per page
        assert all(c["status"] == "open" for c in comms)

        meetings = conn.execute("SELECT * FROM meetings_proposed").fetchall()
        assert len(meetings) == 2  # 1 per page
        assert all(m["status"] == "pending" for m in meetings)
    finally:
        conn.close()


def test_pipeline_second_run_is_noop(tmp_path: Path) -> None:
    """After a complete run, both sync and extract skip existing work."""
    notebook = NotebookConfig(path="/Notes/Daily", name="Daily", type="daily_log")
    cfg = _make_config(tmp_path, [notebook])

    def build(remote_path: str, dest_dir: Path) -> Path:
        return _make_archive(dest_dir, "daily-uuid", [("page-1", 1_700_000_000_000)])

    rmapi = FakeRmapi(archive_builder=build)
    rmc = FakeRmc()

    # First run: creates PNGs + extracts + stores.
    sync.run(cfg, rmapi=rmapi, rmc=rmc)
    canned = _make_canned_response("page-1", "2023-11-14")
    client = _fake_claude_client(canned)
    with (
        patch("remarkable_ea.extract.anthropic_api_key", return_value="fake"),
        patch("remarkable_ea.extract.anthropic.Anthropic", return_value=client),
    ):
        first = extract.run(cfg)
    assert len(first) == 1

    # Second run: sync skips (PNG exists), extract skips (page in store).
    second_pngs = sync.run(cfg, rmapi=rmapi, rmc=rmc)
    assert second_pngs == []

    client2 = _fake_claude_client()  # no side-effects needed — shouldn't be called
    with (
        patch("remarkable_ea.extract.anthropic_api_key", return_value="fake"),
        patch("remarkable_ea.extract.anthropic.Anthropic", return_value=client2),
    ):
        second = extract.run(cfg)
    assert second == []
    assert client2.messages.create.call_count == 0


def test_pipeline_extract_failure_saves_others(tmp_path: Path) -> None:
    """If one page's extraction fails, the others still persist."""
    notebook = NotebookConfig(path="/Notes/Daily", name="Daily", type="daily_log")
    cfg = _make_config(tmp_path, [notebook])

    def build(remote_path: str, dest_dir: Path) -> Path:
        return _make_archive(
            dest_dir,
            "daily-uuid",
            [("page-1", 1_700_000_000_000), ("page-2", 1_700_086_400_000)],
        )

    sync.run(cfg, rmapi=FakeRmapi(archive_builder=build), rmc=FakeRmc())

    canned = _make_canned_response("page-2", "2023-11-15")
    # First call raises, second succeeds.
    client = MagicMock()
    text_block = MagicMock()
    text_block.text = json.dumps(canned)
    ok_response = MagicMock()
    ok_response.content = [text_block]
    client.messages.create.side_effect = [RuntimeError("API error"), ok_response]

    with (
        patch("remarkable_ea.extract.anthropic_api_key", return_value="fake"),
        patch("remarkable_ea.extract.anthropic.Anthropic", return_value=client),
    ):
        results = extract.run(cfg)

    assert len(results) == 1
    assert results[0].page_id == "page-2"

    conn = connect(cfg.storage.db_path)
    try:
        pages = conn.execute("SELECT page_id FROM pages").fetchall()
        assert len(pages) == 1
        assert pages[0]["page_id"] == "page-2"
    finally:
        conn.close()
