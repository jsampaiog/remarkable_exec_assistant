"""Tests for the rmc subprocess wrapper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from remarkable_ea.rmc import RmcConverter, RmcError


def test_convert_invokes_rmc_with_correct_args(tmp_path: Path) -> None:
    conv = RmcConverter(rmc_path="/usr/local/bin/rmc")
    rm_file = tmp_path / "page.rm"
    rm_file.write_bytes(b"fake-rm")
    dest = tmp_path / "out" / "page.png"

    def fake_run(cmd, capture_output, text):
        out_idx = cmd.index("-o") + 1
        Path(cmd[out_idx]).write_bytes(b"\x89PNG-fake")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("remarkable_ea.rmc.subprocess.run", side_effect=fake_run) as m:
        conv.convert(rm_file, dest)

    assert dest.exists()
    assert m.call_args.args[0] == [
        "/usr/local/bin/rmc",
        "-t",
        "png",
        "-o",
        str(dest),
        str(rm_file),
    ]


def test_convert_creates_parent_dir(tmp_path: Path) -> None:
    conv = RmcConverter()
    rm_file = tmp_path / "page.rm"
    rm_file.write_bytes(b"fake")
    dest = tmp_path / "deeply" / "nested" / "out.png"

    def fake_run(cmd, capture_output, text):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"png")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("remarkable_ea.rmc.subprocess.run", side_effect=fake_run):
        conv.convert(rm_file, dest)

    assert dest.exists()


def test_convert_nonzero_exit_raises(tmp_path: Path) -> None:
    conv = RmcConverter()
    rm_file = tmp_path / "page.rm"
    rm_file.write_bytes(b"fake")

    with patch(
        "remarkable_ea.rmc.subprocess.run",
        return_value=MagicMock(returncode=2, stdout="", stderr="unsupported format"),
    ):
        with pytest.raises(RmcError, match="unsupported format"):
            conv.convert(rm_file, tmp_path / "out.png")
