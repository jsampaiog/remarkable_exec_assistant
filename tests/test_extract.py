"""Tests for the extract module.

The Claude API call is mocked; we test the prompt construction, JSON parsing,
edge-case tolerance, and the batch ``run()`` logic. A live integration test
confirmed the prompt produces excellent results on a synthetic meeting-notes
page — that test is too slow and costly for CI but documented in the step-3
commit message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from remarkable_ea.config import (
    ClaudeConfig,
    Config,
    EmailConfig,
    NotebookConfig,
    RemarkableConfig,
    StorageConfig,
)
from remarkable_ea.extract import (
    PageExtraction,
    _parse_extraction,
    _parse_png_filename,
    extract_page,
    extraction_to_json,
    run,
)
from remarkable_ea.store import connect


# ---------- canned response from the live test ----------

CANNED_RESPONSE = {
    "page_id": "test-page-001",
    "notebook": "Meetings",
    "captured_date": "2026-04-25",
    "summary": (
        "J met with the marketing team on Apr 25 to discuss the Q3 campaign. "
        "Key action items were assigned: J must review the analytics dashboard "
        "by Apr 30, Pedro must send a revised proposal by Apr 28, and Sarah M. "
        "must share campaign mockups by May 2."
    ),
    "commitments": [
        {
            "id": "c1",
            "who": "self",
            "what": "Review analytics dashboard",
            "due": "2026-04-30",
            "confidence": 0.95,
        },
        {
            "id": "c2",
            "who": "Pedro",
            "what": "Send revised proposal",
            "due": "2026-04-28",
            "confidence": 0.95,
        },
        {
            "id": "c3",
            "who": "Sarah M.",
            "what": "Share campaign mockups",
            "due": "2026-05-02",
            "confidence": 0.95,
        },
    ],
    "meetings_to_schedule": [
        {
            "with": "design team",
            "topic": "follow-up",
            "proposed_when": "next Tuesday afternoon",
            "duration_minutes": 45,
        }
    ],
    "questions_raised": [
        "What's the ROI target for Q3?",
        "Should we involve external agency?",
    ],
    "people_mentioned": ["Sarah M.", "Pedro", "Lisa Chen"],
    "references_past": "Q2 results from last week's board presentation",
}


# ---------- helpers ----------


def _make_fake_client(response_json: dict) -> MagicMock:
    """Return a mock anthropic.Anthropic whose messages.create returns ``response_json``."""
    text_block = MagicMock()
    text_block.text = json.dumps(response_json)
    response = MagicMock()
    response.content = [text_block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


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


def _write_fake_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG fake image data")


# ---------- _parse_extraction ----------


def test_parse_extraction_full() -> None:
    ext = _parse_extraction(CANNED_RESPONSE)
    assert ext.page_id == "test-page-001"
    assert ext.notebook == "Meetings"
    assert len(ext.commitments) == 3
    assert ext.commitments[0].who == "self"
    assert ext.commitments[1].who == "Pedro"
    assert ext.commitments[1].due == "2026-04-28"
    assert len(ext.meetings_to_schedule) == 1
    assert ext.meetings_to_schedule[0].with_whom == "design team"
    assert ext.meetings_to_schedule[0].duration_minutes == 45
    assert len(ext.questions_raised) == 2
    assert "Sarah M." in ext.people_mentioned
    assert ext.references_past is not None


def test_parse_extraction_missing_optional_fields() -> None:
    minimal = {
        "page_id": "p1",
        "notebook": "nb",
        "captured_date": "2026-01-01",
        "summary": "A short page.",
    }
    ext = _parse_extraction(minimal)
    assert ext.page_id == "p1"
    assert ext.commitments == []
    assert ext.meetings_to_schedule == []
    assert ext.questions_raised == []
    assert ext.people_mentioned == []
    assert ext.references_past is None


def test_parse_extraction_assigns_uuid_when_id_missing() -> None:
    data = {
        **CANNED_RESPONSE,
        "commitments": [{"who": "self", "what": "do thing"}],
    }
    ext = _parse_extraction(data)
    assert len(ext.commitments) == 1
    assert ext.commitments[0].id  # should be a non-empty UUID string
    assert ext.commitments[0].confidence == 0.5  # default when missing


def test_parse_extraction_handles_with_whom_key() -> None:
    """The model might emit 'with_whom' instead of 'with'."""
    data = {
        **CANNED_RESPONSE,
        "meetings_to_schedule": [
            {"with_whom": "ops team", "topic": "standup", "proposed_when": None}
        ],
    }
    ext = _parse_extraction(data)
    assert ext.meetings_to_schedule[0].with_whom == "ops team"


# ---------- extract_page ----------


def test_extract_page_sends_image_and_parses_response(tmp_path: Path) -> None:
    png = tmp_path / "page.png"
    _write_fake_png(png)
    client = _make_fake_client(CANNED_RESPONSE)

    ext = extract_page(
        png_path=png,
        page_id="test-page-001",
        notebook_name="Meetings",
        notebook_type="meeting_notes",
        captured_date="2026-04-25",
        client=client,
    )

    assert ext.page_id == "test-page-001"
    assert len(ext.commitments) == 3
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert call_kwargs["system"] != ""
    user_content = call_kwargs["messages"][0]["content"]
    assert user_content[0]["type"] == "image"
    assert user_content[1]["type"] == "text"


def test_extract_page_strips_markdown_fences(tmp_path: Path) -> None:
    """Claude sometimes wraps JSON in ```json ... ``` despite instructions."""
    png = tmp_path / "page.png"
    _write_fake_png(png)
    fenced = "```json\n" + json.dumps(CANNED_RESPONSE) + "\n```"
    text_block = MagicMock()
    text_block.text = fenced
    response = MagicMock()
    response.content = [text_block]
    client = MagicMock()
    client.messages.create.return_value = response

    ext = extract_page(
        png_path=png,
        page_id="test-page-001",
        notebook_name="Meetings",
        notebook_type="meeting_notes",
        captured_date="2026-04-25",
        client=client,
    )
    assert ext.page_id == "test-page-001"


# ---------- extraction_to_json ----------


def test_extraction_to_json_roundtrip() -> None:
    ext = _parse_extraction(CANNED_RESPONSE)
    raw = extraction_to_json(ext)
    data = json.loads(raw)
    # 'with_whom' is renamed back to 'with' for storage compatibility
    assert data["meetings_to_schedule"][0]["with"] == "design team"
    assert "with_whom" not in data["meetings_to_schedule"][0]


# ---------- _parse_png_filename ----------


def test_parse_png_filename_standard() -> None:
    page_id, date = _parse_png_filename("abc-def-123_2026-04-25")
    assert page_id == "abc-def-123"
    assert date == "2026-04-25"


def test_parse_png_filename_uuid_with_many_dashes() -> None:
    page_id, date = _parse_png_filename("a1b2c3d4-e5f6-7890-abcd-ef1234567890_2026-04-25")
    assert page_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    assert date == "2026-04-25"


def test_parse_png_filename_no_date() -> None:
    page_id, date = _parse_png_filename("just-a-name")
    assert page_id == "just-a-name"
    assert date == "unknown"


# ---------- run() batch ----------


def test_run_extracts_only_new_pages(tmp_path: Path) -> None:
    notebook = NotebookConfig(path="/Notes/Daily", name="Daily", type="daily_log")
    cfg = _make_config(tmp_path, [notebook])

    nb_dir = cfg.storage.pages_dir / "Notes_Daily"
    _write_fake_png(nb_dir / "page-1_2026-04-25.png")
    _write_fake_png(nb_dir / "page-2_2026-04-26.png")

    # Pre-insert page-1 into the store so it's "already extracted".
    conn = connect(cfg.storage.db_path)
    conn.execute(
        "INSERT INTO pages(page_id, notebook, captured_date, summary, raw_json, extracted_at)"
        " VALUES ('page-1', 'Daily', '2026-04-25', 's', '{}', '2026-04-25T18:30:00')"
    )
    conn.commit()
    conn.close()

    canned = {
        **CANNED_RESPONSE,
        "page_id": "page-2",
        "captured_date": "2026-04-26",
    }
    client = _make_fake_client(canned)

    with (
        patch("remarkable_ea.extract.anthropic_api_key", return_value="fake"),
        patch("remarkable_ea.extract.anthropic.Anthropic", return_value=client),
    ):
        results = run(cfg)

    assert len(results) == 1
    assert results[0].page_id == "page-2"
    assert client.messages.create.call_count == 1
