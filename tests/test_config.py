"""The config loader's contract: shapes, validation, root law."""

from __future__ import annotations

import pytest
from helpers import MIN_CONFIG, write_config


def test_minimal_config_loads(tmp_path):
    cfg = write_config(tmp_path, MIN_CONFIG)
    assert "draft" in cfg.stages
    assert cfg.stages["draft"].agents == ("writer-a",)
    assert cfg.stages["draft"].mode == "single"


def test_root_law_config_parents(tmp_path):
    cfg = write_config(tmp_path, MIN_CONFIG)
    assert cfg.root == cfg.path.resolve().parents[1]
    assert cfg.root.name == tmp_path.resolve().name


def test_documentation_stages_are_not_executable(tmp_path):
    raw = MIN_CONFIG + """
  handoff:
    to: human-gate
  human-gate:
    human: editor
    decision: [accept, park]
"""
    cfg = write_config(tmp_path, raw)
    # handoff/human-gate declare no agents: documentation, not executable stages
    assert set(cfg.stages) == {"draft"}


def test_routes_and_gates_loaded(tmp_path):
    raw = MIN_CONFIG + """
gates:
  dash-gate:
    type: codepoints
    codepoints: ["U+2014"]
agent_routes:
  writer-a:
    model: model-a
    endpoint_key: primary
    max_tokens: 4000
    byte_exact: true
"""
    cfg = write_config(tmp_path, raw)
    assert "dash-gate" in cfg.gate_defs
    assert cfg.agent_routes["writer-a"]["byte_exact"] is True


def test_apply_spec_default(tmp_path):
    cfg = write_config(tmp_path, MIN_CONFIG)
    spec = cfg.raw_apply_spec()
    assert spec["key"] == "stage"
    assert spec["values"]["accept"] == "editing"


def test_apply_spec_override_from_config(tmp_path):
    raw = MIN_CONFIG + """
defaults:
  apply:
    key: status
    values:
      accept: published
"""
    cfg = write_config(tmp_path, raw)
    assert cfg.raw_apply_spec()["values"]["accept"] == "published"


def test_broken_yaml_fatals(tmp_path):
    import yaml

    with pytest.raises((SystemExit, yaml.YAMLError)):
        write_config(tmp_path, "key: [unclosed")
