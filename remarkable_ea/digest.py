"""Component 4 — Digest.

Produces and sends the daily briefing email. Sections (built incrementally
across build steps 5–8):

    1. Today's pages
    2. New commitments captured today
    3. Aging commitments
    4. Reconciliation proposals
    5. Proposed meetings (with .ics attachments)
    6. Questions raised today

Stub; implemented starting in build step 5.
"""

from __future__ import annotations

from remarkable_ea.config import Config


def run(cfg: Config) -> None:
    raise NotImplementedError("digest is implemented starting in build step 5")
