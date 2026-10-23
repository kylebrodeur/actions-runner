"""Pipeline config loader.

One YAML file declares the whole pipeline. Schema (evolved from the
writing-duo actions/writing-duo.yml, generalized):

    version: 1

    defaults:
      primary: <agent-id>              # the anchor seat (stages may fall back to it)
      endpoints:                       # the address book: named endpoint roots
        <name>: <base-url>
      auth:
        bearer_env: <ENV_VAR>          # default token env (chain: seat auth_env > this)

      temperatures:
        <stage>: <float>               # stage-default temperatures
      concurrency: <int>               # parallel workers within a stage

    pipeline:
      <stage-name>:
        agents: [<agent-id>, ...]      # whose routes serve this stage
        mode: parallel | cross | sequential-passes | parallel-consolidate | single
        gates: [<gate-name>, ...]      # deterministic gates BEFORE tokens
        inputs: [...]                  # documentation for humans; the runner passes files explicitly
        outputs: [{artifact: <name>, ...}]
        notes: >                       # free text; the runner ignores it
      handoff / human stages: free-form under `pipeline:` — only stage names
        with `agents:` are executed; everything else is documentation.

    agent_routes:                      # the seat table: model + endpoint + auth + knobs
      <agent-id>:
        model: <model-id>
        endpoint: <base-url>           # direct, or endpoint_key: <defaults.endpoints name>
        auth_env: <ENV_VAR>            # optional; else defaults.auth.bearer_env
        max_tokens: <int>
        temperature: <float>
        ...                            # extra keys ride along as `meta` (byte_exact, voice_transform...)

Gates (deterministic, run before any model call in the stage):
    gates:
      <gate-name>:
        type: codepoints               # fail when any codepoint appears (post fence-strip)
        codepoints: ["U+2014", "U+2013"]
        scope: publish-bound           # documentation; the gate applies to the inputs given

Every field beyond `agents`/`mode` is advisory to the executor — configs
stay one YAML read by any runner version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REQUIRED_KEYS = ("agents",)


@dataclass(frozen=True)
class StageSpec:
    """One declared pipeline stage: agents + mode + optional gates."""

    name: str
    agents: tuple[str, ...]
    mode: str = "single"
    gates: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineConfig:
    """The loaded pipeline: stages + defaults + routes + gate definitions."""

    path: Path
    root: Path  # config.resolve().parents[1] — the runner-of-record root law
    stages: dict[str, StageSpec]
    defaults: dict[str, Any]
    agent_routes: dict[str, dict[str, Any]]
    gate_defs: dict[str, dict[str, Any]]

    def raw_apply_spec(self) -> dict[str, Any]:
        """The `apply:` human-gate vocabulary (machine-field bumps). Declared
        under defaults.apply (or pipeline.<stage>.apply); empty = accept bumps
        `stage: editing` with a .accepted.md copy (the measured default)."""
        defaults_apply = (self.defaults or {}).get("apply")
        if isinstance(defaults_apply, dict):
            return defaults_apply
        for stage in self.stages.values():
            block = stage.raw.get("apply")
            if isinstance(block, dict):
                return block
        return {"key": "stage", "values": {"accept": "editing"}, "copy_suffix": ".accepted.md"}

    # ------------------------------------------------------------- routing

    def resolve_route(self, agent_id: str) -> tuple[str, str, dict[str, Any], str]:
        """agent-id -> (endpoint_url, model, meta, auth_env_name).

        The route is DATA: a seat's route may be

            agent_routes:
              <agent>:
                model: <model-id>
                endpoint: <base-url>        # direct address, or
                endpoint_key: <name>        # a name looked up in defaults.endpoints
                auth_env: <ENV_VAR>         # this seat's token env (explicit)

        Auth chain: the seat's `auth_env` -> defaults.auth.bearer_env ->
        the runner default. The endpoint may arrive directly or as a key;
        resolution never mutates config. Raises SystemExit on an
        unresolvable seat (batch tool: fatal, not retry).
        """
        route = dict(self.agent_routes.get(agent_id) or {})
        if not route:
            raise SystemExit(f"agent {agent_id!r}: no route in agent_routes")
        defaults = self.defaults or {}
        endpoints = defaults.get("endpoints") or {}
        if "endpoint" in route:
            url = str(route["endpoint"])
        elif "endpoint_key" in route:
            key = str(route["endpoint_key"])
            if key not in endpoints:
                raise SystemExit(f"agent {agent_id!r}: endpoint_key {key!r} not in defaults.endpoints")
            url = str(endpoints[key])
        else:
            raise SystemExit(f"agent {agent_id!r}: route names no endpoint/endpoint_key")
        env_name = str(route.get("auth_env") or (defaults.get("auth") or {}).get("bearer_env", "RUNNER_TOKEN"))
        return url, str(route.get("model", "")), route, env_name


def resolve_auth_token(env_name: str, explicit_token: str = "") -> str:
    """The bearer for one call: explicit > environment. Empty env = a fatal
    (batch tool: a missing token is not retryable)."""
    if explicit_token:
        return explicit_token
    import os

    token = os.environ.get(env_name, "").strip()
    if not token:
        raise SystemExit(f"{env_name} must be in the environment for this call")
    return token


def load_pipeline(config_path: Path) -> PipelineConfig:
    """Load + validate the pipeline config. Raises SystemExit on shape errors
    (runner CLIs are batch tools: a bad config is a fatal, not a retry)."""
    config_path = Path(config_path)
    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict):
        raise SystemExit(f"config {config_path} is not a YAML mapping")

    defaults = raw.get("defaults") or {}
    pipeline = raw.get("pipeline") or {}

    stages: dict[str, StageSpec] = {}
    for name, block in pipeline.items():
        if not isinstance(block, dict):
            continue  # scalar/nested documentation blocks (handoff, human-gate) are not stages
        if "agents" not in block:
            continue  # documentation-only stage (human gate): NOT executable
        agents = block.get("agents") or []
        if not agents:
            raise SystemExit(f"stage {name!r}: empty agents list")
        stages[name] = StageSpec(
            name=str(name),
            agents=tuple(str(a) for a in agents),
            mode=str(block.get("mode", "single")),
            gates=tuple(str(g) for g in block.get("gates") or []),
            raw=block,
        )

    agent_routes = raw.get("agent_routes") or {}
    gates = raw.get("gates") or {}

    return PipelineConfig(
        path=config_path,
        root=config_path.resolve().parents[1],
        stages=stages,
        defaults=defaults,
        agent_routes=agent_routes,
        gate_defs=gates,
    )
