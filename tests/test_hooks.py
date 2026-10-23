"""Hooks: registration, firing order, containment."""

from __future__ import annotations

import pytest

from runner.hooks import HookRegistry


def test_unknown_hook_name_refused():
    r = HookRegistry()
    with pytest.raises(SystemExit):
        r.register("not-a-hook", lambda: None)


def test_fire_contains_errors():
    r = HookRegistry()

    def broken(x):
        raise ValueError("boom")

    r.register("before-stage", broken)
    errors = r.fire("before-stage", 1)
    assert len(errors) == 1
    assert "boom" in errors[0]


def test_call_threads_values():
    r = HookRegistry()
    r.register("before-call", lambda body, ctx: {**body, "extra": 1})
    r.register("before-call", lambda body, ctx: {**body, "extra": body["extra"] + 1})
    out = r.call("before-call", {}, "ctx")
    assert out == {"extra": 2}


def test_call_contained_on_broken_handler():
    r = HookRegistry()

    def broken(body, ctx):
        raise RuntimeError("no")

    r.register("before-call", broken)
    out = r.call("before-call", {"k": 1}, "ctx")
    assert out == {"k": 1}  # contained: input unchanged


def test_register_doubles_as_decorator():
    r = HookRegistry()

    # decorator form must also work: register("name") used bare
    @r.register("after-call")
    def shape(result, *args):
        return {**result, "shaped": True}

    assert r.registrations("after-call") == [shape]
    out = r.call("after-call", {}, "x")
    assert out["shaped"] is True


def test_register_misuse_named_without_fn_is_registrar():
    r = HookRegistry()
    # register("name") without fn returns a registrar (decorator form), NOT a registration.
    returned = r.register("after-call")
    assert callable(returned) and r.registrations("after-call") == []
