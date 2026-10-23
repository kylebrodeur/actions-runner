"""Deterministic gates: exit-code refusals that run BEFORE any model spend.

Built-ins:
    codepoint_gate    fail when any forbidden codepoint appears (fenced code
                      blocks stripped first: shell diffs/patch flags with
                      `--` must not false-positive a dash gate)
    fingerprint_gate  fail when a prompt-contract fingerprint drifts (the
                      byte-exact prompt contract asserts itself at every
                      build; a "fixed" instruction silently changing shape
                      is a contract break, not an improvement)

Config shape (under `gates:` in the pipeline YAML):

    gates:
      dash-gate:
        type: codepoints
        codepoints: ["U+2014", "U+2013"]
      prompt-contract:
        type: fingerprint
        fingerprint: "<sha16 of (INSTR + sep) around a sentinel>"

`run_gates` is the executor: any gate definition whose `type` has no
built-in implementation is a configuration error (fail closed, never
skip-unknown). The CLI can also run standalone gate checks on files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

# fenced code blocks: gates that scan human text must not scan shell payloads
_FENCE = "```"


def strip_fenced_blocks(text: str) -> str:
    out, in_fence = [], False
    for line in text.splitlines():
        if line.strip().startswith(_FENCE):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def _parse_codepoints(spec: dict[str, Any]) -> dict[str, str]:
    """Config entries may be literal chars or U+XXXX escapes."""
    result: dict[str, str] = {}
    for i, entry in enumerate(spec.get("codepoints") or []):
        entry = str(entry).strip()
        if entry.upper().startswith("U+"):
            result[f"codepoint-{i}"] = chr(int(entry[2:], 16))
        elif entry:
            result[f"literal-{i}"] = entry
    return result


def codepoint_gate(path: Path, spec: dict[str, Any]) -> list[str]:
    """Fail with (file, line, col, name) findings on any forbidden codepoint."""
    text = strip_fenced_blocks(path.read_text(encoding="utf-8", errors="replace"))
    findings: list[str] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for name, cp in _parse_codepoints(spec).items():
            col = line.find(cp)
            if col >= 0:
                findings.append(f"{path.name}:{line_no}:{col} {name} (U+{ord(cp):04X}): {line.strip()[:80]}")
    return findings


def fingerprint_gate(prompt_text: str, spec: dict[str, Any]) -> list[str]:
    """Assert the prompt contract's fingerprint. The contract text ships the
    expected fingerprint; the builder computes sha256[:16] over the sentinel
    form (contract text + '\\n\\nX' + separator) and refuses on drift."""
    expected = str(spec.get("fingerprint", "")).strip()
    if not expected:
        return ["fingerprint gate: config carries no fingerprint"]
    actual = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]
    if actual != expected:
        return [f"fingerprint drifted: expected {expected}, got {actual}"]
    return []


GATE_TYPES = {"codepoints": codepoint_gate, "fingerprint": fingerprint_gate}


def run_gates(gate_defs: dict[str, dict[str, Any]], names: tuple[str, ...],
              files: tuple[Path, ...] = (), prompt_text: str = "") -> int:
    """Run each named gate; print findings; return the failure count.

    Unknown gate TYPES fail closed (a config referencing an unimplemented
    gate type must refuse to spend tokens, not silently skip the gate).
    """
    failures = 0
    for name in names:
        spec = gate_defs.get(name) or {}
        gtype = str(spec.get("type", "")).strip()
        impl = GATE_TYPES.get(gtype)
        if impl is None:
            print(f"GATE {name}: FAIL (unknown gate type {gtype!r})")
            failures += 1
            continue
        findings = impl(prompt_text, spec) if gtype == "fingerprint" else []
        for f in files:
            findings.extend(codepoint_gate(f, spec))
        if findings:
            print(f"GATE {name}: FAIL")
            for finding in findings[:8]:
                print(f"  - {finding}")
            failures += 1
        else:
            print(f"  gate {name}: ok")
    return failures


def main_cli(argv: list[str] | None = None) -> int:
    """Standalone: run one codepoint gate over files (the dash-gate script shape)."""
    import argparse

    ap = argparse.ArgumentParser(description="Standalone codepoint gate")
    ap.add_argument("--codepoints", required=True, help="comma-separated U+XXXX or literal chars")
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args(argv)
    spec = {"codepoints": [c.strip() for c in args.codepoints.split(",") if c.strip()]}
    total = 0
    for f in args.files:
        total += len(codepoint_gate(f, spec))
    if total:
        return 1
    print("gate ok (0 hits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
