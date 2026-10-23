<!-- STATUS: VERIFIED 2026-10-22 against runner/cli.py + runner/stages.py + runner/apply.py (37 tests passing). The fake-endpoint example was executed as written; its output is copied verbatim. -->
# Quickstart: from nothing to a finished stage

The numbered path: clone, install, verify the CLI, run gates for free,
then one real model stage. Every command is copy-pasteable except the two
marked otherwise (they need your endpoint and token).

**What you will have at the end:** a pipeline config you understand line
by line, a gate run that cost zero tokens, one executed model stage with
a JSONL worklog record and an artifact file, and a human-gate decision
applied.

## 0. What this is not

Not a hosted service and not a CI runner for model stages. The runner
executes **locally** against OpenAI-compatible endpoints you control.
CI runs only the deterministic text gates (no model calls, no secrets).

## 1. Clone and install

```bash
git clone https://github.com/kylebrodeur/actions-runner.git
cd actions-runner
uv sync
```

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

## 2. Confirm the CLI

```bash
uv run arun --help
```

```text
usage: arun [-h] {run,gate,show} ...

Config-driven local runner for agentic pipelines

positional arguments:
  {run,gate,show}
    run            execute one declared stage
    gate           standalone gate run over files
    show           print the parsed pipeline
```

## 3. Read one pipeline config

`examples/example-pipeline.yml` is a complete example (two writer seats,
a reviewer, a dash gate, and the human-gate vocabulary). Print the parsed
form:

```bash
uv run arun show examples/example-pipeline.yml
```

```text
pipeline: examples/example-pipeline.yml (root /Users/you/workspace/actions-runner)
stages:
  draft: agents=['writer-deep', 'writer-fast'] mode=parallel gates=['dash-gate']
  counterparty: agents=['writer-deep', 'writer-fast'] mode=cross gates=[]
  review: agents=['reviewer'] mode=sequential-passes gates=['dash-gate']
routes:
  writer-deep: model=deep-model-13b endpoint=deep-server max_tokens=8000
  writer-fast: model=fast-model-7b endpoint=fast-server max_tokens=2000
  reviewer: model=reviewer-32b endpoint=https://api.example/v1 max_tokens=4000
apply: {'key': 'stage', 'values': {'accept': 'editing'}, 'copy_suffix': '.accepted.md', 'wins_over_prose': True}
```

That output is the whole mental model in one screen: stages, routes, and
the human-gate apply vocabulary.

## 4. Run gates for free (zero tokens)

Gates are deterministic: no model calls, no token spend.

```bash
uv run arun gate examples/example-pipeline.yml dash-gate examples/example-pipeline.yml
```

```text
  gate dash-gate: ok
```

The config carries the dash gate (`U+2014` / `U+2013`), so the runner
itself passes its own gate. Run it against a file with an em dash and it
goes red:

```bash
uv run arun gate examples/example-pipeline.yml dash-gate README.md
```

```text
GATE dash-gate: FAIL
  - README.md:19:20 codepoint-0 (U+2014): behavior with hooks... forks needed.
```

(Finding shown trimmed; the real line reads `codepoint-0 (U+2014)` with the
line text.) Exit code 1, `README.md` line 19 was an em dash, and no token
was spent finding out. Fix the text, not the gate.

## 5. Build a minimal pipeline of your own

Make the workspace and a config:

```bash
mkdir -p /tmp/arun-demo/configs
```

Write `configs/demo.yml` (one `single` seat, one gate):

```yaml
version: 1

defaults:
  primary: scribe
  auth:
    bearer_env: RUNNER_TOKEN

pipeline:
  draft:
    agents: [scribe]
    mode: single
    gates: [dash-gate]

  handoff:
    to: human-gate

gates:
  dash-gate:
    type: codepoints
    codepoints: ["U+2014", "U+2013"]

agent_routes:
  scribe:
    model: demo-model
    endpoint: http://127.0.0.1:9999/v1
    max_tokens: 500
    temperature: 0.5
    prompts:
      draft: >-
        Write the piece from this brief:\n\n{brief}
```

And a brief:

```text
# Demo brief

Three things the runner does, and why each matters this week.
```

(saved as `/tmp/arun-demo/brief.md`; `<your-model-token>` below is a
placeholder you replace with your real bearer token before running.)

## 6. Run one stage

From `/tmp/arun-demo`, with a model server answering at
`http://127.0.0.1:9999/v1` (the example above points at the loopback; use
your own OpenAI-compatible endpoint):

```bash
RUNNER_TOKEN=<your-model-token> uv run --project <path-to-kit> arun run configs/demo.yml draft --brief brief.md
```

Real output from an executed run against a local fake endpoint:
```text
  gate dash-gate: ok
draft: 0.1s -> draft.md
```

What happened, in order:

1. the gate ran first and passed (a red gate would have refused here,
   before any spend)
2. one chat call went to the seat's endpoint, streamed, and captured
   `reasoning + content`
3. the artifact landed under `artifacts.local/` (the run root is the
   config's parent-of-parent; see the root law in `docs/RUNBOOK.md`)
4. the worklog got its record:

```bash
head -1 worklog.local.jsonl
```

```json
{"recorded_at": 1792685406.851545, "event": "draft", "agent": "scribe", "mode": "single", "finish": "stop", "elapsed_seconds": 0.1, "reasoning_len": 0, "content_len": 25}
```

## 7. Apply a human-gate decision

The human gate is yours. When you look at a finished piece and accept it,
the runner bumps only the machine field and writes an accepted COPY; the
prose body is never edited by the runner.

Give the piece frontmatter first:

```yaml
---
title: Demo
stage: drafting
---

# Demo piece

A short piece body.
```

(saved as `/tmp/arun-demo/piece.md`)

```bash
uv run --project <path-to-kit> arun run configs/demo.yml draft --decision accept --brief piece.md
```

```text
frontmatter stage bumped to editing (accepted copy written beside the piece); prose untouched
```

Confirm what changed:

```bash
ls piece*
```

```text
piece.accepted.md
piece.md
```

The original `piece.md` is untouched. `piece.accepted.md` carries the
bump (`stage: editing`). The decision also landed in the worklog:
`{"event": "human-gate", "decision": "accept", "frontmatter_updates": {"stage": "editing"}}`.

## What is PLANNED (not shipped)

`uv tool install actions-runner` and the published PyPI package are
PLANNED; until the first release tag exists, install from a checkout or
vendored copy (the delivery model in `SYSTEM.md`). Everything above
except that install line runs today.

## Where next

- Day-to-day operation: [how-to.md](how-to.md)
- Every flag, config key, error string, and recovery procedure:
  [RUNBOOK.md](RUNBOOK.md)
- The full system and its laws: [../SYSTEM.md](../SYSTEM.md)