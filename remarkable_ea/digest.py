"""Component 4 — Digest.

Produces and sends the daily briefing email. This step implements sections
1–3; sections 4–6 are added in subsequent build steps.

Sections:
    1. Today's pages — one bullet per page, summary only.
    2. New commitments captured today — grouped by who.
    3. Aging commitments — open commitments past due or >7 days without due.

The email is sent via SMTP (Gmail with app password). It's plain-text-friendly
HTML — a single <pre>-like body that reads well even in text-only clients.
"""

from __future__ import annotations

import logging
import smtplib
import sqlite3
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from remarkable_ea.config import Config, gmail_app_password
from remarkable_ea.store import connect

log = logging.getLogger(__name__)

AGING_THRESHOLD_DAYS = 7


# ---------- store queries ----------

def _today_pages(conn: sqlite3.Connection, today: str) -> list[dict]:
    rows = conn.execute(
        "SELECT page_id, notebook, summary FROM pages "
        "WHERE captured_date = ? ORDER BY page_id",
        (today,),
    ).fetchall()
    return [dict(r) for r in rows]


def _today_commitments(conn: sqlite3.Connection, today: str) -> list[dict]:
    rows = conn.execute(
        "SELECT c.id, c.who, c.what, c.due, c.confidence "
        "FROM commitments c JOIN pages p ON c.page_id = p.page_id "
        "WHERE p.captured_date = ? AND c.status = 'open' "
        "ORDER BY c.who, c.id",
        (today,),
    ).fetchall()
    return [dict(r) for r in rows]


def _aging_commitments(conn: sqlite3.Connection, today: str) -> list[dict]:
    cutoff = (date.fromisoformat(today) - timedelta(days=AGING_THRESHOLD_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT c.id, c.who, c.what, c.due, c.confidence, c.created_at "
        "FROM commitments c "
        "WHERE c.status = 'open' AND ("
        "  (c.due IS NOT NULL AND c.due < ?) OR "
        "  (c.due IS NULL AND c.created_at < ?)"
        ") "
        "ORDER BY COALESCE(c.due, c.created_at)",
        (today, cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- numbering ----------

class DigestNumbering:
    """Assigns sequential IDs to commitments for the reply-to-close loop."""

    def __init__(self) -> None:
        self._counter = 0
        self._map: dict[int, str] = {}  # digest_num → commitment.id

    def next(self, commitment_id: str) -> int:
        self._counter += 1
        self._map[self._counter] = commitment_id
        return self._counter

    @property
    def mapping(self) -> dict[int, str]:
        return dict(self._map)


# ---------- email body ----------

def _build_body(
    today: str,
    pages: list[dict],
    new_commitments: list[dict],
    aging: list[dict],
    numbering: DigestNumbering,
) -> str:
    parts: list[str] = []
    parts.append(f"reMarkable Daily Digest — {today}")
    parts.append("=" * 50)

    # Section 1: Today's pages
    parts.append("")
    parts.append("📄 TODAY'S PAGES")
    parts.append("-" * 30)
    if pages:
        for p in pages:
            parts.append(f"  • [{p['notebook']}] {p['page_id']}: {p['summary']}")
    else:
        parts.append("  (no new pages today)")

    # Section 2: New commitments
    parts.append("")
    parts.append("✅ NEW COMMITMENTS CAPTURED TODAY")
    parts.append("-" * 30)
    if new_commitments:
        by_who: dict[str, list[dict]] = {}
        for c in new_commitments:
            by_who.setdefault(c["who"], []).append(c)
        for who, items in by_who.items():
            label = "You (J)" if who == "self" else who
            parts.append(f"  {label}:")
            for c in items:
                num = numbering.next(c["id"])
                due_str = f" (due: {c['due']})" if c["due"] else ""
                parts.append(f"    #{num} {c['what']}{due_str}")
    else:
        parts.append("  (none)")

    # Section 3: Aging commitments
    parts.append("")
    parts.append("⏰ AGING COMMITMENTS")
    parts.append("-" * 30)
    if aging:
        for c in aging:
            num = numbering.next(c["id"])
            who_label = "You (J)" if c["who"] == "self" else c["who"]
            if c["due"]:
                age_str = f"past due ({c['due']})"
            else:
                age_str = f"open since {c['created_at'][:10]}"
            parts.append(f"  #{num} [{who_label}] {c['what']} — {age_str}")
    else:
        parts.append("  (none)")

    # Reply instructions
    parts.append("")
    parts.append("-" * 50)
    parts.append("Reply to this email with commands:")
    parts.append("  close 3, 7      — mark commitments as done")
    parts.append("  cancel 5        — drop a commitment")
    parts.append("  snooze 8 until 2026-05-10")
    parts.append("")

    return "\n".join(parts)


def _build_html(body_text: str) -> str:
    escaped = (
        body_text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return (
        '<html><body style="font-family: monospace; white-space: pre-wrap; '
        f'font-size: 14px; line-height: 1.5;">{escaped}</body></html>'
    )


# ---------- send ----------

def _send_email(
    cfg: Config,
    subject: str,
    body_text: str,
    body_html: str,
    *,
    attachments: list[tuple[str, bytes]] | None = None,
) -> None:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = cfg.email.from_addr
    msg["To"] = cfg.email.to_addr

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_text, "plain"))
    alt.attach(MIMEText(body_html, "html"))
    msg.attach(alt)

    if attachments:
        from email.mime.base import MIMEBase
        from email import encoders

        for filename, data in attachments:
            part = MIMEBase("text", "calendar")
            part.set_payload(data)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)

    password = gmail_app_password()
    log.info("sending digest to %s", cfg.email.to_addr)
    with smtplib.SMTP(cfg.email.smtp_host, cfg.email.smtp_port) as server:
        server.starttls()
        server.login(cfg.email.from_addr, password)
        server.send_message(msg)
    log.info("digest sent")


# ---------- entry point ----------

def run(
    cfg: Config,
    *,
    today: str | None = None,
    dry_run: bool = False,
) -> tuple[str, dict[int, str]]:
    """Build and send the daily digest email.

    Returns (body_text, numbering_map) for testability. If ``dry_run`` is
    True, builds the email but does not send it.
    """
    today = today or date.today().isoformat()
    conn = connect(cfg.storage.db_path)
    try:
        pages = _today_pages(conn, today)
        new_commitments = _today_commitments(conn, today)
        aging = _aging_commitments(conn, today)
    finally:
        conn.close()

    numbering = DigestNumbering()
    body = _build_body(today, pages, new_commitments, aging, numbering)
    html = _build_html(body)

    if not dry_run:
        subject = f"reMarkable Digest — {today}"
        _send_email(cfg, subject, body, html)

    return body, numbering.mapping
