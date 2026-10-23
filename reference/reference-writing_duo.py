#!/usr/bin/env python3
"""Local actions runner for the writing duo pipeline (no GitHub required).

Reads the same `actions/writing-duo.yml` config a CI runner would use, and
executes stages locally against OpenAI-compatible endpoints. One definition,
two runners (here, later CI).

Stages:
  draft         same brief -> both writers in parallel (streaming) -> draft artifacts
  counterparty  each writer reads the other's draft -> bridge/ending second takes
  review        reviewer's five separate passes + deterministic gates -> review.md + advice items
  apply         HUMAN-DECISION executor: --decision {accept,redraft,park}
                'accept' bumps only frontmatter machine fields; nothing else writes
                to publish-bound prose, ever.

Usage:
  writing_duo.py --config actions/writing-duo.yml --stage draft --brief piece.md
  writing_duo.py --config actions/writing-duo.yml --stage counterparty
  writing_duo.py --config actions/writing-duo.yml --stage review
  writing_duo.py --config actions/writing-duo.yml --stage apply --decision accept

Gates (non-negotiable, enforced before any output is considered done):
  dash_gate     zero U+2014/U+2013 in publish-bound text (exit-fail)
  human         nothing writes to publish-bound prose; 'accept' touches only
                frontmatter machine fields, and only when Kyle's decision is passed.

Env: MODAL_PROXY_TOKEN (bearer). Per-request records append to the worklog
JSONL from config.logging.worklog.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import concurrent.futures
from pathlib import Path

import httpx  # pip install httpx, or run with `uv run --with httpx`

DASH_CODEPOINTS = ("\u2014", "\u2013")  # em, en
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def load_config(path: Path) -> dict:
    import yaml  # uv run --with pyyaml

    return yaml.safe_load(path.read_text())


# ---------------------------------------------------------------- worklog


class Worklog:
    def __init__(self, config: dict, root: Path, piece_slug: str | None = None):
        rel = str(((config.get("logging") or {}).get("worklog")) or
                  "00-system/00.01-inbox/mci-writing-harness/worklog.jsonl")
        self.path = root / rel
        self.path.parent.mkdir(parents=True, exist_ok=True)
        art = str(((config.get("logging") or {}).get("artifacts")) or
                  "00-system/00.01-inbox/mci-writing-harness/artifacts")
        # Per-piece artifacts: no slug = the shared legacy dir (back-compat);
        # a slug gives that piece its own artifacts folder so multi-piece
        # runs never overwrite each other's drafts/bridges/reviews.
        self.art_root = root / art / piece_slug if piece_slug else root / art
        self.art_root.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"recorded_at": time.time(), **record}, ensure_ascii=False) + "\n")

    def artifact(self, name: str, content: str) -> Path:
        p = self.art_root / name
        p.write_text(content, encoding="utf-8")
        return p


# ---------------------------------------------------------------- client


def stream_completion(url: str, token: str, model: str, messages: list[dict],
                      max_tokens: int, temperature: float, timeout: float = 480.0,
                      retries: int = 2, retry_delay: float = 90.0):
    """Streamed chat completion; captures reasoning_content + content. stream is
    MANDATORY: non-stream responses surface no reasoning_content on this proxy.

    Cold-lane retries: a scaled-down Modal lane ReadTimeouts/ConnectTimeouts
    (or accepts then hangs) while booting weights (~3 min). Retry with a
    backoff sized to the boot window; the runner is batch, not interactive."""
    collected: dict[str, list[str]] = {}
    for attempt in range(retries + 1):
        collected = {}
        t0 = time.perf_counter()
        try:
            with httpx.stream(
                "POST", url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                follow_redirects=True,  # Modal proxy 303s long requests with an attempt token
                json={"model": model, "messages": messages, "max_tokens": max_tokens,
                      "temperature": temperature, "stream": True},
                timeout=timeout,
            ) as resp:
                resp.raise_for_status()
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
            reason = "".join(collected.get("reasoning_content", []))
            content = "".join(collected.get("content", []))
            # A stream that ended with neither reasoning nor content = cold boot
            # swallowed the request; treat as retryable.
            if not reason and not content and attempt < retries:
                print(f"  lane returned nothing (likely cold boot); retry {attempt + 1}/{retries} in {retry_delay:.0f}s", flush=True)
                time.sleep(retry_delay)
                continue
            return {"reasoning": reason, "content": content,
                    "seconds": round(time.perf_counter() - t0, 1)}
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
            if attempt < retries:
                print(f"  {type(exc).__name__} (lane cold or slow); retry {attempt + 1}/{retries} in {retry_delay:.0f}s", flush=True)
                time.sleep(retry_delay)
                continue
            raise
    raise SystemExit(f"lane {url} did not answer after {retries + 1} attempts")


def agent_lane(config: dict, agent_id: str) -> tuple[str, str, dict]:
    """agent-id -> (url, model, meta) resolved from defaults lanes + cloud_lane."""
    defaults = config.get("defaults") or {}
    lanes = defaults.get("lanes") or {}
    agents = AGENT_ROUTES.get(agent_id) or {}
    lane_name = str(agents.get("lane", ""))
    if lane_name == "cloud-lane":
        cloud = defaults.get("cloud_lane") or {}
        url = str(cloud.get("url", ""))
        if not url:
            raise SystemExit("cloud reviewer configured but defaults.cloud_lane.url missing")
        return url, str(agents.get("model", "")), agents
    if lane_name not in lanes:
        raise SystemExit(f"agent {agent_id!r}: lane {lane_name!r} not in config lanes")
    return lanes[lane_name], str(agents.get("model", "")), agents


def _cloud_token(config: dict) -> str:
    bearer_env = str(((config.get("defaults") or {}).get("cloud_lane") or {}).get("bearer_env", "OLLAMA_API_KEY"))
    token = os.environ.get(bearer_env, "").strip()
    if not token:
        raise SystemExit(f"{bearer_env} must be in the environment for the cloud critic lane (1Password 'Ollama API Key')")
    return token


# Profile routes: the models + lane each agent name maps to. Profiles in
# agents/*.md carry the human-readable contracts; this table is the runner's
# routing half of the same source of truth.
AGENT_ROUTES: dict[str, dict] = {
    "writer-hemmingway": {
        "model": "hemmingway-1",
        "lane": "hemmingway-lane",
        "max_tokens": 8000,
        "temperature": 0.8,
    },
    "writer-eclipse-seed": {
        "model": "novelist-eclipse-31b",
        "lane": "eclipse-lane",
        "max_tokens": 2000,
        "temperature": 0.8,
        "voice_transform": True,
    },
    "reviewer-gemini": {
        "model": "gemma3:27b",
        "lane": "cloud-lane",
        "max_tokens": 4000,
        "temperature": 0.2,
    },
    # Cloud critics (Ollama Cloud; measured budgets from the 2026-10-04 probe):
    # glm-5.3-flash needs 12,000 tokens (20.6K reasoning chars), deepseek 12,000
    # (17K); both non-stream with the `reasoning` field captured. At 4K glm
    # silently burns its budget (finish=length, content empty).
    "critic-glm": {
        "model": "glm-5.3-flash",
        "lane": "cloud-lane",
        "max_tokens": 20000,
        "temperature": 0.2,
    },
    "critic-deepseek": {
        "model": "deepseek-v4.1-flash",
        "lane": "cloud-lane",
        "max_tokens": 12000,
        "temperature": 0.2,
    },
    # Polisher (humanizer-12b): NOT a chat seat. The prompt contract is the
    # upstream byte-exact completion (prompt_format.json, fingerprint
    # cc51d66b4c593fbe): INSTR + draft + SEP, EOS-only stop, temp 1.0 /
    # top_p 0.95, no repeats of any other sampling knob. Routes to
    # /v1/completions via stream_polish (never through chat).
    "polisher-humanizer": {
        "model": "humanizer-12b",
        "lane": "polish-lane",
        "max_tokens": 2000,      # per-8K-ctx slice; runner splits long drafts
        "temperature": 1.0,
        "top_p": 0.95,
        "byte_exact": True,
    },
}

ECLIPSE_TRANSFORM = (
    "\n\nBefore emitting publish-bound prose:\n"
    "- cut adjectives by half\n"
    "- remove any image you have seen used before\n"
    "- one metaphor per beat, maximum, earned by the material\n"
    "- if a line sounds writerly, make it plainer until it sounds observed\n"
)

COUNTERPARTY_PROMPT = (
    "Another writer took this same brief and produced the draft below. Read it, then "
    "propose a BETTER bridge into the piece's ending plus one alternative closing "
    "image - informed second take, not a rewrite of their draft and not a merge. "
    "Two to four sentences, publish-bound quality, no preamble.\n\n"
    "=== their draft ===\n{other}\n\n=== end draft ==="
)


# ---------------------------------------------------------------- stages


def stage_draft(config: dict, brief_path: Path, token: str, worklog: Worklog) -> None:
    brief = brief_path.read_text(encoding="utf-8")
    jobs = []
    for agent_id in ("writer-hemmingway", "writer-eclipse-seed"):
        url, model, meta = agent_lane(config, agent_id)
        messages = [{"role": "user", "content": brief + (ECLIPSE_TRANSFORM if meta.get("voice_transform") else "")}]
        jobs.append((agent_id, url, model, meta, messages))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(stream_completion, url, token, model, messages,
                        int(meta["max_tokens"]), float(meta["temperature"])): (agent_id, meta, messages)
            for agent_id, url, model, meta, messages in jobs
        }
        for fut in concurrent.futures.as_completed(futures):
            agent_id, meta, messages = futures[fut]
            result = fut.result()
            # Budget escalation: a writer that burns the whole budget on
            # reasoning and returns no prose did not fail - it ran out. Retry
            # ONCE at 4x the budget (measured 2026-10-04: eclipse burned 2K on
            # 7K reasoning chars on a one-line brief; hemmingway's contract
            # already sizes for this).
            if not result["content"].strip():
                grow = int(meta["max_tokens"]) * 4
                print(f"  {agent_id}: reasoning {len(result['reasoning'])}c ate the budget; escalating to {grow} tokens", flush=True)
                result = _redraft_at_budget(config, agent_id, messages, grow, token)
            dash_hits = sum(result["content"].count(c) for c in DASH_CODEPOINTS)
            record = {
                "event": "draft", "agent": agent_id, "model": meta["model"],
                "max_tokens": meta["max_tokens"], "elapsed_seconds": result["seconds"],
                "reasoning_len": len(result["reasoning"]), "content_len": len(result["content"]),
                "dash_gate_hits": dash_hits,
                "budget_note": "ok" if result["content"].strip() else "escalated-4x still empty",
            }
            worklog.write(record)
            out = worklog.artifact(f"draft-{agent_id}.md", result["content"])
            print(f"{agent_id}: {result['seconds']}s, reasoning {len(result['reasoning'])}c, "
                  f"prose {len(result['content'])}c, dash_hits={dash_hits} -> {out.name}")


def _redraft_at_budget(config: dict, agent_id: str, messages: list[dict], grow: int, token: str) -> dict:
    url, model, meta = agent_lane(config, agent_id)
    return stream_completion(url, token, model, messages, grow, float(meta["temperature"]))


# ---------------------------------------------------------------- polish


# The upstream byte-exact prompt contract (humanizer/prompt_format.json,
# verified fingerprint cc51d66b4c593fbe on load). "legacy" style: build the
# completion prompt from the draft; nothing may be reworded, no chat turns,
# no system prompt - the model was trained on this exact byte sequence.
HU_INSTR = (
    "Rewrite the text below so it reads like a person wrote it, not a language model.\n"
    "\n"
    "Reorganize it as you see fit. Vary sentence length on purpose. Cut hedging,\n"
    "throat-clearing, and any sentence that only announces what comes next.\n"
    "Prefer the concrete word over the abstract one. It is fine to sound uneven.\n"
    "\n"
    "Every fact, number, unit, date, name and quotation must survive unchanged."
)
HU_SEP = "\n\n### Rewritten:\n\n"
HU_FINGERPRINT = "cc51d66b4c593fbe"


def build_hu_prompt(draft: str) -> str:
    prompt = HU_INSTR + "\n\n" + draft.strip() + HU_SEP
    import hashlib

    fp = hashlib.sha256((HU_INSTR + "\n\nX" + HU_SEP).encode("utf-8")).hexdigest()[:16]
    if fp != HU_FINGERPRINT:
        raise SystemExit(f"humanizer prompt contract drifted (fingerprint {fp})")
    return prompt


_CJK_RANGE = (0x4E00, 0x9FFF)


def _looks_chinese(text: str) -> bool:
    return sum(1 for c in text if _CJK_RANGE[0] <= ord(c) <= _CJK_RANGE[1]) > 3


def stream_polish(url: str, token: str, model: str, prompt: str, max_tokens: int,
                  temperature: float = 1.0, top_p: float = 0.95,
                  timeout: float = 480.0, retries: int = 2, retry_delay: float = 90.0,
                  resamples: int = 3) -> dict:
    """The polish call: /v1/completions (NOT chat), EOS-only stop, contract temps.

    Humanizer contract quirks handled here (per the model card):
    - completion endpoint with the byte-exact prompt (chat front ends work but
      the authors have not tested them; completion is the contract);
    - temperature 1.0 / top_p 0.95 / NOTHING else (top_k 0, min_p 0,
      repeat_penalty 1.0 = omit entirely, they are llama defaults-when-sent);
    - language check: an English draft occasionally comes back in Chinese ->
      resample same settings (up to 3);
    - numbers/units/dates/names must survive: the caller does the fact scan
      (see polish_fact_check) and treats a mismatch as resample-worthy.
    Streaming: llama.cpp streams text_completion deltas fine; streamed for
    the same cold-boot retry posture as chat.
    """
    collected: dict[str, list[str]] = {}
    last_finish = ""
    for attempt in range(retries + 1):
        collected = {}
        t0 = time.perf_counter()
        try:
            with httpx.stream(
                "POST", url.rstrip("/") + "/completions",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                follow_redirects=True,
                json={"model": model, "prompt": prompt, "max_tokens": max_tokens,
                      "temperature": temperature, "top_p": top_p, "stream": True},
                timeout=timeout,
            ) as resp:
                resp.raise_for_status()
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
                            collected.setdefault("text", []).append(choice["text"])
                        if choice.get("finish_reason"):
                            last_finish = str(choice["finish_reason"])
            content = "".join(collected.get("text", []))
            if not content and last_finish != "stop" and attempt < retries:
                print(f"  polish lane returned nothing (likely cold boot); retry {attempt + 1}/{retries} in {retry_delay:.0f}s", flush=True)
                time.sleep(retry_delay)
                continue
            return {"content": content, "finish": last_finish,
                    "seconds": round(time.perf_counter() - t0, 1)}
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
            if attempt < retries:
                print(f"  {type(exc).__name__} (lane cold or slow); retry {attempt + 1}/{retries} in {retry_delay:.0f}s", flush=True)
                time.sleep(retry_delay)
                continue
            raise
    raise SystemExit(f"polish lane {url} did not answer after {retries + 1} attempts")


def polish_draft(config: dict, draft: str, token: str) -> dict:
    """Polish one draft through the humanizer seat: chunk to the 8K ctx, call,
    resample per contract (language slip; fact-preservation mismatch), join.

    Chunking: the ctx is 8192 tokens; the instruction + separator eat ~120,
    output needs headroom. Draft slices of <=12,000 chars (the same cap
    edit-pass uses) with max_tokens 2000 stay inside the window; a longer
    draft is polished per-slice with a ~2-sentence overlap-free stitch at
    paragraph boundaries (the model reorganizes within a slice; cross-slice
    continuity is Kyle's read, not assumed).
    """
    url, model, meta = agent_lane(config, "polisher-humanizer")
    max_tokens = int(meta["max_tokens"])
    temp = float(meta["temperature"])
    top_p = float(meta.get("top_p", 0.95))

    sliced = draft.strip()[:12000]
    prompt = build_hu_prompt(sliced)
    for attempt in range(4):
        result = stream_polish(url, token, model, prompt, max_tokens, temp, top_p)
        out = result["content"].strip()
        if not out:
            continue
        if _looks_chinese(out):
            print(f"  polish: language slip (en draft, zh rewrite); resample {attempt + 1}", flush=True)
            continue
        return result
    return result


DEEP_READ_PROMPT = (
    "You are the deep-read critic. The reviewer's five passes produced the findings below; "
    "the draft follows. (1) Consolidate: dedupe repeated advice, flag pass-conflicts with "
    "your verdict. (2) Structural findings the passes cannot see: problem arrival, "
    "performative voice, spine soundness. (3) Prioritized ADVICE for the deep writer, "
    "max 6, each 'location -> observation -> direction -> cited rule'. Never rewrite the "
    "piece.\n\n=== passes ===\n{passes}\n\n=== draft ===\n{draft}"
)


def _primary_writer(config: dict) -> str:
    """The lane-winning seat the review/redraft loop anchors on.

    options:
      (b) lane-ownership: yml decides; no code needed. The pipeline yml carries
      primary_writer: writer-eclipse-seed (the A/B sweep winner 2026-10-05).
    """
    return str(((config.get("defaults") or {}).get("primary_writer")) or "writer-hemmingway")


def stage_deep_read(config: dict, token: str, worklog: Worklog) -> None:
    hybrid = worklog.art_root / "draft-writer-hybrid.md"
    draft = hybrid if hybrid.exists() else worklog.art_root / f"draft-{_primary_writer(config)}.md"
    review = worklog.art_root / "review.md"
    for need, path in (("draft", draft), ("review passes", review)):
        if not need or not path.exists():
            raise SystemExit(f"missing {need}: {path.name}; run the earlier stages first")
    cloud_token = _cloud_token(config)
    # Cloud thinking models on THIS lane: non-stream, 12K budget, reasoning in
    # the `reasoning` field (measured). stream_completion handles both fields?
    # No - it only sums reasoning_content. Use a dedicated non-stream call that
    # captures the `reasoning` field and the final content.
    import concurrent.futures
    def read(agent_id: str):
        url, model, meta = agent_lane(config, agent_id)
        t0 = time.perf_counter()
        r = httpx.post(url.rstrip("/") + "/chat/completions",
                       headers={"Authorization": f"Bearer {cloud_token}", "Content-Type": "application/json"},
                       timeout=570,
                       follow_redirects=True,  # Modal proxy 303s long requests
                       json={"model": model,
                             "messages": [{"role": "user", "content": DEEP_READ_PROMPT.format(
                                 passes=review.read_text(encoding="utf-8")[:40000],
                                 draft=draft.read_text(encoding="utf-8")[:16000])}],
                             "max_tokens": int(meta["max_tokens"]), "temperature": 0.2})
        d = r.json()
        ch = (d.get("choices") or [{}])[0]
        m = ch.get("message") or {}
        return {
            "model": model,
            "content": str(m.get("content") or ""),
            "reasoning_len": len(str(m.get("reasoning") or m.get("reasoning_content") or "")),
            "finish": ch.get("finish_reason"),
            "seconds": round(time.perf_counter() - t0, 1),
        }
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futs = {pool.submit(read, "critic-glm"): "critic-glm",
                pool.submit(read, "critic-deepseek"): "critic-deepseek"}
        sections = []
        for fut in concurrent.futures.as_completed(futs):
            cid = futs[fut]
            result = fut.result()
            if not result["content"].strip():
                print(f"  WARNING {cid}: content empty (finish={result['finish']}) - budget or lane issue; see worklog")
            sections.append(f"## {cid} ({result['seconds']}s, reasoning {result['reasoning_len']}c, finish={result['finish']})\n\n{result['content']}")
            worklog.write({"event": "deep-read", "agent": cid, "model": result["model"],
                           "seconds": result["seconds"], "reasoning_len": result["reasoning_len"],
                           "content_len": len(result["content"]), "finish": result["finish"]})
            print(f"  {cid} ({result['model']}): {result['seconds']}s, reasoning {result['reasoning_len']}c, {len(result['content'])}c")
    out = worklog.artifact("deep-read.md", "\n\n".join(sections))
    print(f"deep-read -> {out.name} (advice prioritized; HUMAN GATE next)")


def stage_counterparty(config: dict, token: str, worklog: Worklog) -> None:
    drafts = {
        "writer-hemmingway": (worklog.art_root / "draft-writer-hemmingway.md"),
        "writer-eclipse-seed": (worklog.art_root / "draft-writer-eclipse-seed.md"),
    }
    missing = [str(p) for p in drafts.values() if not p.exists()]
    if missing:
        raise SystemExit(f"missing drafts; run --stage draft first: {missing}")
    cross = list(drafts.items())
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {}
        for agent_id, path in cross:
            url, model, meta = agent_lane(config, agent_id)
            other = drafts["writer-eclipse-seed" if agent_id == "writer-hemmingway" else "writer-hemmingway"].read_text(encoding="utf-8")
            messages = [{"role": "user", "content": COUNTERPARTY_PROMPT.format(other=other[:6000])}]
            futures[pool.submit(stream_completion, url, token, model, messages,
                                int(meta["max_tokens"]), 0.7)] = agent_id
        for fut in concurrent.futures.as_completed(futures):
            agent_id = futures[fut]
            result = fut.result()
            out = worklog.artifact(f"bridge-{agent_id}.md", result["content"])
            worklog.write({"event": "counterparty", "agent": agent_id,
                           "elapsed_seconds": result["seconds"], "content_len": len(result["content"])})
            print(f"{agent_id} bridge: {result['seconds']}s, {len(result['content'])}c -> {out.name}")


HYBRID_PROMPT = (
    "Two writers took this same brief. The first is the FRAME - its structure, "
    "voice, and pacing won the read. The second is the FACTS SEAT - its points, "
    "dates, named objects, and build details are the strongest part of its draft. "
    "Also included: the human's editorial notes. Produce THE WORKING COPY: keep the "
    "frame's structure and voice verbatim where it is good, work the facts seat's "
    "specific material in where it strengthens the piece, honor every editorial "
    "note. Publish-bound quality, no preamble, no meta-commentary.\n\n"
    "=== frame draft (primary) ===\n{frame}\n\n"
    "=== facts draft (extraction source) ===\n{facts}\n\n"
    "=== editorial notes ===\n{notes}\n\n"
    "=== end ==="
)


def stage_hybrid(config: dict, token: str, worklog: Worklog, brief_path: Path) -> None:
    """Option (c): frame(primary) + facts(second) + Kyle's notes -> working copy.

    Frame = primary_writer's draft; facts = the other writer's; notes =
    Kyle's marked-up copy of the frame (KB notes + marks) if present.
    The working copy lands as draft-writer-hybrid.md and becomes the
    review anchor (overriding primary_writer for THIS piece's loop).
    """
    primary = _primary_writer(config)
    secondary = "writer-hemmingway" if primary == "writer-eclipse-seed" else "writer-eclipse-seed"
    frame_p = worklog.art_root / f"draft-{primary}.md"
    facts_p = worklog.art_root / f"draft-{secondary}.md"
    missing = [str(p) for p in (frame_p, facts_p) if not p.exists()]
    if missing:
        raise SystemExit(f"missing drafts: {missing}; run --stage draft first")

    # Kyle's notes: prefer a marked-up copy he wrote in Obsidian
    notes = ""
    kyle_copy = worklog.art_root / "working-copy.md"
    if kyle_copy.exists():
        notes = kyle_copy.read_text(encoding="utf-8")
    else:
        # fall back to the piece brief's KB notes if the piece itself carries any
        notes = "(no marked-up working copy found; work from the frame + facts alone)"

    frame = frame_p.read_text(encoding="utf-8")
    facts = facts_p.read_text(encoding="utf-8")

    url, model, meta = agent_lane(config, primary)
    messages = [{"role": "user", "content": HYBRID_PROMPT.format(
        frame=frame[:8000], facts=facts[:6000], notes=notes[:4000])}]
    # hybrid = careful work; budget between the seats' contracts
    result = stream_completion(url, token, model, messages, 8000, 0.6)
    if not result["content"].strip():
        result = _redraft_at_budget(config, primary, messages, 16000, token)
    dash_hits = sum(result["content"].count(c) for c in DASH_CODEPOINTS)
    worklog.write({"event": "hybrid", "frame": primary, "facts": secondary,
                   "elapsed_seconds": result["seconds"],
                   "reasoning_len": len(result["reasoning"]),
                   "content_len": len(result["content"]),
                   "dash_gate_hits": dash_hits})
    out = worklog.artifact(f"draft-writer-hybrid.md", result["content"])
    print(f"hybrid: {result['seconds']}s, prose {len(result['content'])}c, dash_hits={dash_hits} -> {out.name}")
    print("next: --stage review anchors on the hybrid (set primary_writer: writer-hybrid in the yml OR the review falls back automatically)")


EDIT_PASS_PROMPT = (
    "You are the EDIT pass. The writer's draft follows, with the author's "
    "[[KB: ...]] marking notes inline. Do an editor's pass: honor every note, "
    "cut and tighten, plain over pretty, vary the structure where it reads "
    "AI-generated. Do NOT rewrite what is working; do NOT add new claims, "
    "facts, or scenes; preserve the author's voice and the piece's spine. "
    "Remove the [[KB: ...]] markers as you resolve them; keep any <mark> "
    "sections only if the cut improves the piece. Publish-bound quality, "
    "no preamble.\n\n=== marked draft ===\n{draft}"
)


def stage_edit_pass(config: dict, token: str, worklog: Worklog, brief_path: Path | None = None) -> None:
    """The real Kyle workflow: hemmingway drafts -> Kyle marks -> eclipse edits honoring the marks."""
    # marked draft: prefer kyle-marked working copy, else the drafting seat's artifact
    draft_seat = _primary_writer(config)  # = drafting seat
    marked = None
    for cand in ("working-copy.md", f"draft-{draft_seat}.md"):
        c = worklog.art_root / cand
        if c.exists():
            marked = c
            break
    if not marked:
        raise SystemExit("no marked draft or draft artifact; run --stage draft first")
    draft_text = marked.read_text(encoding="utf-8")
    if "[[KB:" not in draft_text and "<mark>" not in draft_text:
        print("NOTE: no [[KB:]]/<mark> found in the marked draft; editing plain")
    url, model, meta = agent_lane(config, "writer-eclipse-seed")
    messages = [{"role": "user", "content": EDIT_PASS_PROMPT.format(draft=draft_text[:12000])}]
    result = stream_completion(url, token, model, messages, int(meta["max_tokens"]) * 2, 0.6)
    if not result["content"].strip():
        result = _redraft_at_budget(config, "writer-eclipse-seed", messages, int(meta["max_tokens"]) * 4, token)
    dash_hits = sum(result["content"].count(c) for c in DASH_CODEPOINTS)
    worklog.write({"event": "edit-pass", "marked_source": marked.name,
                   "agent": "writer-eclipse-seed", "model": meta["model"],
                   "elapsed_seconds": result["seconds"],
                   "reasoning_len": len(result["reasoning"]),
                   "content_len": len(result["content"]),
                   "dash_gate_hits": dash_hits})
    out = worklog.artifact("edit-pass.md", result["content"])
    print(f"edit-pass: {result['seconds']}s, prose {len(result['content'])}c, dash_hits={dash_hits} -> {out.name}")
    print("next: gates + review; HUMAN GATE after")




def stage_polish(config: dict, token: str, worklog: Worklog, brief_path: Path | None = None) -> None:
    """POLISH stage: the humanizer seat de-AI-rewrites the current best text.

    Position in the loop: AFTER the human gate's redraft/apply settled the
    prose (edit-pass or accepted working copy), BEFORE the dash gate re-run
    + frontmatter bump. Kyle's prose is NEVER polished in place: polish
    produces polish.md beside the piece artifacts for Kyle to diff and
    choose from (human gate owns prose, D-056 shape).

    Input precedence: working-copy.md (post-edit-pass) > edit-pass.md >
    draft-<primary_writer>.md.
    """
    for cand in ("working-copy.md", "edit-pass.md", f"draft-{_primary_writer(config)}.md"):
        c = worklog.art_root / cand
        if c.exists():
            src = c
            break
    else:
        raise SystemExit("no draft to polish; run --stage draft (and ideally edit-pass) first")
    draft_text = src.read_text(encoding="utf-8")
    result = polish_draft(config, draft_text, token)
    dash_hits = sum(result["content"].count(c) for c in DASH_CODEPOINTS)
    worklog.write({"event": "polish",
                   "source": src.name,
                   "model": AGENT_ROUTES["polisher-humanizer"]["model"],
                   "elapsed_seconds": result["seconds"],
                   "finish": result["finish"],
                   "content_len": len(result["content"]),
                   "dash_gate_hits": dash_hits})
    out = worklog.artifact("polish.md", result["content"])
    print(f"polish: {result['seconds']}s, {result['finish']}, prose {len(result['content'])}c, dash_hits={dash_hits} -> {out.name}")
    print("next: dash-gate + Kyle diffs polish.md against the source (human gate owns prose)")


def stage_review(config: dict, token: str, worklog: Worklog, brief_path: Path | None = None) -> None:
    hybrid = worklog.art_root / "draft-writer-hybrid.md"
    draft = hybrid if hybrid.exists() else worklog.art_root / f"draft-{_primary_writer(config)}.md"
    if not draft.exists():
        raise SystemExit("no deep draft; run --stage draft first")
    # Deterministic gates FIRST (fail fast, before spending reviewer tokens):
    # the ported publish validator (title drift, KB notes, marks, images,
    # dashes) then the standalone dash gate on the artifact.
    import subprocess, sys
    pkg_root = Path(__file__).resolve().parent.parent
    for gate in ("validate_publish_markdown.py", "dash_gate.py"):
        r = subprocess.run([sys.executable, str(pkg_root / "scripts" / gate), str(draft)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout + r.stderr)
            raise SystemExit(f"{gate} FAILED on the draft; fix before review tokens are spent")
    url, model, meta = agent_lane(config, "reviewer-gemini")
    auth = _cloud_token(config) if str(meta.get("lane", "")) == "cloud-lane" else token
    passes = ["source and fact", "grammar and tense", "voice", "formatting", "optional cuts"]
    advice: list[str] = []
    for pass_name in passes:
        prompt = (
            f"Pass: {pass_name.upper()} (run ONLY this pass). Audit the draft below per the "
            "content-writing-system detect mode and the Voice-And-Tone guide. Report findings as "
            "evidence, mark optional cuts with <mark>, and emit ADVICE items "
            "(location -> observation -> suggested direction -> cited rule, max 8) for the deep "
            "writer to redraft from. You never rewrite the piece.\n\n=== draft ===\n"
            + draft.read_text(encoding="utf-8")[:9000]
        )
        result = stream_completion(url, auth, model, [{"role": "user", "content": prompt}],
                                   int(meta["max_tokens"]), 0.2)
        advice.append(f"## {pass_name}\n{result['content']}")
        worklog.write({"event": "review-pass", "pass": pass_name, "elapsed_seconds": result["seconds"],
                       "content_len": len(result["content"])})
        print(f"  pass {pass_name}: {result['seconds']}s, {len(result['content'])}c")
    review = worklog.artifact("review.md", "\n\n".join(advice))
    print(f"review -> {review.name} (advice routed to writer-hemmingway; HUMAN GATE next)")


def stage_apply(config: dict, decision: str, brief_path: Path, worklog: Worklog) -> None:
    if decision == "redraft":
        print("redraft: re-run --stage draft with review.md's advice items; the deep writer treats them as material.")
        return
    if decision == "park":
        worklog.write({"event": "human-gate", "decision": "park"})
        print("parked; frontmatter untouched")
        return
    if decision != "accept":
        raise SystemExit(f"unknown decision {decision!r}")
    text = brief_path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SystemExit("piece has no frontmatter; refusing to invent one")
    block = m.group(1)
    bumps = {"stage": "editing"}  # stage moves forward one step; machine field wins
    for key, value in bumps.items():
        key_re = re.compile(rf"^{key}:.*$", re.M)
        if key_re.search(block):
            block = key_re.sub(f"{key}: {value}", block, count=1)
        else:
            block += f"\n{key}: {value}"
    worklog.write({"event": "human-gate", "decision": "accept", "frontmatter_updates": bumps})
    (brief_path.parent / (brief_path.stem + ".accepted.md")).write_text(
        text.replace(m.group(1), block), encoding="utf-8")
    print("frontmatter machine fields bumped (accepted copy written beside the piece); prose untouched")


# ---------------------------------------------------------------- gates + main


def enforce_dash_gate(brief_path: Path) -> int:
    """Deterministic publish-bound dash check; exit 1 on any hit."""
    text = brief_path.read_text(encoding="utf-8") if brief_path.exists() else ""
    hits = [(i, c) for i, line in enumerate(text.splitlines(), 1) for c in DASH_CODEPOINTS if c in line]
    if hits:
        print("DASH GATE FAIL:", [(line, "em-dash" if c == DASH_CODEPOINTS[0] else "en-dash") for line, c in hits[:8]])
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Local runner for the writing-duo pipeline")
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--stage", required=True,
                    choices=["draft", "counterparty", "review", "deep-read", "hybrid", "edit-pass", "polish", "apply", "dash-gate"])
    ap.add_argument("--brief", type=Path, help="piece path or brief file (draft/apply stages)")
    ap.add_argument("--decision", choices=["accept", "redraft", "park"], help="apply stage only")
    args = ap.parse_args()

    token = os.environ.get("MODAL_PROXY_TOKEN", "").strip()
    config = load_config(args.config)
    root = args.config.resolve().parents[1]
    piece_slug = args.brief.stem if args.brief else None
    worklog = Worklog(config, root, piece_slug)

    if args.stage == "dash-gate":
        if not args.brief:
            raise SystemExit("--brief required for dash-gate")
        return enforce_dash_gate(args.brief)

    if not token:
        raise SystemExit("MODAL_PROXY_TOKEN must be in the environment (vault-mind.env for vault sessions)")

    if args.stage == "draft":
        if not args.brief or not args.brief.exists():
            raise SystemExit("--brief must point at an existing file")
        gate = enforce_dash_gate(args.brief)
        if gate:
            return gate
        stage_draft(config, args.brief, token, worklog)
    elif args.stage == "counterparty":
        stage_counterparty(config, token, worklog)
    elif args.stage == "edit-pass":
        stage_edit_pass(config, token, worklog, args.brief)
    elif args.stage == "polish":
        stage_polish(config, token, worklog, args.brief)
    elif args.stage == "hybrid":
        if not args.brief or not args.brief.exists():
            raise SystemExit("--brief must point at the piece for the hybrid stage")
        stage_hybrid(config, token, worklog, args.brief)
    elif args.stage == "review":
        stage_review(config, token, worklog, args.brief)
    elif args.stage == "deep-read":
        stage_deep_read(config, token, worklog)
    elif args.stage == "apply":
        if not args.decision:
            raise SystemExit("--decision required for apply")
        if not args.brief:
            raise SystemExit("--brief required for apply")
        stage_apply(config, args.decision, args.brief, worklog)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())