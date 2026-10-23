"""The stage executor + human-gate apply (against a local fake endpoint)."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from helpers import write_config

from runner.apply import apply_gate_decision
from runner.stages import execute_stage
from runner.worklog import Worklog


@dataclass
class FakeConfig:
    defaults: dict = field(default_factory=dict)
    agent_routes: dict = field(default_factory=dict)
    gate_defs: dict = field(default_factory=dict)


class _FakeEndpoint(BaseHTTPRequestHandler):
    """A minimal OpenAI-compatible chat endpoint: returns one content delta."""

    def log_message(self, *a):  # silence the test server
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        max_tokens = body.get("max_tokens", 16)
        model = body.get("model", "?")
        payload = json.dumps({
            "id": "t", "object": "chat.completion.chunk", "choices": [
                {"index": 0, "delta": {"content": f"prose-from-{model}-budget-{max_tokens}"},
                 "finish_reason": "stop"}],
        })
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self.wfile.write(f"data: {payload}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture(scope="module")
def fake_endpoint():
    server = HTTPServer(("127.0.0.1", 0), _FakeEndpoint)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


@pytest.fixture
def env(tmp_path, monkeypatch, fake_endpoint):
    cfg_path = write_config(tmp_path, f"""
defaults:
  primary: writer-a
  endpoints:
    primary: {fake_endpoint}
  auth:
    bearer_env: TOKEN
  concurrency: 2
pipeline:
  draft:
    agents: [writer-a]
    mode: single
  paired:
    agents: [writer-a, writer-b]
    mode: parallel
  passes:
    agents: [reviewer]
    mode: sequential-passes
    passes: [source, voice]
agent_routes:
  writer-a:
    model: model-a
    endpoint_key: primary
    max_tokens: 100
    temperature: 0.5
  writer-b:
    model: model-b
    endpoint_key: primary
    max_tokens: 200
    temperature: 0.5
  reviewer:
    model: model-r
    endpoint_key: primary
    max_tokens: 300
    temperature: 0.2
""")
    monkeypatch.setenv("TOKEN", "test-token")
    cfg = cfg_path
    wl = Worklog({"logging": {"worklog": "w.jsonl", "artifacts": "art/"}}, tmp_path, slug="p")
    return cfg, wl, tmp_path


def test_single_stage(env, capsys):
    cfg, wl, _ = env
    results = execute_stage(cfg, "test-token", wl, cfg.stages["draft"], brief="the brief")
    assert "writer-a" in results
    assert results["writer-a"]["finish"] == "stop"
    assert "prose-from-model-a" in results["writer-a"]["content"]
    out = capsys.readouterr().out
    assert "draft:" in out


def test_parallel_stage_records_each_agent(env):
    cfg, wl, _ = env
    results = execute_stage(cfg, "t", wl, cfg.stages["paired"], brief="b")
    assert set(results) >= {"writer-a", "writer-b"}
    assert "model-b" in results["writer-b"]["content"]


def test_sequential_passes_join(env):
    cfg, wl, _ = env
    results = execute_stage(cfg, "t", wl, cfg.stages["passes"], brief="b")
    assert "## source" in results["reviewer"]["content"]
    assert "## voice" in results["reviewer"]["content"]


def test_worklog_has_records(env):
    cfg, wl, tmp_path = env
    execute_stage(cfg, "t", wl, cfg.stages["draft"], brief="b")
    lines = (tmp_path / "w.jsonl").read_text().strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["event"] == "draft"
    assert "elapsed_seconds" in rec


def test_apply_accept_bumps_machine_field(tmp_path):
    piece = tmp_path / "piece.md"
    piece.write_text("---\ntitle: T\nstage: draft\n---\n\nProse body.\n")
    wl = Worklog({"logging": {"worklog": "w.jsonl", "artifacts": "art/"}}, tmp_path)
    rc = apply_gate_decision("accept", piece, {"key": "stage", "values": {"accept": "editing"},
                                               "copy_suffix": ".accepted.md"}, wl)
    assert rc == 0
    accepted = tmp_path / "piece.accepted.md"
    assert "stage: editing" in accepted.read_text()
    # the ORIGINAL untouched (the hard rule)
    assert "stage: draft" in piece.read_text()
    assert "Prose body." in accepted.read_text() and "stage: editing" not in piece.read_text()


def test_apply_park_touches_nothing(tmp_path):
    piece = tmp_path / "p.md"
    piece.write_text("---\nstage: draft\n---\nbody")
    wl = Worklog({"logging": {"worklog": "w.jsonl", "artifacts": "art/"}}, tmp_path)
    assert apply_gate_decision("park", piece, {}, wl) == 0
    assert not (tmp_path / "p.accepted.md").exists()


def test_apply_refuses_frontmatter_invention(tmp_path):
    piece = tmp_path / "p.md"
    piece.write_text("no frontmatter\n")
    wl = Worklog({"logging": {"worklog": "w.jsonl", "artifacts": "art/"}}, tmp_path)
    with pytest.raises(SystemExit):
        apply_gate_decision("accept", piece, {}, wl)
