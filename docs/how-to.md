<!-- STATUS: VERIFIED 2026-10-22 against runner/*.py (37 tests passing); every error string in RUNBOOK.md is copied from the source or captured live. -->
# How-to: day-to-day operation

Task-shaped answers for the things you do repeatedly once the runner is
installed. The one-time setup is [quickstart.md](quickstart.md); every
flag, config key, and error is indexed in [RUNBOOK.md](RUNBOOK.md).

## Run the gates over a change, for free

Before spending anything, or in CI:

```bash
uv run arun gate configs/demo.yml dash-gate README.md docs/*.md
```

```text
  gate dash-gate: ok
```

A file with a forbidden character comes back with `(file, line, col)`
findings and exit 1. Gates scan with fenced code blocks stripped, so
shell flags like `--` inside fences do not false-positive a dash gate.

## Run one stage against one seat

```bash
RUNNER_TOKEN=<your-model-token> uv run arun run configs/demo.yml draft --brief brief.md
```

```text
  gate dash-gate: ok
draft: 0.1s -> draft.md
```

Per-seat prompts live in the route (`prompts.<stage>`); the fill slots
are `{brief}`, `{other}`, `{draft}`, and the pass name. Add per-agent
inputs as `--input <agent>=<file>` (repeatable):

```bash
RUNNER_TOKEN=<your-model-token> uv run arun run configs/demo.yml counterparty --brief brief.md --input writer-deep=draft-1.md --input writer-fast=draft-2.md
```

## Read what actually happened

Everything a run did is in the worklog (`worklog.local.jsonl` unless
`defaults.logging.worklog` says otherwise) and the artifacts dir
(`artifacts.local/`, or `artifacts.local/<slug>/` per piece when the run
carries a `--brief`):

```bash
python3 -c "import json; [print(json.loads(l)['event'], json.loads(l).get('agent','')) for l in open('worklog.local.jsonl')]"
```

## Decide a human gate

Accept (bump the machine field, write the accepted copy, original
untouched):

```bash
uv run arun run configs/demo.yml draft --decision accept --brief piece.md
```

```text
frontmatter stage bumped to editing (accepted copy written beside the piece); prose untouched
```

Park (record only, touch nothing):

```bash
uv run arun run configs/demo.yml draft --decision park --brief piece.md
```

```text
parked; frontmatter untouched
```

Redraft (route back; the runner never iterates past a human gate):

```bash
uv run arun run configs/demo.yml draft --decision redraft --brief piece.md
```

```text
redraft: re-run the earlier stage with the review artifacts' advice items as material (the runner doesn't auto-iterate past a human gate)
```

## Handle a cold model server

A server scaled to zero times out or accepts-then-hangs while weights
load. The transport retries on that signature with a 90-second backoff,
printed live:

```text
  ConnectError (server cold or slow); retry 1/2 in 90s
```

Wake the server between retries if you can; after the last retry the run
exits with:

```text
endpoint http://127.0.0.1:9999/v1 did not answer after 3 attempts
```

Re-run the same command once the server is warm; gates run again and
cost nothing.

## Handle a reasoning model that returns no prose

Empty content with `finish == "length"` did not fail; the model ran out
of budget thinking. The executor re-issues the SAME call at `factor`
times the budget (`budget_escalation` per seat, default factor 4, one
round), printing:

```text
  reasoning 7982c ate the budget; escalating to 32000 tokens
```

For reasoning-first seats, budget up front instead: the example config
sets `max_tokens: 8000` for a craft seat because those models burn 4-20K
chars on reasoning before prose.

## Wire the text gates into another repo's CI

Vendor the kit and the composite (cross-repo `uses:` to a private kit is
not supported):

1. copy `actions-runner/` into `<repo>/tools/actions-runner/`
2. copy `actions/text-gates/` into `<repo>/.github/actions/text-gates/`
3. add a workflow step shaped like `actions/example-workflow.yml`:

```yaml
- uses: ./tools/actions-runner/actions/text-gates
  with:
    config: actions/pipeline.yml
    gate: dash-gate
    files: ${{ steps.files.outputs.list }}
```

A miss in kit resolution fails LOUD with `::error::`, never a swallowed
failure. The gate step needs no secrets and makes no model calls.

## Extend behavior without forking

Register hooks from a host script instead of editing the runner:

```python
from runner.hooks import hooks

@hooks.register("before-call")
def shape(config, agent_id, body, meta):
    body["top_p"] = 0.95
    return body
```

`before-stage` refusals fail the stage; before/after-call and after-stage
errors are contained per call and recorded in the worklog as
`hook-error` events. One extension system only: routing that needs code
is a before-call hook; gating that needs code is a custom gate type
registered through the hook system.

## Ship the machinery into a consumer project

The supported delivery path is vendoring, not a dependency pin:

```bash
cp -r actions-runner/ <consumer-project>/tools/actions-runner/
cp -r actions/text-gates/ <consumer-project>/.github/actions/text-gates/
```

Changes belong upstream first; the consumer re-vendors at the new tag.
The kit is deliberately decoupled: no project names, prompts, gate
vocabularies, or paths ride in the code (the extraction contract in
[../SYSTEM.md](../SYSTEM.md)).