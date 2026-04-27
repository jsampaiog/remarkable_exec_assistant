"""Component 2 — Extract.

Calls Claude Sonnet vision once per page PNG and returns a structured
extraction result. The extractor never decides to schedule anything — it
only reports what the notes say. Decisions happen downstream in the digest.

The returned schema is defined by ``PageExtraction`` and its sub-types.
All fields map 1-to-1 to the spec's JSON schema. When the model is
uncertain about a commitment, it lowers the ``confidence`` score rather
than omitting it.

The ``extract_page()`` function is the unit of work. ``run()`` processes
every un-extracted PNG in the pages dir and persists results to the store.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from remarkable_ea.config import Config, NotebookConfig, anthropic_api_key

log = logging.getLogger(__name__)


# ---------- extraction schema ----------

@dataclass
class Commitment:
    id: str
    who: str           # "self" or a person name
    what: str
    due: str | None    # YYYY-MM-DD or null
    confidence: float  # 0.0–1.0


@dataclass
class MeetingToSchedule:
    with_whom: str     # person or group ("with" is a Python keyword)
    topic: str
    proposed_when: str | None   # natural language, e.g. "next Tuesday afternoon"
    duration_minutes: int | None


@dataclass
class PageExtraction:
    page_id: str
    notebook: str
    captured_date: str  # YYYY-MM-DD
    summary: str
    commitments: list[Commitment] = field(default_factory=list)
    meetings_to_schedule: list[MeetingToSchedule] = field(default_factory=list)
    questions_raised: list[str] = field(default_factory=list)
    people_mentioned: list[str] = field(default_factory=list)
    references_past: str | None = None


# ---------- prompt ----------

SYSTEM_PROMPT = """\
You are reading a photograph of a handwritten page from an executive's \
personal reMarkable notebook. Your job is to extract structured intelligence \
from it.

Rules:
1. Extract ONLY what is explicitly written — never invent commitments, \
meetings, or facts that are not on the page.
2. When you are unsure whether something is a commitment, include it but \
lower the confidence score (0.0–1.0). Never omit a plausible commitment.
3. "who": "self" means the executive (J) committed to do something. \
Any other name means that person owes J something.
4. For meetings_to_schedule: report what the notes say verbatim. You do NOT \
decide whether to schedule anything — only extract.
5. Keep the summary to 2–3 sentences of plain language.
6. People's names should be normalised to their most complete form as written \
on the page (e.g. "Maria S." not "M").
7. If the page references something from an earlier page or a past \
conversation, note it briefly in references_past.

The notebook's type hint is provided for context — use it to calibrate your \
expectations (e.g. "daily_log" pages may have a mix of tasks and reflections; \
"meeting_notes" pages are structured around a specific meeting).
"""

USER_TEMPLATE = """\
Notebook: {notebook_name}
Notebook type: {notebook_type}
Page ID: {page_id}
Captured date: {captured_date}

Please extract the structured information from this handwritten page.

Respond with a SINGLE JSON object matching this schema exactly — no \
commentary, no markdown fences, just raw JSON:

