"""The gates + prompt-fingerprint contracts."""

from __future__ import annotations

import hashlib

import pytest

from runner.gates import codepoint_gate, run_gates, strip_fenced_blocks
from runner.prompts import build_prompt


def test_fenced_blocks_stripped(tmp_path):
    text = "clean line\n```\n--flag with \u2014 dash\n```\nclean again"
    assert "\u2014" not in strip_fenced_blocks(text)


def test_codepoint_gate_finds_location(tmp_path):
    p = tmp_path / "piece.md"
    p.write_text("line one\nhas a \u2014 real em dash\n")
    findings = codepoint_gate(p, {"codepoints": ["U+2014"]})
    assert len(findings) == 1
    assert findings[0].startswith("piece.md:2:")


def test_codepoint_gate_literal(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("a forbidden char: —\n")
    findings = codepoint_gate(p, {"codepoints": ["—"]})
    assert len(findings) == 1


def test_fingerprint_gate_contract():
    instr = "Rewrite the text.\n\nLine two."
    sep = "\n\n### Out:\n\n"
    sentinel = instr + "\n\nX" + sep
    fp = hashlib.sha256(sentinel.encode()).hexdigest()[:16]
    template = {"instr": instr, "sep": sep, "fingerprint": fp}
    # builds clean at the registered fingerprint
    assert build_prompt(template, "  some draft \n").endswith(sep)
    # drift refuses (an edited instruction is a contract break)
    bad = dict(template, fingerprint="0" * 16)
    with pytest.raises(SystemExit):
        build_prompt(bad, "draft")


def test_run_gates_unknown_type_fails_closed(capsys):
    failures = run_gates({"g1": {"type": "definitely-not-real"}}, ("g1",))
    assert failures == 1
    assert "unknown gate type" in capsys.readouterr().out


def test_run_gates_happy_path(tmp_path, capsys):
    p = tmp_path / "clean.md"
    p.write_text("no dashes here\n")
    failures = run_gates({"dash": {"type": "codepoints", "codepoints": ["U+2014"]}},
                         ("dash",), (p,))
    assert failures == 0
    assert "gate dash: ok" in capsys.readouterr().out
