#!/usr/bin/env python3
"""
Family Manager AI Worker

Runs on your Mac. Polls the Fly.io backend for pending AI analysis jobs,
dispatches each prompt to the local `claude` CLI (your Claude Code subscription),
parses the JSON reply, and posts the result back.

Env vars:
  FAMILY_API          default https://gw-family-manager.fly.dev
  FAMILY_WORKER_TOKEN required; same value as the WORKER_TOKEN secret on Fly
  CLAUDE_BIN          default "claude"
"""
import json
import os
import subprocess
import sys
import time
import traceback
from urllib import request as urlrequest
from urllib.error import HTTPError

BASE = os.environ.get("FAMILY_API", "https://gw-family-manager.fly.dev")
TOKEN = os.environ.get("FAMILY_WORKER_TOKEN", "")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
POLL_IDLE_SECONDS = 20
POLL_BUSY_SECONDS = 2  # when a job was just processed, check again soon
CLAUDE_TIMEOUT = 180

if not TOKEN:
    print("FAMILY_WORKER_TOKEN is required", file=sys.stderr)
    sys.exit(1)


def _req(method: str, path: str, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urlrequest.Request(
        f"{BASE}{path}", method=method, data=data,
        headers={
            "Content-Type": "application/json",
            "X-Worker-Token": TOKEN,
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
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}") from e


def next_job():
    return _req("GET", "/api/worker/jobs/next")


def complete(job_id, result):
    return _req("POST", f"/api/worker/jobs/{job_id}/complete", {"result": result})


def fail(job_id, error):
    return _req("POST", f"/api/worker/jobs/{job_id}/fail", {"error": error})


def run_claude(prompt: str) -> str:
    """Call `claude -p` non-interactively and return stdout."""
    r = subprocess.run(
        [CLAUDE_BIN, "-p", prompt],
        capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude exit {r.returncode}: {r.stderr.strip()[:500]}")
    return r.stdout


def extract_json(text: str):
    t = text.strip()
    # Strip markdown fences if claude wrapped the JSON in ```json ... ```
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
    print(f"[worker] job {job['id']} · {job['job_type']} · attempt {job['attempts']}")
    try:
        raw = run_claude(job["prompt"])
        parsed = extract_json(raw)
        complete(job["id"], parsed)
        print(f"[worker] job {job['id']} done: {parsed.get('summary', '')[:60]}")
    except subprocess.TimeoutExpired:
        fail(job["id"], "claude timed out after 180s")
    except Exception as e:
        tb = traceback.format_exc()[-800:]
        fail(job["id"], f"{type(e).__name__}: {e}\n---\n{tb}")
        print(f"[worker] job {job['id']} failed: {e}", file=sys.stderr)


def main():
    print(f"[worker] started, API={BASE}, poll={POLL_IDLE_SECONDS}s", flush=True)
    while True:
        try:
            job = next_job()
        except Exception as e:
            print(f"[worker] fetch error: {e}", file=sys.stderr)
            time.sleep(POLL_IDLE_SECONDS)
            continue
        if not job:
            time.sleep(POLL_IDLE_SECONDS)
            continue
        process(job)
        time.sleep(POLL_BUSY_SECONDS)


if __name__ == "__main__":
    main()
