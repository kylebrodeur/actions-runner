"""Route + auth resolution contracts (the config-data routing model)."""

from __future__ import annotations

import pytest
from helpers import write_config

from runner.config import resolve_auth_token


def test_direct_endpoint_resolution(tmp_path):
    cfg = write_config(tmp_path, """
defaults:
  auth:
    bearer_env: TOKEN_ENV
agent_routes:
  writer:
    model: model-a
    endpoint: http://direct.example/v1
    max_tokens: 4000
""")
    url, model, meta, env = cfg.resolve_route("writer")
    assert url == "http://direct.example/v1"
    assert model == "model-a"
    assert env == "TOKEN_ENV"


def test_endpoint_key_resolution(tmp_path):
    cfg = write_config(tmp_path, """
defaults:
  endpoints:
    deep: http://deep.example/v1
    fast: http://fast.example/v1
agent_routes:
  writer:
    model: model-a
    endpoint_key: deep
  sprinter:
    model: model-b
    endpoint_key: fast
    auth_env: OTHER_ENV
""")
    assert cfg.resolve_route("writer")[0] == "http://deep.example/v1"
    url, _, _, env = cfg.resolve_route("sprinter")
    assert url == "http://fast.example/v1"
    assert env == "OTHER_ENV"  # seat-explicit auth wins


def test_unresolvable_route_fatals(tmp_path):
    cfg = write_config(tmp_path, """
agent_routes:
  ghost:
    model: m
    endpoint_key: nowhere
""")
    with pytest.raises(SystemExit):
        cfg.resolve_route("ghost")


def test_missing_route_fatals(tmp_path):
    cfg = write_config(tmp_path, "agent_routes: {}\n")
    with pytest.raises(SystemExit):
        cfg.resolve_route("nonexistent")


def test_default_auth_env_name(tmp_path):
    """The auth chain: the seat's auth_env, then the default bearer_env,
    then the runner default."""
    cfg = write_config(tmp_path, """
defaults:
  auth:
    bearer_env: CHAIN_ENV
agent_routes:
  seat:
    model: model-a
    endpoint: http://seat.example/v1
""")
    # No auth_env on the seat: the default bearer_env applies.
    assert cfg.resolve_route("seat")[3] == "CHAIN_ENV"

    # The default bearer_env is itself absent: the runner default applies.
    bare = write_config(tmp_path, """
agent_routes:
  seat:
    model: model-a
    endpoint: http://seat.example/v1
""")
    assert bare.resolve_route("seat")[3] == "RUNNER_TOKEN"

    # An explicit token beats the environment.
    assert resolve_auth_token("CHAIN_ENV", explicit_token="x") == "x"


def test_seat_auth_env_beats_the_default(tmp_path):
    """A seat's own auth_env wins over the default bearer_env."""
    cfg = write_config(tmp_path, """
defaults:
  auth:
    bearer_env: CHAIN_ENV
agent_routes:
  seat:
    model: model-a
    endpoint: http://seat.example/v1
    auth_env: SEAT_ENV
""")
    assert cfg.resolve_route("seat")[3] == "SEAT_ENV"


def test_resolve_auth_explicit_wins(monkeypatch):
    monkeypatch.setenv("E1", "from-env")
    assert resolve_auth_token("E1", explicit_token="explicit") == "explicit"
    assert resolve_auth_token("E1") == "from-env"


def test_resolve_auth_missing_fatals(monkeypatch):
    monkeypatch.delenv("MISSING_ENV", raising=False)
    with pytest.raises(SystemExit):
        resolve_auth_token("MISSING_ENV")
