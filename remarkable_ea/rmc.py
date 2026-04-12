"""Thin wrapper around the ``rmc`` binary (`.rm` → PNG conversion).

rmc is a separate tool that renders reMarkable ``.rm`` page files to images.
Its exact CLI surface varies by version; this wrapper targets the common
``rmc -t png -o <out> <in>`` form.

The spec asks for 150 DPI output. rmc's resolution control depends on the
installed version, so we keep ``dpi`` as a field for future use but do not
currently pass it to the subprocess. If the default rendering is wrong on
the operator's machine they can edit this wrapper without touching sync.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class RmcError(RuntimeError):
    """Raised when rmc exits non-zero."""


@dataclass(frozen=True)
class RmcConverter:
    rmc_path: str = "rmc"
    dpi: int = 150  # advisory; not currently forwarded to rmc

    def convert(self, rm_file: Path, dest_png: Path) -> None:
        """Render a single ``.rm`` page to a PNG file at ``dest_png``."""
        dest_png.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [self.rmc_path, "-t", "png", "-o", str(dest_png), str(rm_file)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            msg = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RmcError(f"rmc failed on {rm_file.name}: {msg}")
