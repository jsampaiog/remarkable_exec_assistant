"""Tests for the rmapi subprocess wrapper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from remarkable_ea.rmapi import RmapiClient, RmapiError


def _ok(stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "", stdout: str = "") -> MagicMock:
    return MagicMock(returncode=1, stdout=stdout, stderr=stderr)


def test_download_invokes_rmapi_with_correct_args(tmp_path: Path) -> None:
    client = RmapiClient(rmapi_path="/usr/local/bin/rmapi")

    def fake_run(cmd, cwd, capture_output, text):
        (Path(cwd) / "DailyLog.rmdoc").write_bytes(b"fake-archive")
        return _ok()

    with patch("remarkable_ea.rmapi.subprocess.run", side_effect=fake_run) as m:
        result = client.download("/Notes/DailyLog", tmp_path)

    assert result == tmp_path / "DailyLog.rmdoc"
    assert m.call_args.args[0] == [
        "/usr/local/bin/rmapi",
        "get",
        "/Notes/DailyLog",
    ]
    assert m.call_args.kwargs["cwd"] == str(tmp_path)


def test_download_identifies_only_the_new_archive(tmp_path: Path) -> None:
    """Pre-existing files in dest_dir must not be returned."""
    client = RmapiClient(rmapi_path="rmapi")
    (tmp_path / "stale.rmdoc").write_bytes(b"old")
    (tmp_path / "unrelated.txt").write_text("ignore me")

    def fake_run(cmd, cwd, capture_output, text):
        (Path(cwd) / "fresh.rmdoc").write_bytes(b"new")
        return _ok()

    with patch("remarkable_ea.rmapi.subprocess.run", side_effect=fake_run):
        result = client.download("/Notes/X", tmp_path)

    assert result == tmp_path / "fresh.rmdoc"


def test_download_accepts_zip_suffix(tmp_path: Path) -> None:
    """Some rmapi versions emit .zip instead of .rmdoc."""
    client = RmapiClient(rmapi_path="rmapi")

    def fake_run(cmd, cwd, capture_output, text):
        (Path(cwd) / "legacy.zip").write_bytes(b"new")
        return _ok()

    with patch("remarkable_ea.rmapi.subprocess.run", side_effect=fake_run):
        result = client.download("/Notes/Legacy", tmp_path)

    assert result.suffix == ".zip"


def test_download_nonzero_exit_raises_with_stderr(tmp_path: Path) -> None:
    client = RmapiClient(rmapi_path="rmapi")
    with patch(
        "remarkable_ea.rmapi.subprocess.run",
        return_value=_fail(stderr="authentication required"),
    ):
        with pytest.raises(RmapiError, match="authentication required"):
            client.download("/Notes/X", tmp_path)


def test_download_no_archive_raises(tmp_path: Path) -> None:
    client = RmapiClient(rmapi_path="rmapi")
    with patch("remarkable_ea.rmapi.subprocess.run", return_value=_ok()):
        with pytest.raises(RmapiError, match="no .rmdoc/.zip archive"):
            client.download("/Notes/X", tmp_path)


def test_download_creates_dest_dir(tmp_path: Path) -> None:
    client = RmapiClient(rmapi_path="rmapi")
    dest = tmp_path / "nested" / "archives"

    def fake_run(cmd, cwd, capture_output, text):
        (Path(cwd) / "a.rmdoc").write_bytes(b"new")
        return _ok()

    with patch("remarkable_ea.rmapi.subprocess.run", side_effect=fake_run):
        client.download("/Notes/X", dest)

    assert dest.is_dir()
