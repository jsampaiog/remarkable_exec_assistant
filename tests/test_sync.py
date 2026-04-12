"""Tests for sync orchestration.

The subprocess layer (rmapi + rmc) is replaced with in-process fakes so
we can exercise the full flow — archive unpacking, deduplication by
destination path, per-notebook error isolation, and sync_state upkeep —
without touching the network or requiring Go binaries.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from remarkable_ea import sync
from remarkable_ea.config import (
    ClaudeConfig,
    Config,
    EmailConfig,
    NotebookConfig,
    RemarkableConfig,
    StorageConfig,
)
from remarkable_ea.store import connect


# ---------- fakes ----------


@dataclass
class FakeRmapi:
    archive_builder: Any  # (remote_path, dest_dir) -> archive Path
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
    dest_dir: Path,
    notebook_uuid: str,
    pages: list[tuple[str, int]],
) -> Path:
    """Build a fake .rmdoc archive mirroring the real layout.

    ``pages`` is a list of (page_uuid, lastModified_ms).
    """
    archive_path = dest_dir / f"{notebook_uuid}.rmdoc"
    with zipfile.ZipFile(archive_path, "w") as zf:
        for page_uuid, last_modified_ms in pages:
            zf.writestr(f"{notebook_uuid}/{page_uuid}.rm", b"fake-rm-data")
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


# ---------- tests ----------


def test_sync_creates_png_per_new_page(tmp_path: Path) -> None:
    notebook = NotebookConfig(path="/Notes/Daily", name="Daily", type="daily_log")
    cfg = _make_config(tmp_path, [notebook])

    def build(remote_path: str, dest_dir: Path) -> Path:
        return _make_archive(
            dest_dir,
            "daily-uuid",
            [
                ("page-1", 1_700_000_000_000),  # 2023-11-14
                ("page-2", 1_700_086_400_000),  # 2023-11-15
            ],
        )

    rmapi = FakeRmapi(archive_builder=build)
    rmc = FakeRmc()

    created = sync.run(cfg, rmapi=rmapi, rmc=rmc)

    assert len(created) == 2
    assert all(p.exists() and p.suffix == ".png" for p in created)
    nb_dir = cfg.storage.pages_dir / "Notes_Daily"
    assert sorted(p.name for p in nb_dir.glob("*.png")) == [
        "page-1_2023-11-14.png",
        "page-2_2023-11-15.png",
    ]
    # rmapi was called exactly once for this notebook
    assert len(rmapi.calls) == 1
    assert rmapi.calls[0][0] == "/Notes/Daily"


def test_sync_is_idempotent(tmp_path: Path) -> None:
    notebook = NotebookConfig(path="/Notes/Daily", name="Daily", type="daily_log")
    cfg = _make_config(tmp_path, [notebook])

    def build(remote_path: str, dest_dir: Path) -> Path:
        return _make_archive(dest_dir, "daily-uuid", [("page-1", 1_700_000_000_000)])

    rmapi = FakeRmapi(archive_builder=build)
    rmc = FakeRmc()

    first = sync.run(cfg, rmapi=rmapi, rmc=rmc)
    second = sync.run(cfg, rmapi=rmapi, rmc=rmc)

    assert len(first) == 1
    assert second == []
    assert len(rmc.calls) == 1  # rmc only ran on the first pass


def test_sync_picks_up_new_pages_on_second_run(tmp_path: Path) -> None:
    notebook = NotebookConfig(path="/Notes/Daily", name="Daily", type="daily_log")
    cfg = _make_config(tmp_path, [notebook])

    pages = [("page-1", 1_700_000_000_000)]

    def build(remote_path: str, dest_dir: Path) -> Path:
        return _make_archive(dest_dir, "daily-uuid", list(pages))

    rmapi = FakeRmapi(archive_builder=build)
    rmc = FakeRmc()

    first = sync.run(cfg, rmapi=rmapi, rmc=rmc)
    assert len(first) == 1

    pages.append(("page-2", 1_700_172_800_000))  # 2023-11-16
    second = sync.run(cfg, rmapi=rmapi, rmc=rmc)

    assert len(second) == 1
    assert second[0].name == "page-2_2023-11-16.png"


def test_sync_updates_sync_state(tmp_path: Path) -> None:
    notebook = NotebookConfig(path="/Notes/Daily", name="Daily", type="daily_log")
    cfg = _make_config(tmp_path, [notebook])

    def build(remote_path: str, dest_dir: Path) -> Path:
        return _make_archive(dest_dir, "daily-uuid", [("p", 1_700_000_000_000)])

    sync.run(cfg, rmapi=FakeRmapi(archive_builder=build), rmc=FakeRmc())

    conn = connect(cfg.storage.db_path)
    try:
        row = conn.execute(
            "SELECT last_synced_at FROM sync_state WHERE notebook_id=?",
            ("Notes_Daily",),
        ).fetchone()
        assert row is not None
        assert row["last_synced_at"].endswith("+00:00")
    finally:
        conn.close()


def test_sync_isolates_per_notebook_failures(tmp_path: Path) -> None:
    nb_bad = NotebookConfig(path="/Notes/Bad", name="Bad", type="daily_log")
    nb_good = NotebookConfig(path="/Notes/Good", name="Good", type="daily_log")
    cfg = _make_config(tmp_path, [nb_bad, nb_good])

    def build(remote_path: str, dest_dir: Path) -> Path:
        if "Bad" in remote_path:
            raise RuntimeError("fake rmapi auth failure")
        return _make_archive(dest_dir, "good-uuid", [("p", 1_700_000_000_000)])

    rmapi = FakeRmapi(archive_builder=build)
    created = sync.run(cfg, rmapi=rmapi, rmc=FakeRmc())

    assert len(created) == 1
    assert "Notes_Good" in str(created[0])

    # The good notebook still recorded its sync_state; the bad one did not.
    conn = connect(cfg.storage.db_path)
    try:
        ids = {
            row[0] for row in conn.execute("SELECT notebook_id FROM sync_state")
        }
        assert ids == {"Notes_Good"}
    finally:
        conn.close()


def test_sync_falls_back_to_mtime_when_metadata_missing(tmp_path: Path) -> None:
    notebook = NotebookConfig(path="/Notes/Daily", name="Daily", type="daily_log")
    cfg = _make_config(tmp_path, [notebook])

    def build(remote_path: str, dest_dir: Path) -> Path:
        # Build an archive with a .rm but NO sibling metadata file.
        archive = dest_dir / "bare.rmdoc"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("bare/page-x.rm", b"fake-rm-data")
        return archive

    created = sync.run(
        cfg,
        rmapi=FakeRmapi(archive_builder=build),
        rmc=FakeRmc(),
    )

    assert len(created) == 1
    # Falls back to file mtime, which is "today" in the unpacked tmp dir;
    # we only assert the filename shape is <page>_<YYYY-MM-DD>.png.
    name = created[0].name
    assert name.startswith("page-x_")
    assert name.endswith(".png")
    assert len(name) == len("page-x_YYYY-MM-DD.png")
