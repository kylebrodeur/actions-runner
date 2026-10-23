"""Stage executor: the declared stage -> mode-shaped model calls at agent routes.

Modes (the four shapes measured in real pipelines):

    parallel            every agent takes the SAME brief concurrently
    cross               each agent reads the OTHER's artifact and produces
                        an informed second take (no merges)
    sequential-passes   one agent runs its passes IN ORDER (the runner
                        emits per-pass artifacts + records)
    single              one agent, one call
    parallel-consolidate  every agent runs concurrently; the runner joins
                        non-duplicate findings (dedupe by content hash)

The runner owns the SHAPES; the config owns the PERSONA (prompts live in
agent_routes as templates, not hardcoded here). Prompt templates:

    agent_routes:
      <agent>:
        prompts:
          <stage>: >          # {brief}, {other}, {draft}, {passes} placeholders
            (template text)
        budget_escalation: {factor: 4, rounds: 1}   # optional policy

Outputs: every call lands a worklog record + writes its artifact
(`outputs[].artifact` names or `<stage>-<agent>.md` by default).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
from typing import Any

from .calls import escalation_retry, stream_chat
from .config import PipelineConfig, StageSpec, resolve_auth_token


def _template_for(config: PipelineConfig, agent_id: str, stage: str, fallback: str = "") -> str:
    route = config.agent_routes.get(agent_id) or {}
    prompts = route.get("prompts") or {}
    return str(prompts.get(stage, prompts.get("*", fallback)))


def _fill(template: str, **values: str) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = _content_hash(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def execute_stage(config: PipelineConfig, token: str, worklog: Any,
                  stage: StageSpec, *,
                  brief: str = "",
                  input_texts: dict[str, str] | None = None,
                  output_names: dict[str, str] | None = None,
                  extra_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute one declared stage; returns {agent_id: result-dict}.

    input_texts: per-agent prompt inputs (the stage caller owns the files:
    reading drafts/bridges/reviews is the CALLER's job — the executor only
    shapes the calls).

    Hook seams: before-stage (refusal fails the stage), before-call/after-call
    (thread the body/result), after-stage (post-hoc checks; errors contained).
    """
    from .hooks import hooks as registry

    texts = input_texts or {}
    names = output_names or {}
    # per-stage hook errors are recorded, never silent or fatal post-gates
    ctx = {"brief": brief, "input_texts": texts, "output_names": names}
    before_errors = registry.fire("before-stage", config, stage, ctx)
    for err in before_errors:
        worklog.write({"event": "hook-error", "hook": "before-stage", "stage": stage.name, "error": err})
        print(f"  hook(before-stage): {err}")
        raise SystemExit(f"before-stage hook refused stage {stage.name!r}: {err}")
    mode = stage.mode

    def _call_one(agent_id: str, prompt: str) -> tuple[str, dict[str, Any]]:
        url, model, meta, env_name = config.resolve_route(agent_id)
        auth = resolve_auth_token(env_name, explicit_token=token)
        budget = int(meta.get("max_tokens", 4000))
        body = registry.call("before-call", {"messages": [{"role": "user", "content": prompt}],
                                             "max_tokens": budget,
                                             "temperature": float(meta.get("temperature", 0.8))},
                             config, agent_id, meta)
        temp = float(body.pop("temperature", meta.get("temperature", 0.8)))
        budget = int(body.pop("max_tokens", budget))
        messages = body.get("messages") or [{"role": "user", "content": prompt}]
        result = stream_chat(url, auth, model, messages, budget, temp)
        # after-call hook: postprocess (resample policies, transforms) — contained
        result = registry.call("after-call", result, config, agent_id, meta)
        # Budget-escalation policy (config per-agent, defaults measured):
        escalation = meta.get("budget_escalation") or {}
        if escalation.get("enabled", True):
            result = escalation_retry(
                lambda grown: _recall(config, token, agent_id, prompt, grown),
                result, budget,
                factor=int(escalation.get("factor", 4)),
                max_rounds=int(escalation.get("rounds", 1)),
            )
        return agent_id, result

    results: dict[str, Any] = {}

    if mode == "parallel":
        workers = int((config.defaults or {}).get("concurrency", len(stage.agents)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_call_one, agent_id,
                            _fill(_template_for(config, agent_id, stage.name,
                                                fallback="Write the piece from this brief:\n\n{brief}"),
                                  brief=brief, **texts)): agent_id
                for agent_id in stage.agents
            }
            for fut in concurrent.futures.as_completed(futures):
                agent_id, result = fut.result()
                results[agent_id] = result
                out = worklog.artifact(names.get(agent_id, f"{stage.name}-{agent_id}.md"), result["content"])
                worklog.write({"event": stage.name, "agent": agent_id,
                               "mode": mode, "finish": result["finish"],
                               "elapsed_seconds": result["seconds"],
                               "reasoning_len": len(result["reasoning"]),
                               "content_len": len(result["content"])})
                print(f"{agent_id}: {result['seconds']}s, reasoning {len(result['reasoning'])}c, "
                      f"prose {len(result['content'])}c -> {out.name}")

    elif mode == "cross":
        # each agent reads the OTHER's artifact text (texts keyed by agent)
        if len(stage.agents) != 2:
            raise SystemExit("cross mode expects exactly two agents")
        left, right = stage.agents
        pairs = {left: texts.get(right, ""), right: texts.get(left, "")}
        for agent_id, other_text in pairs.items():
            agent_id_, result = _call_one(
                agent_id,
                _fill(_template_for(config, agent_id, stage.name),
                      other=other_text[:6000], brief=brief, **{k: v for k, v in texts.items() if k == agent_id}))
            results[agent_id] = result
            out = worklog.artifact(names.get(agent_id, f"{stage.name}-{agent_id}.md"), result["content"])
            worklog.write({"event": stage.name, "agent": agent_id, "mode": mode,
                           "elapsed_seconds": result["seconds"], "content_len": len(result["content"])})
            print(f"{agent_id} cross-take: {result['seconds']}s, {len(result['content'])}c -> {out.name}")

    elif mode == "sequential-passes":
        agent_id = stage.agents[0]
        url, model, meta, env_name = config.resolve_route(agent_id)
        auth = resolve_auth_token(env_name, explicit_token=token)
        passes = [str(p) for p in (stage.raw.get("passes") or [])]
        sections: list[str] = []
        for pass_name in passes:
            prompt = _fill(_template_for(config, agent_id, stage.name), pass_name=pass_name,
                           draft=texts.get(agent_id, ""), brief=brief, **texts)
            result = stream_chat(url, auth, model, [{"role": "user", "content": prompt}],
                                 int(meta.get("max_tokens", 4000)), float(meta.get("temperature", 0.2)))
            sections.append(f"## {pass_name}\n{result['content']}")
            worklog.write({"event": f"{stage.name}-pass", "pass": pass_name,
                           "elapsed_seconds": result["seconds"], "content_len": len(result["content"])})
            print(f"  pass {pass_name}: {result['seconds']}s, {len(result['content'])}c")
        out = worklog.artifact(names.get(agent_id, f"{stage.name}.md"), "\n\n".join(sections))
        results[agent_id] = {"content": "\n\n".join(sections), "artifact": str(out)}
        print(f"{stage.name} -> {out.name}")

    elif mode in ("single", "parallel-consolidate"):
        if mode == "parallel-consolidate":
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(stage.agents)) as pool:
                futures = {
                    pool.submit(_call_one, agent_id,
                                _fill(_template_for(config, agent_id, stage.name),
                                      brief=brief, **texts)): agent_id
                    for agent_id in stage.agents
                }
                for fut in concurrent.futures.as_completed(futures):
                    agent_id, result = fut.result()
                    results[agent_id] = result
                    worklog.write({"event": stage.name, "agent": agent_id, "mode": mode,
                                   "elapsed_seconds": result["seconds"],
                                   "content_len": len(result["content"])})
            consolidated = _dedupe([r["content"] for r in results.values() if r["content"]])
            out = worklog.artifact(names.get(stage.agents[0], f"{stage.name}.md"), "\n\n".join(consolidated))
            results["_consolidated"] = {"content": "\n\n".join(consolidated), "artifact": str(out),
                                        "deduped_from": len(results)}
            print(f"{stage.name} (consolidated: {len(consolidated)} sections)")
        else:
            agent_id = stage.agents[0]
            agent_id_, result = _call_one(agent_id,
                                          _fill(_template_for(config, agent_id, stage.name),
                                                brief=brief, **texts))
            results[agent_id] = result
            out = worklog.artifact(names.get(agent_id, f"{stage.name}.md"), result["content"])
            worklog.write({"event": stage.name, "agent": agent_id, "mode": mode,
                           "finish": result["finish"], "elapsed_seconds": result["seconds"],
                           "reasoning_len": len(result["reasoning"]),
                           "content_len": len(result["content"])})
            print(f"{stage.name}: {result['seconds']}s -> {out.name}")
    else:
        raise SystemExit(f"unknown stage mode {mode!r} (implemented: parallel, cross, "
                         "sequential-passes, single, parallel-consolidate)")

    after_errors = registry.fire("after-stage", config, stage, results, ctx)
    for err in after_errors:
        worklog.write({"event": "hook-error", "hook": "after-stage", "stage": stage.name, "error": err})
        print(f"  hook(after-stage) contained: {err}")

    return results


def _recall(config: PipelineConfig, token: str, agent_id: str, prompt: str, budget: int) -> dict[str, Any]:
    """Re-issue one call at a grown budget (escalation_retry's closure)."""
    url, model, meta, env_name = config.resolve_route(agent_id)
    auth = resolve_auth_token(env_name, explicit_token=token)
    return stream_chat(url, auth, model, [{"role": "user", "content": prompt}],
                       budget, float(meta.get("temperature", 0.8)))
