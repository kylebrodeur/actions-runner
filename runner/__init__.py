"""actions-runner: config-driven local runner for agentic-actions pipelines.

One YAML config declares a multi-stage pipeline over named endpoints
(OpenAI-compatible model servers); the runner executes stages locally with:

- streamed chat calls that capture reasoning + content (with cold-boot
  retries, backoff, and a budget-escalation policy hook)
- JSONL worklog + per-piece artifact dirs (every request recorded)
- deterministic GATES run BEFORE model tokens are spent (exit-fail)
- human-gate apply (frontmatter machine-field bumps only; prose untouched)

Design law (measured in real agentic pipelines):
- agents never rewrite human-owned prose; outputs are ROADS for the human
- per-request worklog records are the honesty layer (model/endpoint/budget/
  elapsed/reasoning_len/content_len on every call)
- deterministic gates run before tokens; a red gate refuses to spend
- the hook system is the ONE extension surface: hosts register before/after
  stage and call hooks; route shapes are config DATA, never code branches

This package is the machinery; pipeline configs carry the persona.
"""

from .apply import apply_gate_decision
from .calls import escalation_retry, stream_chat, stream_completion
from .config import PipelineConfig, StageSpec, load_pipeline, resolve_auth_token
from .gates import codepoint_gate, fingerprint_gate, run_gates
from .hooks import hooks
from .stages import execute_stage
from .worklog import Worklog

__all__ = [
    "load_pipeline",
    "PipelineConfig",
    "StageSpec",
    "resolve_auth_token",
    "Worklog",
    "stream_chat",
    "stream_completion",
    "escalation_retry",
    "run_gates",
    "codepoint_gate",
    "fingerprint_gate",
    "hooks",
    "execute_stage",
    "apply_gate_decision",
]
