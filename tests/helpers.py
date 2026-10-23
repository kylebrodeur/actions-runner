"""The pipeline config's contract."""

from __future__ import annotations

from pathlib import Path

from runner.config import PipelineConfig, load_pipeline  # noqa: F401


def write_config(tmp_path: Path, raw: str, name: str = "pipeline.yml") -> PipelineConfig:
    p = tmp_path / "proj" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw)
    return load_pipeline(p)


MIN_CONFIG = """
version: 1
defaults:
  endpoints:
    primary: http://endpoint-a.example/v1
  auth:
    bearer_env: TOKEN_ENV
pipeline:
  draft:
    agents: [writer-a]
    mode: single
agent_routes:
  writer-a:
    model: model-a
    endpoint_key: primary
    max_tokens: 4000
    temperature: 0.8
"""
