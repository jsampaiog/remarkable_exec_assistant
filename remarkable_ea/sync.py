"""Component 1 — Sync.

Pulls whitelisted notebooks from the reMarkable cloud via rmapi, unpacks
each ``.rmdoc`` archive, converts new pages to PNG via rmc, and writes them
to the pages dir.

Idempotency model: a PNG is written at a deterministic path
``pages/<notebook_id>/<page_uuid>_<captured_date>.png``. If that file
already exists on disk, the page is treated as already synced and skipped.
This makes re-running the same day a no-op if nothing changed, without
requiring us to trust any remote mtimes.

The ``rmapi`` and ``rmc`` dependencies are injected so tests can replace
them with fakes; by default they are built from ``cfg``.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Protocol

from remarkable_ea.config import Config, NotebookConfig
from remarkable_ea.rmapi import RmapiClient
from remarkable_ea.rmc import RmcConverter
from remarkable_ea.store import connect

log = logging.getLogger(__name__)


class _RmapiLike(Protocol):
    def download(self, remote_path: str, dest_dir: Path) -> Path: ...


class _RmcLike(Protocol):
    def convert(self, rm_file: Path, dest_png: Path) -> None: ...


def run(
    cfg: Config,
    *,
    rmapi: _RmapiLike | None = None,
    rmc: _RmcLike | None = None,
) -> list[Path]:
    """Sync every whitelisted notebook. Returns PNG paths created this run."""
    rmapi = rmapi or RmapiClient(cfg.remarkable.rmapi_path)
    rmc = rmc or RmcConverter()

    cfg.storage.pages_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(cfg.storage.db_path)
    created: list[Path] = []
    try:
        for notebook in cfg.remarkable.notebooks:
            try:
                created.extend(_sync_notebook(notebook, cfg, rmapi, rmc, conn))
            except Exception as exc:  # noqa: BLE001 - isolate per-notebook failures
                log.error(
                    "sync failed for notebook %s (%s): %s",
                    notebook.name,
                    notebook.path,
                    exc,
                )
    finally:
        conn.close()
    return created


def _sync_notebook(
    notebook: NotebookConfig,
    cfg: Config,
    rmapi: _RmapiLike,
    rmc: _RmcLike,
    conn,
) -> list[Path]:
    log.info("syncing notebook %s (%s)", notebook.name, notebook.path)

    with tempfile.TemporaryDirectory(prefix="remarkable-ea-") as tmp:
        tmp_path = Path(tmp)
        archive = rmapi.download(notebook.path, tmp_path)

        unpacked = tmp_path / "unpacked"
        _unpack(archive, unpacked)

        nb_id = notebook_id(notebook)
        nb_pages_dir = cfg.storage.pages_dir / nb_id
        nb_pages_dir.mkdir(parents=True, exist_ok=True)

        created: list[Path] = []
        for rm_page in _iter_pages(unpacked):
            captured = _captured_date(rm_page)
            dest = nb_pages_dir / f"{rm_page.stem}_{captured}.png"
            if dest.exists():
                log.debug("skip existing %s", dest.name)
                continue
            try:
                rmc.convert(rm_page, dest)
            except Exception as exc:  # noqa: BLE001
                log.error("rmc failed on %s: %s", rm_page.name, exc)
                continue
            log.info("wrote %s", dest)
            created.append(dest)

    _update_sync_state(conn, notebook_id(notebook))
    return created


def notebook_id(notebook: NotebookConfig) -> str:
    """Stable, filesystem-safe directory name for a notebook.

    Derived from the rmapi path so it doesn't change when the operator
    edits the ``name`` field in config.yaml.
    """
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", notebook.path.strip("/"))
    return sanitized or "root"


def _unpack(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)


def _iter_pages(unpacked: Path) -> Iterator[Path]:
    yield from sorted(unpacked.rglob("*.rm"))


def _captured_date(rm_page: Path) -> str:
    """Infer the captured date (YYYY-MM-DD) for a page.

    Preference order:
        1. Sibling ``<page>-metadata.json`` ``lastModified`` field (ms epoch).
        2. The file's own mtime.
    """
    meta_path = rm_page.with_name(rm_page.stem + "-metadata.json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            ts = meta.get("lastModified")
            if ts is not None:
                ms = int(ts)
                return (
                    datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
                    .date()
                    .isoformat()
                )
        except (ValueError, OSError, json.JSONDecodeError):
            pass
    return (
        datetime.fromtimestamp(rm_page.stat().st_mtime, tz=timezone.utc)
        .date()
        .isoformat()
    )


def _update_sync_state(conn, notebook_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO sync_state(notebook_id, last_synced_at) VALUES (?, ?)"
        " ON CONFLICT(notebook_id) DO UPDATE SET last_synced_at=excluded.last_synced_at",
        (notebook_id, now),
    )
    conn.commit()
