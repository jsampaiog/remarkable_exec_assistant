"""Configuration loader.

Parses ``config.yaml`` into a frozen ``Config`` dataclass. Secrets are NOT
stored in the dataclass; they are read from environment variables on demand
via ``anthropic_api_key()`` and ``gmail_app_password()``. This keeps
``load_config`` callable in tests and during scaffolding without the secrets
being set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class NotebookConfig:
    # The rmapi path to the notebook, e.g. "/Notes/Daily Log". The spec
    # describes this as a UUID, but rmapi's CLI is path-based and does not
    # accept raw cloud UUIDs, so we store the path instead.
    path: str
    name: str
    type: str  # daily_log | meeting_notes | strategy | scratch


@dataclass(frozen=True)
class RemarkableConfig:
    rmapi_path: str
    notebooks: tuple[NotebookConfig, ...]


@dataclass(frozen=True)
class ClaudeConfig:
    model: str


@dataclass(frozen=True)
class EmailConfig:
    smtp_host: str
    smtp_port: int
    imap_host: str
    imap_port: int
    from_addr: str
    to_addr: str


@dataclass(frozen=True)
class StorageConfig:
    db_path: Path
    pages_dir: Path


@dataclass(frozen=True)
class Config:
    remarkable: RemarkableConfig
    claude: ClaudeConfig
    email: EmailConfig
    storage: StorageConfig


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def load_config(path: str | Path) -> Config:
    """Load and parse a config.yaml file. Raises on missing required keys."""
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"Config at {path} is not a mapping")

    rm = raw["remarkable"]
    notebooks = tuple(
        NotebookConfig(path=n["path"], name=n["name"], type=n["type"])
        for n in rm.get("notebooks", [])
    )

    claude = raw["claude"]
    email = raw["email"]
    storage = raw["storage"]

    return Config(
        remarkable=RemarkableConfig(
            rmapi_path=rm.get("rmapi_path", "rmapi"),
            notebooks=notebooks,
        ),
        claude=ClaudeConfig(model=claude["model"]),
        email=EmailConfig(
            smtp_host=email["smtp_host"],
            smtp_port=int(email["smtp_port"]),
            imap_host=email.get("imap_host", "imap.gmail.com"),
            imap_port=int(email.get("imap_port", 993)),
            from_addr=email["from"],
            to_addr=email["to"],
        ),
        storage=StorageConfig(
            db_path=_expand(storage["db_path"]),
            pages_dir=_expand(storage["pages_dir"]),
        ),
    )


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def anthropic_api_key() -> str:
    return _require_env("ANTHROPIC_API_KEY")


def gmail_app_password() -> str:
    return _require_env("GMAIL_APP_PASSWORD")
