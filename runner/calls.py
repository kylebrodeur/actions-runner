"""LLM call surfaces: streamed chat + streamed completion.

Both capture the full streamed shape into a dict:

    {"content": str, "reasoning": str, "seconds": float, "finish": str}

Design law (measured on real model servers):
- stream:true is DEFAULT — proxies on some servers surface no reasoning
  non-streamed; a streamed request also survives Modal-style long-request
  redirects (follow_redirects=True).
- cold-boot retry: a server that was scaled to zero ReadTimeouts / accepts
  then hangs while weights load (~minutes). Retry with a backoff sized to
  the boot window; batch runners can wait, interactive callers cannot.
- budget-escalation policy hook: a call that returns NO content (finish
  length with only reasoning) did not fail — it ran out. The stage executor
  decides the retry budget (escalation_retry); the call surface stays policy-free.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx

DEFAULT_TIMEOUT = 480.0
DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 90.0  # cold-boot window


def _consume_chat_stream(resp: httpx.Response) -> tuple[str, str, str]:
    """Consume a chat SSE stream: returns (content, reasoning, finish)."""
    collected: dict[str, list[str]] = {}
    finish = ""
    for line in resp.iter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            for key, value in delta.items():
                if value:
                    collected.setdefault(key, []).append(str(value))
            if choice.get("finish_reason"):
                finish = str(choice["finish_reason"])
    return (
        "".join(collected.get("content", [])),
        "".join(collected.get("reasoning_content", [])),
        finish,
    )


def _consume_completion_stream(resp: httpx.Response) -> tuple[str, str, str]:
    """Consume a completion SSE stream: returns (content, '', finish).
    Completion endpoints have no reasoning channel and no chat template:
    the prompt is taken byte-exact (the contract lives at the caller)."""
    collected: list[str] = []
    finish = ""
    for line in resp.iter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices") or []:
            if choice.get("text"):
                collected.append(choice["text"])
            if choice.get("finish_reason"):
                finish = str(choice["finish_reason"])
    return "".join(collected), "", finish


def _streamed_call(
    url: str,
    token: str,
    body: dict[str, Any],
    *,
    consumer: Callable[[httpx.Response], tuple[str, str, str]],
    endpoint: str,
    timeout: float,
    retries: int,
    retry_delay: float,
) -> dict[str, Any]:
    finish = ""
    for attempt in range(retries + 1):
        content, reasoning, finish = "", "", ""
        t0 = time.perf_counter()
        try:
            with httpx.stream(
                "POST", url.rstrip("/") + endpoint,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                follow_redirects=True,
                json=body,
                timeout=timeout,
            ) as resp:
                resp.raise_for_status()
                content, reasoning, finish = consumer(resp)
            # A stream that ended with neither reasoning nor content = a cold
            # boot swallowed the request; treat as retryable.
            if not content and not reasoning and attempt < retries and finish != "stop":
                print(
                    f"  endpoint returned nothing (likely cold boot); "
                    f"retry {attempt + 1}/{retries} in {retry_delay:.0f}s",
                    flush=True,
                )
                time.sleep(retry_delay)
                continue
            return {"content": content, "reasoning": reasoning, "finish": finish,
                    "seconds": round(time.perf_counter() - t0, 1)}
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
            if attempt < retries:
                print(
                    f"  {type(exc).__name__} (server cold or slow); "
                    f"retry {attempt + 1}/{retries} in {retry_delay:.0f}s",
                    flush=True,
                )
                time.sleep(retry_delay)
                continue
            raise
    raise SystemExit(f"endpoint {url} did not answer after {retries + 1} attempts")


def stream_chat(url: str, token: str, model: str, messages: list[dict[str, Any]],
                max_tokens: int, temperature: float, *,
                timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES,
                retry_delay: float = DEFAULT_RETRY_DELAY) -> dict[str, Any]:
    """Streamed chat completion; captures reasoning_content + content."""
    return _streamed_call(
        url, token,
        {"model": model, "messages": messages, "max_tokens": max_tokens,
         "temperature": temperature, "stream": True},
        consumer=_consume_chat_stream, endpoint="/chat/completions",
        timeout=timeout, retries=retries, retry_delay=retry_delay,
    )


def stream_completion(url: str, token: str, model: str, prompt: str,
                      max_tokens: int, *, temperature: float = 1.0,
                      top_p: float | None = None,
                      timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES,
                      retry_delay: float = DEFAULT_RETRY_DELAY) -> dict[str, Any]:
    """Streamed text completion (the byte-exact-prompt contract surface).

    Only the knobs the body carries are sent: a contract that says "temp 1.0
    / top_p 0.95, nothing else" passes exactly those; other sampling knobs
    (top_k, min_p, repeat_penalty) are deliberately omitted — some engines
    apply defaults when a knob is present and the llama-family sends
    1.0/absent as different semantics.
    """
    body: dict[str, Any] = {"model": model, "prompt": prompt,
                            "max_tokens": max_tokens, "temperature": temperature,
                            "stream": True}
    if top_p is not None:
        body["top_p"] = top_p
    return _streamed_call(
        url, token, body,
        consumer=_consume_completion_stream, endpoint="/completions",
        timeout=timeout, retries=retries, retry_delay=retry_delay,
    )


def escalation_retry(recall: Callable[[int], dict[str, Any]],
                     first_result: dict[str, Any],
                     base_budget: int,
                     factor: int = 4,
                     max_rounds: int = 1) -> dict[str, Any]:
    """The budget-escalation policy: a call that burned the whole budget on
    reasoning and returned no prose did not fail — it ran out. Retry at
    factor x the budget (measured: reasoning-first models overrun on short
    briefs; the retry returns real prose at the grown budget).

    `recall(grown_budget)` re-issues the SAME call at the grown budget; the
    policy owns nothing about endpoints or messages — the caller closes over them.
    """
    result = first_result
    budget = base_budget
    for _ in range(max_rounds):
        if result["content"].strip():
            return result
        budget *= factor
        print(f"  reasoning {len(result['reasoning'])}c ate the budget; escalating to {budget} tokens", flush=True)
        result = recall(budget)
    return result
