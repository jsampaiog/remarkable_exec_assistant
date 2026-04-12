"""Reply-to-close ingest.

Polls the inbox (IMAP) for replies to yesterday's digest and applies simple
commands parsed from the reply body:

    close 3, 7, 12
    cancel 5
    snooze 8 until 2026-04-20
    schedule 2

Malformed commands are ignored; a bad reply never breaks the loop.

Stub; implemented in build step 6.
"""

from __future__ import annotations

from remarkable_ea.config import Config


def run(cfg: Config) -> None:
    raise NotImplementedError("ingest-replies is implemented in build step 6")
