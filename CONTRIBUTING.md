# Contributing

How to set up the repo, run the tests, and open a change.

## Set up

The repo uses [uv](https://docs.astral.sh/uv/) end to end. Python 3.11+
(python 3.13 is what CI runs):

```bash
uv sync
```

That creates `.venv/` and installs the two runtime dependencies (httpx,
pyyaml) plus pytest. Nothing else is needed; no model endpoint is required
for anything in the repo except live runs.

## Run the tests

```bash
uv run pytest tests -q
```

Expected output: `37 passed` (check date: 2026-10-22). Every test is
offline; the stage-executor tests run against a fake endpoint started
in-process by the test helpers.

Lint with ruff (line length 120, rules E/F/I/B/W):

```bash
uv run ruff check runner tests
```

## Verify the documentation

The repo belongs to the AI ECP documentation family. Run these before any
commit that adds or moves a doc:

```bash
uv run pytest tests -q
uv run ruff check .
```

Those commands print pytest and ruff output, not `actions-runner:` lines.
The family harness that prints those lines checks the required doc files,
README link resolution, the prose gates (no em or en dashes, no banned
AI-slop words), funding parity, and indexing. It lives in the PRIVATE
system repo and is maintainer-only; a public contributor runs the
commands above, and the family harness runs on the maintainer's side.

Repo docs carry their check date: when you verify a claim about the
code, note the date in the doc.

## Open a change

1. Make the change, tests first where the change is behavior.
2. `uv run pytest tests -q` and `uv run ruff check runner tests` green.
3. If you touched a doc: the harness green, and the check date updated.
4. Open a pull request. Keep the diff one boundary: the CLI, the config
   schema, and the stage shapes are contract surfaces, and a change to
   them comes with a section in `SYSTEM.md` explaining the new law.

## What makes a good change here

This repo's value is being boring. The stage shapes, the worklog schema,
and the gate semantics are measured laws, not preferences: a PR that
"improves" escalation behavior without the operating evidence to show the
measured defaults are wrong is not merged. Extension belongs in hooks
(`runner/hooks.py`); config belongs in persona fields a consumer owns.

## Vendored copies

If you ship this kit into a consuming repo (vendored under
`tools/actions-runner/`), changes belong upstream first, then the
consumer re-vendors at a tag. Edit the kit here, not the vendored copy.