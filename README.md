# actions-runner

Config-driven local runner for agentic-actions pipelines: model seats at
named endpoints, declared stages in measured shapes (parallel / cross /
sequential-passes / single / parallel-consolidate), deterministic gates
before any token spend, JSONL worklog + per-piece artifacts, and a
human-gate apply that writes machine state but never prose.

The full system documentation is in [SYSTEM.md](SYSTEM.md); start there
for the pieces, the laws, and the extraction contract. The
[docs/quickstart.md](docs/quickstart.md) is the path from nothing to a
finished stage.

## Family

This kit is a member of the AI Engineering Control Plane (AI ECP)
family: independent, single-purpose repos. Siblings:

- [ecp-toolkit](https://github.com/kylebrodeur/ecp-toolkit) (the `ecp`
  control-plane CLI).
- [repo-guards](https://github.com/kylebrodeur/repo-guards) (the `guv`
  repo-discipline gates).
- [actions-runner](https://github.com/kylebrodeur/actions-runner) (the
  `arun` local agent-pipeline runner).
- [agent-workspace-kit](https://github.com/kylebrodeur/agent-workspace-kit)
  (the manifest-driven role launcher for multi-agent teams).
- [docs-policy-kit](https://github.com/kylebrodeur/docs-policy-kit) (the
  `docpol` documentation policy engine).
- [op-safe](https://github.com/kylebrodeur/op-safe) (the `op://` secrets
  reference resolution).
- [anti-slop-kit](https://github.com/kylebrodeur/anti-slop-kit) (the
  vendored neutral lint policy).

## Install

```bash
uv tool install --from "git+https://github.com/kylebrodeur/actions-runner@v0.1.1" actions-runner
# or run from a checkout: uv run arun ...
```

**The kit installs from GitHub, at a tag.** It is not on PyPI, and the
name is not available there either: PyPI already has an unrelated
project called `actions-runner`, so a bare `uv tool install
actions-runner` would install somebody else's code without saying so.
The family's one registry entry is `ecp-toolkit`, the control plane.
See the family's distribution policy.

The library surface (`from runner import hooks, execute_stage, ...`) is
first-class: hosts import the machinery behind their own CLIs and extend
behavior with hooks; no forks needed.

    arun show  <pipeline.yml>
    arun gate  <pipeline.yml> <gate-name> <files...>
    arun run   <pipeline.yml> <stage> --brief <piece.md>

## In the family

actions-runner is the agent-pipeline kit of the AI Engineering Control
Plane (AI ECP) family, catalogued in the family catalog
(the family catalog (in the private control-plane repo)) alongside its
siblings: [repo-guards](https://github.com/kylebrodeur/repo-guards)
(governance), [ecp-toolkit](https://github.com/kylebrodeur/ecp-toolkit)
(the control plane), [agent-workspace-kit](https://github.com/kylebrodeur/agent-workspace-kit)
(agent teams), [docs-policy-kit](https://github.com/kylebrodeur/docs-policy-kit)
(docs and publication), and
[op-safe](https://github.com/kylebrodeur/op-safe) (the secrets helper).
[llms.txt](llms.txt) is the map of this repo's docs for agents.

[examples/example-pipeline.yml](examples/example-pipeline.yml) is a
complete translated example. Tests: `uv run pytest tests -q`
(check date: 2026-10-24, 38 passing).