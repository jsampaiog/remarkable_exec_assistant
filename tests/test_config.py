"""Tests for the config loader."""

from __future__ import annotations

from pathlib import Path

from remarkable_ea.config import load_config


SAMPLE_CONFIG = """
remarkable:
  rmapi_path: /usr/local/bin/rmapi
  notebooks:
    - uuid: "abc-123"
      name: "Daily Log"
      type: "daily_log"
    - uuid: "def-456"
      name: "Meetings"
      type: "meeting_notes"

claude:
  model: "claude-sonnet-4-6"

email:
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  from: "a@b.com"
  to: "c@d.com"

storage:
  db_path: "{db}"
  pages_dir: "{pages}"
"""


def test_load_config_parses_all_sections(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        SAMPLE_CONFIG.format(
            db=tmp_path / "state.db",
            pages=tmp_path / "pages",
        )
    )

    cfg = load_config(cfg_path)

    assert cfg.remarkable.rmapi_path == "/usr/local/bin/rmapi"
    assert len(cfg.remarkable.notebooks) == 2
    assert cfg.remarkable.notebooks[0].uuid == "abc-123"
    assert cfg.remarkable.notebooks[0].type == "daily_log"
    assert cfg.remarkable.notebooks[1].name == "Meetings"

    assert cfg.claude.model == "claude-sonnet-4-6"

    assert cfg.email.smtp_host == "smtp.gmail.com"
    assert cfg.email.smtp_port == 587
    # IMAP defaults fill in when omitted.
    assert cfg.email.imap_host == "imap.gmail.com"
    assert cfg.email.imap_port == 993
    assert cfg.email.from_addr == "a@b.com"
    assert cfg.email.to_addr == "c@d.com"

    assert cfg.storage.db_path == (tmp_path / "state.db").resolve()
    assert cfg.storage.pages_dir == (tmp_path / "pages").resolve()


def test_load_config_expands_user(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        SAMPLE_CONFIG.format(db="~/state.db", pages="~/pages")
    )

    cfg = load_config(cfg_path)

    assert cfg.storage.db_path == (tmp_path / "state.db").resolve()
    assert cfg.storage.pages_dir == (tmp_path / "pages").resolve()
