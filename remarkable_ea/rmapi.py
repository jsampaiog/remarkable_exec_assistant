"""Thin wrapper around the ``rmapi`` Go binary.

rmapi exposes the reMarkable cloud API as a filesystem-like CLI: documents
have paths, not raw UUIDs, and ``rmapi get <path>`` downloads a notebook as
a ``.rmdoc`` archive (a zip containing ``.rm`` page files and metadata).

Only the subset of rmapi we actually need is wrapped here, and every call
goes through ``subprocess.run`` so tests can monkeypatch one seam.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class RmapiError(RuntimeError):
    """Raised when rmapi exits non-zero or produces nothing usable."""


_ARCHIVE_SUFFIXES = (".rmdoc", ".zip")


@dataclass(frozen=True)
class RmapiClient:
    """Subprocess wrapper around the rmapi Go binary."""

    rmapi_path: str

    def download(self, remote_path: str, dest_dir: Path) -> Path:
        """Download a notebook via ``rmapi get`` and return the archive path.

        rmapi writes the archive (``<name>.rmdoc`` or ``<name>.zip`` depending
        on the version) into the current working directory, so we run it with
        ``cwd=dest_dir`` and then identify the new file by set difference.

        Raises ``RmapiError`` if rmapi exits non-zero or produces no archive.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        before = {p.name for p in dest_dir.iterdir()}

        result = subprocess.run(
            [self.rmapi_path, "get", remote_path],
            cwd=str(dest_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            msg = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RmapiError(f"rmapi get {remote_path!r} failed: {msg}")

        new_archives = [
            p
            for p in dest_dir.iterdir()
            if p.name not in before and p.suffix in _ARCHIVE_SUFFIXES
        ]
        if not new_archives:
            raise RmapiError(
                f"rmapi get {remote_path!r} succeeded but produced no "
                f".rmdoc/.zip archive in {dest_dir}"
            )
        # Prefer the newest if rmapi somehow dropped more than one.
        new_archives.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return new_archives[0]
