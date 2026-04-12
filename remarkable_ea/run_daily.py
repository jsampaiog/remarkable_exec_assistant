"""Daily orchestration entrypoint.

Runs the full pipeline sequentially. This is what cron invokes (via
``remarkable-ea run-daily``).

Order is deliberate:
    1. ingest-replies  — apply any commands from yesterday's digest first, so
                         the state the rest of the pipeline reads is current.
    2. sync            — pull new or modified pages from the reMarkable cloud.
    3. extract         — run Claude vision over any newly synced PNGs.
    4. digest          — build and send today's briefing email.
"""

from __future__ import annotations

from remarkable_ea import digest, extract, ingest_replies, sync
from remarkable_ea.config import Config


def run(cfg: Config) -> None:
    ingest_replies.run(cfg)
    sync.run(cfg)
    extract.run(cfg)
    digest.run(cfg)
