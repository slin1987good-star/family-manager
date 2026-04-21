#!/usr/bin/env python3
"""
Family Manager AI Worker

Runs on the Mac as a LaunchAgent. Polls the Fly.io backend for pending AI
analysis jobs and dispatches each prompt to an Anthropic-compatible proxy
(e.g. tdyun.ai, api.anthropic.com directly, or any New API gateway) using a
stable API key.

Env vars:
  FAMILY_API            default https://gw-family-manager.fly.dev
  FAMILY_WORKER_TOKEN   required; same value as WORKER_TOKEN secret on Fly
  LLM_API_KEY           required; Anthropic-compatible sk-... key
  LLM_BASE_URL          default https://api.anthropic.com
  LLM_MODEL             default claude-sonnet-4-6
"""
import json
import os
import sys
import time
import traceback
from urllib import request as urlrequest
from urllib.error import HTTPError

BASE = os.environ.get("FAMILY_API", "https://gw-family-manager.fly.dev")
WORKER_TOKEN = os.environ.get("FAMILY_WORKER_TOKEN", "")
API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.anthropic.com").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
POLL_IDLE_SECONDS = 20
POLL_BUSY_SECONDS = 2
CLAUDE_TIMEOUT = 120
MAX_OUTPUT_TOKENS = 1024

# Backoff state for rate-limit storms.
_rate_limit_cooldown_until = 0.0  # unix seconds

if not WORKER_TOKEN:
    print("FAMILY_WORKER_TOKEN required", file=sys.stderr)
    sys.exit(1)
if not API_KEY:
    print("LLM_API_KEY required", file=sys.stderr)
    sys.exit(1)


def _backend(method: str, path: str, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urlrequest.Request(
        f"{BASE}{path}", method=method, data=data,
        headers={
            "Content-Type": "application/json",
            "X-Worker-Token": WORKER_TOKEN,
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=30) as r:
            if r.status == 204:
                return None
            body = r.read()
            return json.loads(body) if body else None
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"backend HTTP {e.code}: {body[:300]}") from e


def next_job():
    return _backend("GET", "/api/worker/jobs/next")


def complete(job_id, result):
    return _backend("POST", f"/api/worker/jobs/{job_id}/complete", {"result": result})


def fail(job_id, error):
    return _backend("POST", f"/api/worker/jobs/{job_id}/fail", {"error": error})


def call_anthropic(prompt: str) -> str:
    """
    Call <LLM_BASE_URL>/v1/messages with an Anthropic-compatible API key.
    Works with the direct api.anthropic.com endpoint or any New-API / OpenAI-
    proxy-style gateway that forwards the Anthropic message format (x-api-key
    header, Anthropic JSON schema).
    """
    payload = {
        "model": MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": "You are a caring family assistant. Always reply in valid JSON exactly as requested.",
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urlrequest.Request(
        f"{LLM_BASE_URL}/v1/messages",
        method="POST", data=json.dumps(payload).encode(),
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=CLAUDE_TIMEOUT) as r:
            body = json.loads(r.read())
    except HTTPError as e:
        err_body = e.read().decode(errors="replace")
        raise RuntimeError(f"LLM HTTP {e.code}: {err_body[:400]}") from e

    # response.content is a list of {type:"text", text:"..."}
    parts = [b["text"] for b in body.get("content", []) if b.get("type") == "text"]
    return "".join(parts)


def extract_json(text: str):
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if 0 <= start < end:
            return json.loads(t[start:end + 1])
        raise


def process(job):
    global _rate_limit_cooldown_until
    print(f"[worker] job {job['id']} · {job['job_type']} · attempt {job['attempts']}", flush=True)
    try:
        raw = call_anthropic(job["prompt"])
        parsed = extract_json(raw)
        complete(job["id"], parsed)
        print(f"[worker] job {job['id']} done: {parsed.get('summary', '')[:60]}", flush=True)
    except Exception as e:
        msg = str(e)
        tb = traceback.format_exc()[-600:]
        fail(job["id"], f"{type(e).__name__}: {msg}\n---\n{tb}")
        print(f"[worker] job {job['id']} failed: {msg}", file=sys.stderr, flush=True)
        # If it's a 429, park the worker for 10 min so we stop burning retries.
        if "429" in msg or "rate_limit" in msg.lower():
            _rate_limit_cooldown_until = time.time() + 600
            print(f"[worker] rate-limited — cooling down for 10 min", file=sys.stderr, flush=True)


def main():
    global _rate_limit_cooldown_until
    print(f"[worker] started · API={BASE} · model={MODEL} · poll={POLL_IDLE_SECONDS}s", flush=True)
    while True:
        now = time.time()
        if now < _rate_limit_cooldown_until:
            remaining = int(_rate_limit_cooldown_until - now)
            print(f"[worker] cooldown, {remaining}s left", flush=True)
            time.sleep(min(60, remaining))
            continue
        try:
            job = next_job()
        except Exception as e:
            print(f"[worker] fetch error: {e}", file=sys.stderr, flush=True)
            time.sleep(POLL_IDLE_SECONDS)
            continue
        if not job:
            time.sleep(POLL_IDLE_SECONDS)
            continue
        process(job)
        time.sleep(POLL_BUSY_SECONDS)


if __name__ == "__main__":
    main()
