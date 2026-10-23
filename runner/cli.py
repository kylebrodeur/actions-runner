"""CLI: `arun` — the actions runner's entry point.

arun run   <config> <stage> [--brief <path>] [--input <agent=path> ...]
                 [--decision <d>] [--token-env <env-name>]
arun gate  <config> <gate-name> <file> [...]   standalone deterministic gate
arun show  <config>                            print the parsed pipeline

The CLI owns no persona: configs carry stages/routes/prompts; hooks carry
project-specific behavior; the human-gate vocabulary lives under
defaults.apply in the config.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .apply import apply_gate_decision
from .config import load_pipeline
from .gates import run_gates
from .stages import execute_stage
from .worklog import Worklog


def cmd_show(args: argparse.Namespace) -> int:
    config = load_pipeline(Path(args.config))
    print(f"pipeline: {config.path} (root {config.root})")
    print("stages:")
    for name, stage in config.stages.items():
        print(f"  {name}: agents={list(stage.agents)} mode={stage.mode} gates={list(stage.gates)}")
    print("routes:")
    for agent_id, route in config.agent_routes.items():
        print(f"  {agent_id}: model={route.get('model')} endpoint={route.get('endpoint') or route.get('endpoint_key')} "
              f"max_tokens={route.get('max_tokens')}")
    print("apply:", config.raw_apply_spec())
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_pipeline(Path(args.config))
    if args.stage not in config.stages:
        raise SystemExit(f"stage {args.stage!r} not declared as an executable stage "
                         f"(declared: {sorted(config.stages)})")
    stage = config.stages[args.stage]
    slug = Path(args.brief).stem if args.brief else None
    worklog = Worklog(config.defaults, config.root, slug)

    # inputs: the brief + per-agent input texts (the caller owns reading files)
    brief = ""
    input_files: list[Path] = []
    if args.brief:
        brief_path = Path(args.brief)
        if not brief_path.exists():
            raise SystemExit(f"--brief {brief_path} does not exist")
        input_files.append(brief_path)
        brief = brief_path.read_text(encoding="utf-8")
    inputs: dict[str, str] = {}
    for pair in args.input or []:
        agent, _, path = pair.partition("=")
        p = Path(path)
        if not p.exists():
            raise SystemExit(f"input {pair}: {path} does not exist")
        inputs[agent] = p.read_text(encoding="utf-8")
        input_files.append(p)

    # HUMAN GATE decision: state write, never prose; skips stage execution
    if args.decision:
        if not args.brief:
            raise SystemExit("--brief required for --decision")
        return apply_gate_decision(args.decision, Path(args.brief),
                                   config.raw_apply_spec(), worklog)

    # deterministic gates FIRST: a red gate refuses to spend tokens
    failures = run_gates(config.gate_defs, stage.gates, tuple(input_files))
    if failures:
        raise SystemExit(f"{failures} gate(s) red before any tokens were spent")

    # the token: explicit flag > the route-resolved env chain inside the executor
    token = os.environ.get(args.token_env, "").strip() if args.token_env else ""
    execute_stage(config, token, worklog, stage, brief=brief[:20000], input_texts=inputs)
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    config = load_pipeline(Path(args.config))
    return run_gates(config.gate_defs, (args.gate_name,), tuple(Path(f) for f in args.files))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Config-driven local runner for agentic pipelines")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="execute one declared stage")
    run_p.add_argument("config", help="pipeline YAML config")
    run_p.add_argument("stage", help="declared stage name")
    run_p.add_argument("--brief", type=Path, help="the piece/brief file (gates run on it first)")
    run_p.add_argument("--input", action="append", help="agent=path prompt inputs (repeatable)")
    run_p.add_argument("--decision", help="human-gate decision (accept/park/redraft)")
    run_p.add_argument("--token-env", help="bearer env name for this run (else the route-auth chain)")
    run_p.set_defaults(func=cmd_run)

    gate_p = sub.add_parser("gate", help="standalone gate run over files")
    gate_p.add_argument("config", help="pipeline YAML config")
    gate_p.add_argument("gate_name", help="declared gate name")
    gate_p.add_argument("files", nargs="+", help="files to gate")
    gate_p.set_defaults(func=cmd_gate)

    show_p = sub.add_parser("show", help="print the parsed pipeline")
    show_p.add_argument("config", help="pipeline YAML config")
    show_p.set_defaults(func=cmd_show)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
