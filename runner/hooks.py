"""Hook system: pipeline-level extension points, registered at runtime.

The runner's coupling law: the CORE owns the shapes (config, worklog, calls,
gates, stage modes); everything project-specific plugs through hooks.

Available hooks (register with `hooks.register(name, fn)`):

    before-stage   (config, stage, ctx) -> None
        runs before any gate or call; use for setup (wake endpoints, prep dirs).
        Raise to refuse the stage entirely.
    after-stage    (config, stage, results, ctx) -> None
        runs after every agent call landed; `results` is the executor's dict.
        Raise to fail the stage post-hoc (e.g. a custom postcheck).
    before-call    (config, agent_id, body, meta) -> body
        last look at the outbound request body (mutate/add fields; return the
        body to forward). Use for prompt shaping beyond templates.
    after-call     (config, agent_id, result, meta) -> result
        postprocess one call result (resample policies, transforms, redaction).
        Return the result (possibly replaced).
    gate           (name, spec, files, prompt_text) -> list[str]
        a custom gate implementation; registered under the gate's `type`.

Fire order per call: before-call -> HTTP stream -> after-call.
Fire order per stage: before-stage -> gates -> calls (each with call hooks)
-> after-stage.

Fail-closed semantics: hook errors in before-stage refuse the stage; errors
in before-call/after-call are CONTAINED per call (recorded in the worklog as
{"event": "hook-error", "hook": ...}) so one broken extension degrades one
call, never the pipeline — hooks extend, they must not be able to kill runs
silently OR loudly.

Usage (any host script):

    from runner.hooks import hooks

    @hooks.register("before-call")
    def shape_body(config, agent_id, body, meta):
        body["top_p"] = 0.95
        return body
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

HookFn = Callable[..., Any]

HOOK_NAMES = ("before-stage", "after-stage", "before-call", "after-call")


class HookRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, list[HookFn]] = defaultdict(list)

    def register(self, name: str, fn: HookFn | None = None) -> HookFn | Callable[[HookFn], HookFn]:
        """Register a handler under a hook name. Doubles as a decorator:
        `@hooks.register("after-call")` (fn omitted returns a registrar)."""
        if name not in HOOK_NAMES:
            raise SystemExit(f"unknown hook {name!r} (available: {HOOK_NAMES})")
        if fn is None:
            def _reg(inner: HookFn) -> HookFn:
                self._registry[name].append(inner)
                return inner
            return _reg
        self._registry[name].append(fn)
        return fn

    def registrations(self, name: str) -> list[HookFn]:
        return list(self._registry[name])

    def fire(self, name: str, *args: Any) -> list[str]:
        """Run a hook's handlers in order; contain + record errors; return them."""
        if name not in HOOK_NAMES:
            raise SystemExit(f"unknown hook {name!r}")
        errors: list[str] = []
        for fn in self._registry[name]:
            try:
                fn(*args)
            except Exception as exc:
                errors.append(f"{getattr(fn, '__name__', 'hook')}: {type(exc).__name__}: {str(exc)[:150]}")
        return errors

    def call(self, name: str, value: Any, *args: Any) -> Any:
        """Thread one value through a chain (before-call/after-call shape):
        each handler receives (value, *args) and returns the (possibly new)
        value; contained: a broken handler returns its input unchanged."""
        for fn in self._registry[name]:
            try:
                result = fn(value, *args)
                if result is not None:
                    value = result
            except Exception:
                continue
        return value


hooks = HookRegistry()