{{
  "page_id": "{page_id}",
  "notebook": "{notebook_name}",
  "captured_date": "{captured_date}",
  "summary": "2-3 sentence summary",
  "commitments": [
    {{
      "id": "random-uuid",
      "who": "self | person name",
      "what": "free text",
      "due": "YYYY-MM-DD | null",
      "confidence": 0.0-1.0
    }}
  ],
  "meetings_to_schedule": [
    {{
      "with": "person or group",
      "topic": "free text",
      "proposed_when": "natural language | null",
      "duration_minutes": "integer | null"
    }}
  ],
  "questions_raised": ["free text"],
  "people_mentioned": ["names"],
  "references_past": "free text | null"
}}
"""


# ---------- extraction ----------

def extract_page(
    png_path: Path,
    page_id: str,
    notebook_name: str,
    notebook_type: str,
    captured_date: str,
    *,
    model: str = "claude-sonnet-4-6",
    api_key: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> PageExtraction:
    """Run Claude vision on a single page PNG and return structured data."""
    client = client or anthropic.Anthropic(api_key=api_key or anthropic_api_key())

    image_data = base64.b64encode(png_path.read_bytes()).decode("utf-8")
    media_type = "image/png"

    user_text = USER_TEMPLATE.format(
        notebook_name=notebook_name,
        notebook_type=notebook_type,
        page_id=page_id,
        captured_date=captured_date,
    )

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": user_text},
                ],
            }
        ],
    )

    raw_text = response.content[0].text.strip()
    # Strip markdown fences if the model wraps the JSON despite instructions.
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1]
        if raw_text.endswith("```"):
            raw_text = raw_text[: raw_text.rfind("```")]
        raw_text = raw_text.strip()

    data = json.loads(raw_text)
    return _parse_extraction(data)


def _parse_extraction(data: dict) -> PageExtraction:
    """Parse the raw JSON dict into typed dataclasses.

    Lenient: missing optional fields get defaults, unknown keys are ignored.
    """
    commitments = [
        Commitment(
            id=c.get("id", str(uuid.uuid4())),
            who=c["who"],
            what=c["what"],
            due=c.get("due"),
            confidence=float(c.get("confidence", 0.5)),
        )
        for c in data.get("commitments", [])
    ]
    meetings = [
        MeetingToSchedule(
            with_whom=m.get("with", m.get("with_whom", "")),
            topic=m.get("topic", ""),
            proposed_when=m.get("proposed_when"),
            duration_minutes=_int_or_none(m.get("duration_minutes")),
        )
        for m in data.get("meetings_to_schedule", [])
    ]
    return PageExtraction(
        page_id=data["page_id"],
        notebook=data["notebook"],
        captured_date=data["captured_date"],
        summary=data.get("summary", ""),
        commitments=commitments,
        meetings_to_schedule=meetings,
        questions_raised=data.get("questions_raised", []),
        people_mentioned=data.get("people_mentioned", []),
        references_past=data.get("references_past"),
    )


def extraction_to_json(ext: PageExtraction) -> str:
    """Serialize a PageExtraction to JSON for storage in the raw_json column."""
    d = asdict(ext)
    for m in d.get("meetings_to_schedule", []):
        m["with"] = m.pop("with_whom")
    return json.dumps(d, indent=2)


def _int_or_none(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ---------- persistence ----------

def save_extraction(conn: sqlite3.Connection, ext: PageExtraction) -> bool:
    """Persist a PageExtraction to the store.

    Inserts the page row, all commitments (status='open'), and all meeting
    proposals (status='pending') in a single transaction. If the page_id
    already exists the call is a no-op (returns False).
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        conn.execute(
            "INSERT INTO pages(page_id, notebook, captured_date, summary, raw_json, extracted_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                ext.page_id,
                ext.notebook,
                ext.captured_date,
                ext.summary,
                extraction_to_json(ext),
                now,
            ),
        )
    except sqlite3.IntegrityError:
        return False

    for c in ext.commitments:
        conn.execute(
            "INSERT INTO commitments"
            "(id, page_id, who, what, due, confidence, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
            (c.id, ext.page_id, c.who, c.what, c.due, c.confidence, now),
        )
    for m in ext.meetings_to_schedule:
        conn.execute(
            "INSERT INTO meetings_proposed"
            "(id, page_id, with_whom, topic, proposed_when, duration_minutes, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                str(uuid.uuid4()),
                ext.page_id,
                m.with_whom,
                m.topic,
                m.proposed_when,
                m.duration_minutes,
                now,
            ),
        )
    conn.commit()
    return True


# ---------- batch entry point (used by CLI / run_daily) ----------

def run(cfg: Config) -> list[PageExtraction]:
    """Extract all un-processed PNGs and persist results to the store.

    Scans the pages dir, skips pages already in the store, calls Claude
    vision on the rest, and saves each extraction immediately so partial
    progress survives crashes.
    """
    from remarkable_ea.store import connect

    conn = connect(cfg.storage.db_path)
    existing_ids = {
        row[0] for row in conn.execute("SELECT page_id FROM pages")
    }

    client = anthropic.Anthropic(api_key=anthropic_api_key())
    results: list[PageExtraction] = []

    try:
        for notebook in cfg.remarkable.notebooks:
            from remarkable_ea.sync import notebook_id

            nb_dir = cfg.storage.pages_dir / notebook_id(notebook)
            if not nb_dir.is_dir():
                continue
            for png in sorted(nb_dir.glob("*.png")):
                page_id, captured_date = _parse_png_filename(png.stem)
                if page_id in existing_ids:
                    log.debug("skip already-extracted %s", page_id)
                    continue
                log.info("extracting %s from %s", page_id, notebook.name)
                try:
                    ext = extract_page(
                        png_path=png,
                        page_id=page_id,
                        notebook_name=notebook.name,
                        notebook_type=notebook.type,
                        captured_date=captured_date,
                        model=cfg.claude.model,
                        client=client,
                    )
                    save_extraction(conn, ext)
                    existing_ids.add(page_id)
                    results.append(ext)
                except Exception as exc:  # noqa: BLE001
                    log.error("extraction failed for %s: %s", png.name, exc)
    finally:
        conn.close()

    return results


def _parse_png_filename(stem: str) -> tuple[str, str]:
    """Parse ``<page_uuid>_<YYYY-MM-DD>`` into (page_id, captured_date).

    The date portion is always the last ``_``-delimited segment (``YYYY-MM-DD``
    contains dashes but no underscores), so a single rsplit on ``_`` is safe
    even when the page UUID itself contains dashes.
    """
    parts = stem.rsplit("_", 1)
    if len(parts) == 2 and len(parts[1]) == 10:  # "YYYY-MM-DD" is 10 chars
        return parts[0], parts[1]
    return stem, "unknown"
