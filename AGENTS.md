# AGENTS.md: the boundary of this repo, stated as rules

An agent working inside `actions-runner/` obeys these rules. They are the
contract; everything else in the docs is detail.

## What this repo is

The machinery for local agentic-actions pipelines: config-loaded stages,
route resolution, streamed model calls, deterministic gates, a JSONL
worklog, and the human-gate apply. It is a library-first kit with a thin
CLI (`arun`): hosts import the modules and register hooks; the CLI runs
the same machinery from a shell.

## Rules an agent keeps here

1. **Do not invent commands, config keys, or error strings.** The command
   surface is exactly `arun run | gate | show` (runner/cli.py). A doc
   that names anything else is wrong; check the module docstrings and
   `tests/` before writing a flag.
2. **Configs are data; behavior is code behind hooks.** Never add routing
   or gating behavior as new config grammar. A consumer's persona (prompts,
   models, budgets, gate vocabularies) lives in its own YAML; a behavior
   extension lives in `runner/hooks.py` registrations.
3. **Never spend tokens past a red gate.** Gates run before calls.
   Unknown gate types fail closed: refuse, never skip.
4. **Never edit prose in apply.** `apply_gate_decision` writes frontmatter
   machine fields and accepted copies; the piece body is untouchable, and
   the original file is never modified by `accept`.
5. **A run is observable or it did not happen.** Every call gets a worklog
   record; every output gets an artifact. Do not add a code path that
   models silently.
6. **Model calls stay local.** CI and the Actions composite run
   deterministic text gates only; never wire a model stage into shared CI.
7. **Keep the docs honest per the family standard** (the
   documentation standard in the private control-plane repo): real
   commands with expected output, errors carrying their fix, no em or en
   dashes, no AI-slop words, no personal identifiers, and a check date on
   factual claims. Run the suite:
   `uv run pytest tests -q` and `uv run ruff check .`
8. **The prose the machinery routes belongs to the human.** Runner output
   (drafts, reviews, redraft hints) is a ROAD for a human gate, never the
   published artifact of record.
9. **No personal identifiers anywhere in the public docs.** The examples
   use generic seat ids (`writer-deep`, `scribe`, `reviewer`), never a
   person's name or handle.

## Where to start reading

`SYSTEM.md` is the docs home: the pieces, the law each carries, the
extraction contract. The skill in `skills/actions-runner/SKILL.md` is the
short form of rules 2 through 6.