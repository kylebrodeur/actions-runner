"""Prompt-template handler: the COMPLETION seat (byte-exact contracts).

A completion seat's route carries a `prompt_template` block:

    prompt_template:
      instr: >-                      # the contract INSTRUCTION text (verbatim)
        (multi-line bytes)
      sep: "\n\n### Rewritten:\n\n"  # the separator appended after the draft
      fingerprint: "<sha16>"         # the sentinel fingerprint (asserted every build)
      contract: byte-exact           # doc marker: never reword, no chat turns

The builder composes `instr + "\n\n" + draft + sep` and asserts the
fingerprint over the SENTINEL form (instr + "\\n\\nX" + sep) at EVERY call.
A config edit that changes the instruction bytes fails the call instead of
silently degrading a trained model's output.
"""

from __future__ import annotations

import hashlib
from typing import Any


def build_prompt(template: dict[str, Any], text: str) -> str:
    """Build the byte-exact prompt; refuse on fingerprint drift."""
    instr = str(template.get("instr", ""))
    sep = str(template.get("sep", "\n\n"))
    prompt = instr + "\n\n" + text.strip() + sep

    expected = str(template.get("fingerprint", "")).strip()
    if expected:
        sentinel = instr + "\n\nX" + sep
        actual = hashlib.sha256(sentinel.encode("utf-8")).hexdigest()[:16]
        if actual != expected:
            raise SystemExit(
                f"prompt contract drifted (fingerprint {actual}, expected {expected}); "
                "reject instead of silently degrading a trained contract"
            )
    return prompt
