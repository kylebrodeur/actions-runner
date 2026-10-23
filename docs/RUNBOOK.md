<!-- STATUS: VERIFIED 2026-10-22. Every error string below is quoted from runner/*.py source or captured from a live run; every flag was checked against the argparse definitions in runner/cli.py. -->
# RUNBOOK.md: the operator's reference

The full command surface, every config key, the error strings and their
fixes, and the recovery procedures. Task-shaped guidance lives in
[how-to.md](how-to.md); the system's laws live in [../SYSTEM.md](../SYSTEM.md).

## The command surface

Exactly three subcommands (`runner/cli.py`), no more:

```
arun run   <config> <stage> [--brief <path>] [--input <agent=path> ...]
                [--decision <d>] [--token-env <env-name>]
arun gate  <config> <gate-name> <file> [...]
arun show  <config>
```

## `arun run`: execute one declared stage

| Argument | Meaning |
|---|---|
| `config` | pipeline YAML config (the runner-of-record; its location sets the run root, see the root law below) |
| `stage` | a stage declared with `agents:` in the config's `pipeline:` |
| `--brief <path>` | the piece/brief file; gates run on it first; it becomes the per-piece artifact slug |
| `--input <agent=path>` | per-agent prompt input text, read from the file (repeatable) |
| `--decision <d>` | human-gate decision: `accept`, `park`, or `redraft`; runs the apply executor and SKIPS stage execution |
| `--token-env <name>` | the bearer env var for this run; else the route-auth chain resolves it |

Run order: load config, resolve stage, read inputs, (if `--decision`)
apply the gate decision and exit, else run the stage's gates, then the
model calls, then the after-stage hooks.

## `arun gate`: standalone gate run

```bash
uv run arun gate <config> <gate-name> <files...>
```

Runs one named gate over the given files, prints findings, exits with the
failure count. Costs nothing; safe anywhere, including CI. Findings print
as `(file, line, col) name (U+XXXX): line text` for codepoint gates, up to
8 shown per gate.

## `arun show`: print the parsed pipeline

```bash
uv run arun show <config>
```

Prints the config path and run root, every executable stage (agents,
mode, gates), every route (model, endpoint, max_tokens), and the resolved
apply vocabulary. Use it to sanity-check a config before a real run.

## Tokens: the auth chain

Resolution order (config DATA, never stored or logged):

1. `--token-env NAME` set on the command and `NAME` in the environment
2. the seat's own `auth_env` (an OpenAI-compatible server per seat)
3. `defaults.auth.bearer_env` (one token for the address book)
4. the runner default `RUNNER_TOKEN`

Empty at resolution time is a fatal (below).

## The config schema (every key the loader reads)

Top level (`runner/config.py:load_pipeline`): `defaults:`, `pipeline:`,
`gates:`, `agent_routes:`. `version: 1` is accepted and advisory.

### `defaults`

| Key | Meaning |
|---|---|
| `primary: <agent-id>` | the anchor seat; stages may fall back to it |
| `endpoints: {<name>: <base-url>}` | the address book that `endpoint_key` resolves into |
| `auth: {bearer_env: <ENV_VAR>}` | the default token env (chain: seat `auth_env` > this) |
| `temperatures: {<stage>: <float>}` | stage-default temperatures |
| `concurrency: <int>` | parallel workers within a stage; also sizes the `parallel` mode's thread pool |
| `logging: {worklog: <path>, artifacts: <dir>}` | worklog path (default `worklog.local.jsonl`) and artifacts dir (default `artifacts.local/`) |
| `apply: {...}` | the human-gate vocabulary (below) |

### `pipeline.<stage>`

| Key | Meaning |
|---|---|
| `agents: [<agent-id>, ...]` | REQUIRED for the stage to be executable; a block without `agents` is documentation the runner loads and ignores |
| `mode` | `parallel` | `cross` | `sequential-passes` | `single` | `parallel-consolidate` (default `single`) |
| `gates: [<gate-name>, ...]` | deterministic gates run BEFORE any model spend |
| `passes: [<pass-name>, ...]` | the passes `sequential-passes` walks in order |
| `inputs` / `outputs` / `notes` | documentation for humans; the runner passes files explicitly via the CLI |

### `agent_routes.<agent-id>` (the seat table)

| Key | Meaning |
|---|---|
| `model: <model-id>` | the model name sent to the endpoint |
| `endpoint: <base-url>` | direct endpoint; or `endpoint_key:` naming a `defaults.endpoints` entry |
| `auth_env: <ENV_VAR>` | this seat's token env (else the `defaults.auth` chain) |
| `max_tokens: <int>` | the call budget (default 4000 in the executor) |
| `temperature: <float>` | the sampling temperature (default 0.8 in the executor) |
| `prompts: {<stage>: <template>}` | per-stage prompt template; `*` is the fallthrough; slots: `{brief}`, `{other}`, `{draft}`, `{pass_name}`; the `parallel` fallback prompt is `Write the piece from this brief:\n\n{brief}` |
| `budget_escalation: {factor: <int>, rounds: <int>}` | the retry policy on empty-prose runs (defaults factor 4, 1 round; `enabled: false` disables) |
| `prompt_template: {instr, sep, fingerprint, contract}` | byte-exact completion-contract seats: the fingerprint is asserted on every build, drift is fatal |

Extra keys ride along as route `meta` (e.g. `byte_exact`,
`voice_transform`); the executor ignores what it does not read.

### `gates.<gate-name>`

| Key | Meaning |
|---|---|
| `type: codepoints` | fail when any listed codepoint appears in a gated file (fenced code blocks stripped first) |
| `codepoints: [...]` | literal characters or `"U+2014"`-style escapes |
| `type: fingerprint` | assert a byte-exact prompt-contract fingerprint (used with `prompt_template.fingerprint` by the builder in `runner/prompts.py`) |
| `fingerprint: <sha16>` | sha256 first 16 hex of the sentinel form `(instr + "\n\nX" + sep)`, precomputed at build time and shipped in the config |
| `scope: publish-bound` | documentation; the gate applies to the inputs it is given |

Any other `type` value fails closed: the gate prints
`GATE <name>: FAIL (unknown gate type '<type>')` and the run refuses
before tokens. Custom gate implementations join via the hook system
(`runner/hooks.py`), not by extending the config grammar ad hoc.

### The human-gate apply vocabulary (`defaults.apply` or `pipeline.<stage>.apply`)

| Key | Meaning |
|---|---|
| `key: <frontmatter-field>` | the machine field a decision bumps (default `stage`) |
| `values: {<decision>: <bumped-value>}` | per-decision bump values (`accept: editing` in the example) |
| `copy_suffix: "<suffix>"` | the accepted copy lands beside the piece with this suffix (default `.accepted.md`) |
| `wins_over_prose: true` | documentation marker: machine state wins, prose untouched |

Empty apply spec = the measured default: accept bumps `stage: editing`
with a `.accepted.md` copy.

## The run root (the root law)

`config.resolve().parents[1]` is the run root. Worklogs and artifacts
resolve relative to it, so a config living at
`<project>/configs/demo.yml` makes `<project>` the run root. Artifacts:

- no `--brief`: the shared dir `artifacts.local/`
- with `--brief <file>`: per-piece `artifacts.local/<file-stem>/`

so multi-piece runs never overwrite each other.

## Error strings and their fixes

Every error below exits `SystemExit` (code 1) with the exact string the
repo emits. Find your message, apply its fix.

### Stage and config

| Error string | Cause and fix |
|---|---|
| `stage <name> not declared as an executable stage (declared: [...])` | you named a stage that has no `agents:` (or a typo). Fix: `arun show <config>` to list the executable stages. |
| `config <path> is not a YAML mapping` | the config file parses as a scalar or list. Fix: the config must be a YAML mapping at top level. |
| `stage <name>!r: empty agents list` | a stage declared `agents: []`. Fix: list seats or drop `agents` to make the block documentation. |
| YAML parser errors (e.g. `yaml.parser.ParserError`) | malformed YAML; the loader surfaces the parser message with line/column. Fix the YAML shape at the cited line/column. |

### Routes and auth

| Error string | Cause and fix |
|---|---|
| `agent <id>!r: no route in agent_routes` | the stage names a seat with no `agent_routes` entry. Fix: add the route or fix the seat id spelling. |
| `agent <id>!r: endpoint_key <key>!r not in defaults.endpoints` | the seat's `endpoint_key` is not a name in the `defaults.endpoints` address book. Fix: add the endpoint under `defaults.endpoints` or switch to a direct `endpoint:` URL. |
| `agent <id>!r: route names no endpoint/endpoint_key` | the route carries neither `endpoint` nor `endpoint_key`. Fix: add one; routes are pure data. |
| `<ENV_NAME> must be in the environment for this call` | the resolved token env var is empty (missing from the environment). Fix: `export <ENV_NAME>=<token>` for the run, or pass the right `--token-env`. A missing token is never retried. |
| `endpoint <url> did not answer after 3 attempts` | endpoint exhausted transport retries (timeouts, refused connections, empty streams). Fix: wake the model server (cold boot) and re-run; gates re-run for free (see "Recovery: cold boot" below). |
| `fingerprint drifted: expected <sha16>, got <sha16>` | the byte-exact prompt contract changed bytes. Fix: restore the instruction text, or if the contract is intentionally re-trained, recompute and re-pin the fingerprint at build time. |
| `prompt contract drifted (fingerprint <sha16>, expected <sha16>); reject instead of silently degrading a trained contract` | the same law, raised by the prompt builder (`runner/prompts.py:build_prompt`). Same fix. |
| `fingerprint gate: config carries no fingerprint` | a `type: fingerprint` gate with no `fingerprint:` value. Fix: ship the sha16 in the config. |

### Inputs

| Error string | Cause and fix |
|---|---|
| `--brief <path> does not exist` | the brief path is wrong. Fix the path (relative paths resolve from your CWD; the worklog/artifacts resolve from the run root). |
| `--brief required for --decision` | `--decision` needs a piece to operate on. Fix: pass `--brief <piece.md>`. |
| `input <agent=path>: <path> does not exist` | a `--input` file is missing. Fix the path or the `agent=path` spelling. |

### Human-gate apply

| Error string | Cause and fix |
|---|---|
| `unknown decision <d>!r` | only `accept`, `park`, `redraft` exist. Fix the spelling. |
| `apply.values.accept missing: the accept bump value must be configured` | an apply spec without an `accept` value. Fix: add `values: {accept: <bumped-value>}` under `apply:`. |
| `piece has no frontmatter; refusing to invent one` | accept on a piece with no `---` frontmatter block. Fix: add the frontmatter (a machine field for `apply.key` to bump) and re-run. |

### Gates

| Error string | Cause and fix |
|---|---|
| `GATE <name>: FAIL` (findings listed beneath) | the gate found violations. Fix the TEXT. Findings carry the file, line, column, the codepoint, and a line excerpt (80 chars). |
| `GATE <name>: FAIL (unknown gate type <t>!r)` | the config names a gate type with no built-in implementation: fail closed by design. Fix: implement it as a hook, or use a built-in type. |
| `1 gate(s) red before any tokens were spent` | exit message from `arun run`: a red gate refused the stage. Fix the text, then re-run (re-running re-runs the gates for free). |

### Hooks

| Error string | Cause and fix |
|---|---|
| `before-stage hook refused stage <name>!r: <err>` | a before-stage hook raised. The stage died before any spend. Fix the hook's precondition and re-run. |
| `hook(<name>): <msg>` in the worklog as `hook-error` events | before/after-call and after-stage hook errors are contained; the run continues, the record does not. Fix at leisure; check `hook-error` events after each run. |
| `unknown hook <name>!r (available: (...))` | registered under a misspelled name. The available names are `before-stage`, `after-stage`, `before-call`, `after-call`. |

## Recovery procedures

### A red gate refuses the run

1. Read the finding lines; fix the flagged characters in the TEXT (never
   the gate definition to make a finding disappear).
2. Re-run the gate standalone to confirm green:

```bash
uv run arun gate <config> <gate-name> <files...>
```

```text
  gate dash-gate: ok
```

3. Re-run the stage. Nothing was spent; the worklog shows no model
   events for the refused attempt.

### Cold boot

Symptom: `ConnectError` / `ReadTimeout` with the line
`(server cold or slow); retry 1/2 in 90s`.

1. Let the transport retry (up to 2 retries at a 90-second backoff), or
   wake the server out-of-band between attempts.
2. If it exhausts (`did not answer after 3 attempts`): wait for the boot
   window, then re-run the identical command. Gates re-run for free.
3. Repeated cold-boot exhaustion on one seat: budget the seat's timeout
   above its real boot time (the 480-second default covers measured
   windows; `runner/calls.py` `DEFAULT_TIMEOUT`) or schedule a wake call
   before the run.

### Empty prose from a reasoning model

Symptom: the stage prints a record with `content_len: 0` and
`finish: length`, or the escalation line
`reasoning <n>c ate the budget; escalating to <n> tokens`.

1. One escalation round at factor x4 is automatic (configurable per seat
   via `budget_escalation`).
2. Still empty: raise `max_tokens` for that seat up front. Reasoning-first
   models burn 4-20K chars before prose.
3. The call did not fail; no action is needed after a successful
   escalation.

### A stage produced the wrong artifact name

Artifacts default to `<stage>-<agent>.md` (`parallel`, `cross`), or
`<stage>.md` (single, sequential-passes, consolidated). Override per
agent with the stage's `outputs: [{artifact: <name>}]`. Names land inside
the run root's artifacts dir; nothing outside the run root is written.

### Re-verify a run's honesty

Any claim about what a run did maps to a worklog record:

```bash
python3 -c "import json; [print(json.dumps(json.loads(l))) for l in open('worklog.local.jsonl')]"
```

Every record carries `recorded_at`, the event name, the seat, timings,
`reasoning_len` / `content_len`, and `finish`. A missing record for a
claimed call means the claim is unverifiable: trust the worklog, not the
claim.

## What this runbook does NOT cover

- model server administration: seats point at endpoints you already own
- CI for model stages: deliberately unsupported (text gates only)
- publishing prose: the human gate is the publishing surface; the runner
  writes machine state and artifacts, never the publish-bound text