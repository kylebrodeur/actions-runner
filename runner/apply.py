"""Human-gate apply executor: decisions WRITE machine state, NEVER prose.

The gate vocabulary is generic; the FRONTMATTER VOCABULARY is per-repo
(config-carried):

    pipeline:
      apply:
        key: stage                  # the machine field the decision bumps
        values:                     # decision -> bumped value
          accept: editing
        copy_suffix: ".accepted.md" # the accepted copy lands beside the piece
        wins_over_prose: true       # machine state updated, not just text

    decisions:
      accept:   bump the machine field + write the accepted copy
      redraft:  route back (documentation; the runner prints the rerun hint)
      park:     record the decision; touch nothing

The hard rule this module enforces: apply NEVER edits the prose body. The
only writes are the frontmatter machine field and the accepted COPY beside
the piece. The original file is not modified by `accept`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def apply_gate_decision(decision: str, piece_path: Path, apply_spec: dict[str, Any],
                        worklog: Any) -> int:
    """Execute one human-gate decision; returns 0 on success, SystemExit on
    bad shapes (missing frontmatter = refuse to invent one)."""
    if decision == "park":
        worklog.write({"event": "human-gate", "decision": "park"})
        print("parked; frontmatter untouched")
        return 0
    if decision == "redraft":
        print("redraft: re-run the earlier stage with the review artifacts' advice "
              "items as material (the runner doesn't auto-iterate past a human gate)")
        return 0
    if decision != "accept":
        raise SystemExit(f"unknown decision {decision!r}")

    key = str(apply_spec.get("key", "stage"))
    values = dict(apply_spec.get("values") or {})
    bumped = values.get("accept")
    if not bumped:
        raise SystemExit("apply.values.accept missing: the accept bump value must be configured")
    suffix = str(apply_spec.get("copy_suffix", ".accepted.md"))

    text = piece_path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SystemExit("piece has no frontmatter; refusing to invent one")
    block = m.group(1)
    key_re = re.compile(rf"^{key}:.*$", re.M)
    if key_re.search(block):
        block = key_re.sub(f"{key}: {bumped}", block, count=1)
    else:
        block += f"\n{key}: {bumped}"

    worklog.write({"event": "human-gate", "decision": "accept", "frontmatter_updates": {key: bumped}})
    (piece_path.parent / (piece_path.stem + suffix)).write_text(
        text.replace(m.group(1), block), encoding="utf-8")
    print(f"frontmatter {key} bumped to {bumped} (accepted copy written beside the piece); prose untouched")
    return 0
