"""Worklog + artifacts store.

Every model call gets a JSONL record; every stage artifact lands in a
per-piece directory so multi-piece runs never overwrite each other.

Layout (config-driven):
    <logging.worklog>          JSONL (append; one record per request/event)
    <logging.artifacts>/       shared artifact dir (no slug) or legacy root
    <logging.artifacts>/<slug>/  per-piece artifacts when the CLI carries a piece
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class Worklog:
    """Append-only JSONL + artifact writer bound to one run root."""

    def __init__(self, config: dict[str, Any], root: Path, slug: str | None = None):
        logging = config.get("logging") or {}
        rel_worklog = str(logging.get("worklog") or "worklog.local.jsonl")
        rel_artifacts = str(logging.get("artifacts") or "artifacts.local/")
        self.path = root / rel_worklog
        self.path.parent.mkdir(parents=True, exist_ok=True)
        art = root / rel_artifacts
        # Per-piece artifacts: no slug = the shared legacy dir (back-compat);
        # a slug gives that piece its own artifacts folder so multi-piece
        # runs never overwrite each other's outputs.
        self.art_root = art / slug if slug else art
        self.art_root.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        """Append one record with a recorded_at timestamp; never raise into a stage."""
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"recorded_at": time.time(), **record}, ensure_ascii=False) + "\n")

    def artifact(self, name: str, content: str) -> Path:
        """Write (overwrite) one artifact; returns its path."""
        p = self.art_root / name
        p.write_text(content, encoding="utf-8")
        return p
