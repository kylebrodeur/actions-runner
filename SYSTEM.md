# actions-runner: the system, fully documented

A config-driven local runner for **agentic-actions pipelines**: one YAML
file declares which model seats run, in what order, through which
deterministic gates, and where the human gate sits. The runner executes
the declared stages against OpenAI-compatible endpoints and writes a
worklog + artifacts trail for every call.

CLI: `arun run|gate|show`.

## What problem it solves

Multi-model pipelines (writers, reviewers, critics, utility seats) get
reinvented per project: the stage shapes, the streaming handling, the
cold-boot retries, the budget-escalation policy, the worklog, the
frontmatter-bump gates; all the same every time, but each repo writes
its own runner and each runner drifts. This kit is the machinery once;
per-project configs carry only the persona: seats, prompts, budgets,
gates, gate vocabularies.

## The pieces (and the law each one carries)

### 1. Config (`runner/config.py`): the schema is the contract

One YAML file; stages that declare `agents:` are executable; stage blocks
without `agents` (handoffs, human-gate docs) are documentation the runner
loads and ignores. Schema: see the module docstring + `examples/example-pipeline.yml`
(a writing-shaped pipeline with two writer seats, a reviewer, a dash gate,
and the full apply vocabulary). `arun show <config>` prints the parsed form.

**Root law**: config.resolve().parents[1] is the run root. Artifacts and
worklogs land under the config's OWN tree (relative paths resolve there),
so the config file is the runner-of-record and no absolute cross-repo
paths are needed.

**Route law**: seats are DATA. A seat's agent route names a model, an
endpoint (direct base URL or `endpoint_key` into `defaults.endpoints`),
and optionally its own `auth_env`. Auth chain: seat auth_env >
defaults.auth.bearer_env > the runner default. No routing code branches:
a route resolves to (url, model, meta, env) by pure lookup; custom
routing behavior belongs in hooks (below), not new config grammar.

### 2. Worklog (`runner/worklog.py`): the honesty layer

Every call appends a JSONL record: event, agent/model, elapsed_seconds,
reasoning_len, content_len, finish, hook errors. Artifacts land per-piece
(`artifacts/<slug>/`) so concurrent pieces never overwrite each other.
Nothing in a run is unobservable: any claim a model (or agent) makes about
"what it did" is checkable against the worklog.

### 3. Calls (`runner/calls.py`): the transport, policy-free

