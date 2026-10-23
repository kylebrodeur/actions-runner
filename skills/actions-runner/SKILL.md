---
name: actions-runner
description: Use when setting up, running, or debugging a local agentic-actions pipeline with this kit: running declared stages with gates before token spend, wiring the text-gate composite into CI, or writing a pipeline config against the documented schema.
---

# actions-runner skill: when to reach for this kit and how to stay in its lanes

Use when you need to run a local agentic-actions pipeline: model seats at
named endpoints, declared stages, deterministic gates before token spend.
This skill teaches the boundary:

1. **Configs carry the persona; the runner carries the shapes.** Seats,
   prompts, budgets, gates, and gate vocabularies are YAML in the pipeline
   config. Routing that needs code, or gating that needs code, belongs in
   `runner/hooks.py` registrations, never in a config grammar or a fork.
2. **Deterministic gates run BEFORE any token spend.** A stage with
   `gates:` never calls a model while a gate is red. Unknown gate types
   fail closed: a config naming an unimplemented gate type refuses to
   spend, never silently skips.
3. **Stage modes are one of the five measured shapes:** parallel, cross,
   sequential-passes, single, parallel-consolidate. A new shape needs
   operating evidence, not a guess.
4. **The worklog is the honesty layer.** Every call appends a JSONL
   record (elapsed, reasoning_len, content_len, finish); every artifact
   lands per-piece. Claims about what a run did are checked against the
   worklog, not memory.
5. **The human gate stays human.** `apply` decisions write machine state
   only (`key` frontmatter bumps + accepted copies); the runner never
   rewrites publish-bound prose.
6. **Model stages stay local.** `arun run` is a local batch surface;
   CI runs only deterministic text gates (no model calls, no secrets).

Run the CLI as:

```bash
arun show  <pipeline.yml>
arun gate  <pipeline.yml> <gate-name> <files...>
arun run   <pipeline.yml> <stage> --brief <piece.md>
```