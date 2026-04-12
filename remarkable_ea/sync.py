"""Component 1 — Sync.

Pulls new or modified pages from whitelisted reMarkable notebooks since the
last run, converts ``.rm`` files to PNG, and writes them to the pages dir.

Stub; implemented in build step 2.
"""

from __future__ import annotations

from remarkable_ea.config import Config


def run(cfg: Config) -> None:
    raise NotImplementedError("sync is implemented in build step 2")