`stream_chat` (chat endpoint, `reasoning_content` captured) and
`stream_completion` (completion endpoint, body carries ONLY the knobs the
caller passes: contract surfaces send exactly their contract's knobs).
Both stream (SSE), follow redirects, and retry on cold-boot signatures
(ReadTimeout / ConnectTimeout / ConnectError backoff sized to the boot
window ~90s; an empty stream that never finished is also retryable).

`escalation_retry` is the one policy primitive: empty content + length
finish = the model RAN OUT (reasoning-first models burn 4-20K chars
before prose), not "writer's block". One retry at factor× the budget
(4× measured right); the caller closes over the re-issue.

### 4. Gates (`runner/gates.py`): deterministic refusals before tokens

Gates run BEFORE any model spend in a stage; a red gate refuses to spend.
Built-ins: `codepoints` (forbidden characters, fenced blocks stripped so
shell `--` flags never false-positive) and `fingerprint` (a byte-exact
prompt contract asserts itself at every build; instruction drift refuses
the call instead of silently degrading a trained model). **Unknown gate
types fail closed**; a config referencing an unimplemented gate refuses,
never skips. Custom gate implementations join via the hook system.

### 5. Stage executor (`runner/stages.py`): the four measured shapes

| mode | shape |
|---|---|
| `parallel` | every seat takes the SAME brief concurrently (thread pool sized by `defaults.concurrency`) |
| `cross` | two seats read each OTHER's artifact; informed second takes, never merges |
| `sequential-passes` | one seat runs its declared `passes` in order; per-pass records + a joined artifact |
| `single` | one seat, one call |
| `parallel-consolidate` | seats run concurrently; non-duplicate findings join (content-hash dedupe) |

Per-seat prompt templates live in the route (`prompts.<stage>`; `*` is
the fallthrough) with `{brief}`, `{other}`, `{draft}`, `{pass}` slots:
the persona is config; the executor never hardcodes prose. Every mode
records + writes artifacts identically.

### 6. Hook system (`runner/hooks.py`): the ONE extension surface

| hook | fires |
|---|---|
| `before-stage` | before gates/calls; raising REFUSES the stage |
| `before-call` | threads the outbound body (mutate/shape; return it) |
| `after-call` | postprocesses one result (resample policies, transforms) |
| `after-stage` | post-hoc checks on the results dict (errors contained + recorded) |

```python
from runner.hooks import hooks

@hooks.register("before-call")
def shape(config, agent_id, body, meta):
    body["top_p"] = 0.95          # e.g. a completion-contract knob
    return body
```

Containment law: before-stage refusals fail the stage (loud, before any
spend); before/after-call and after-stage errors are contained per call
and RECORDED in the worklog (`hook-error` events); extensions can
degrade one call, never kill a run silently OR loudly. One extension
system only: routing that needs code = a before-call hook; gating that
needs code = a `gate` hook; no second registries.

### 7. Human-gate apply (`runner/apply.py`): decisions write state, never prose

`arun run <config> <stage> --decision accept --brief piece.md` bumps the
machine field (defaults.apply vocabulary: key, per-decision values, copy
suffix) and writes the accepted COPY beside the piece. The original file
is never modified; missing frontmatter refuses rather than inventing.
`park`/`redraft` are records + hints.

## Running it

```bash
uv run arun show  <pipeline.yml>
uv run arun gate  <pipeline.yml> <gate-name> <files...>
uv run arun run   <pipeline.yml> <stage> --brief <piece.md> \
                  [--input <agent>=<file>] [--token-env RUNNER_TOKEN] \
                  [--decision accept]          # the human-gate executor
```

Tokens come from the environment (the route-auth chain); nothing is
printed, logged, or stored.

## Extraction contract (how this relates to productized instances)

The kit is DECOPULED machinery: no project names, no project prompts, no
project gate vocabularies, no project paths. A productized pipeline (a
repo that has one) keeps its own runner/configs/skills and either (a)
imports this package as library machinery while keeping its CLI surface,
or (b) adopts `arun` with a translated config. Nothing in a consumer repo
is removed by this kit's existence.

## Tests

38 tests: config schema + root law + docs-not-stages + apply-spec;
worklog append/isolation/defaults; route resolution (direct/key/auth
chain/fatals); gates (codepoints/fingerprint/fail-closed/happy); hooks
(registration/decorator/threading/containment); stage executor against a
local fake endpoint (single/parallel/sequential + worklog records); apply
(accept bump + original-untouched, park, refuse-invent).

Run: `uv run pytest tests -q`.

## Actions/CI alignment (the delivery model)

One delivery convention (the same in every kit of this family):

- **Vendored delivery is the supported path**: consumers copy
  `actions-runner/` into `tools/actions-runner/` and
  `actions/text-gates/` into `.github/actions/`. Cross-repo `uses:` refs
  to a private kit are not supported (Actions cannot check out another
  private repo without an access token). This matches the anti-slop
  installer's vendored-not-dependency law.
- **Composite action** ([actions/text-gates/action.yml](actions/text-gates/action.yml)):
  resolves the kit source vendored-first, installs uv-native
  (`astral-sh/setup-uv@v7`, the family CI standard), then
  `python -m runner.cli gate <config> <gate> <files>`; any miss in kit
  resolution fails LOUD (`::error::`): never a swallowed failure followed
  by a confusing command-not-found.
- **What belongs in CI**: deterministic TEXT gates only (codepoints /
  fingerprint; no model calls, no secrets). **Model stages stay local by
  design**: private endpoints, bearer tokens, GPU servers, cold-boot
  windows; `arun run` is a local batch surface, and running model stages
  on shared CI runners would leak endpoints into logs and burn budgets
  unattended. [actions/example-workflow.yml](actions/example-workflow.yml)
  shows the shape (changed-markdown collection + the vendored composite).

## Source of the measured numbers

Design laws above (cold-boot windows, escalation factor, reasoning-burn
budgets, "road not applied" gate discipline) were measured in real
multi-month pipeline operation; see the operating docs of the pipeline
that contributed them. This kit carries the LAWS, not their history.