"""Component 2 — Extract.

Calls Claude vision once per page PNG and writes the structured result into
the store. The extractor never decides to schedule anything; it only reports
what the notes say.

Stub; implemented in build step 3.
"""

from __future__ import annotations

from remarkable_ea.config import Config


def run(cfg: Config) -> None:
    raise NotImplementedError("extract is implemented in build step 3")
