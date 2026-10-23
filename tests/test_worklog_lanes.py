"""Worklog's contracts (the routing contracts moved to test_routes.py)."""

from __future__ import annotations

import json

from runner.worklog import Worklog


def test_worklog_jsonl_appends(tmp_path):
    cfg = {"logging": {"worklog": "logs/worklog.jsonl", "artifacts": "artifacts/"}}
    wl = Worklog(cfg, tmp_path, slug="piece-1")
    wl.write({"event": "draft", "content_len": 10})
    wl.write({"event": "draft", "content_len": 20})
    lines = (tmp_path / "logs/worklog.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "draft"
    assert "recorded_at" in json.loads(lines[0])


def test_worklog_per_piece_artifacts_isolated(tmp_path):
    cfg = {"logging": {"worklog": "w.jsonl", "artifacts": "art/"}}
    a = Worklog(cfg, tmp_path, slug="piece-a")
    b = Worklog(cfg, tmp_path, slug="piece-b")
    a.artifact("draft.md", "text a")
    b.artifact("draft.md", "text b")
    assert (a.art_root / "draft.md").read_text() == "text a"
    assert (b.art_root / "draft.md").read_text() == "text b"
    assert (tmp_path / "art/piece-a/draft.md").exists()
    assert (tmp_path / "art/piece-b/draft.md").exists()


def test_worklog_defaults_when_config_empty(tmp_path):
    wl = Worklog({}, tmp_path)
    wl.write({"event": "x"})
    assert (tmp_path / "worklog.local.jsonl").exists()
    assert (tmp_path / "artifacts.local").is_dir()


def test_worklog_overwrite_artifact(tmp_path):
    wl = Worklog({"logging": {"worklog": "w.jsonl", "artifacts": "a/"}}, tmp_path)
    wl.artifact("f.md", "v1")
    wl.artifact("f.md", "v2")
    assert (wl.art_root / "f.md").read_text() == "v2"
