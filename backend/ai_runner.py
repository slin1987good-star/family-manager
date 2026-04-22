"""
AI runner — calls any Anthropic-compatible `/v1/messages` endpoint
(official api.anthropic.com, tdyun.ai, or any New API gateway) and parses
the strict-JSON reply our prompts request.

Configured entirely via env vars so switching providers is one secret flip:
  LLM_API_KEY    Anthropic-compatible bearer key (required)
  LLM_BASE_URL   default https://api.anthropic.com
  LLM_MODEL      default claude-sonnet-4-6
"""
import asyncio
import json
import os
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy.orm import Session

import ai as ai_mod
import models
from db import SessionLocal

API_KEY = os.environ.get("LLM_API_KEY", "")
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.anthropic.com").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
MAX_OUTPUT_TOKENS = 1024
REQUEST_TIMEOUT = 120
MAX_ATTEMPTS = 3
POLL_INTERVAL_SECONDS = 3


def _extract_json(text: str):
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
            return json.loads(t[start: end + 1])
        raise


async def call_llm(prompt: str) -> dict:
    """Single round-trip to the LLM. Returns parsed JSON dict."""
    if not API_KEY:
        raise RuntimeError("LLM_API_KEY not configured on the server")

    payload = {
        "model": MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": "You are a caring family assistant. Always reply in valid JSON exactly as requested.",
        "messages": [{"role": "user", "content": prompt}],
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        r = await client.post(
            f"{BASE_URL}/v1/messages",
            headers={
                "x-api-key": API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if r.status_code != 200:
            raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:400]}")
        body = r.json()

    parts = [b["text"] for b in body.get("content", []) if b.get("type") == "text"]
    raw = "".join(parts)
    return _extract_json(raw)


def _apply_result(db: Session, job: models.AiJob, result: dict):
    """Write the parsed result back into the right target row based on
    the job type."""
    job.result = result
    job.completed_at = datetime.now(timezone.utc)
    job.status = "done"

    if job.job_type == "event_analysis" and job.event_id:
        ev = db.query(models.Event).get(job.event_id)
        if ev:
            title = (result.get("title") or "").strip()
            if title:
                ev.title = title
            ev.ai_summary = result.get("summary") or None
            ev.ai_cause = result.get("cause") or None
            ev.ai_suggest = result.get("suggest") or None
            ev.ai_script = result.get("script") or None
            ev.ai_status = "done"

    elif job.job_type == "daily_report":
        from datetime import timedelta
        job_created = (job.created_at or datetime.now(timezone.utc))
        if job_created.tzinfo is None:
            job_created = job_created.replace(tzinfo=timezone.utc)
        # Beijing yesterday, relative to when the job was created
        bj_created = job_created.astimezone(timezone(timedelta(hours=8)))
        target_date = (bj_created - timedelta(days=1)).strftime("%Y-%m-%d")

        report_content = {
            "highlight": result.get("highlight", ""),
            "good": result.get("good") or [],
            "watch": result.get("watch") or [],
            "tip": result.get("tip", ""),
        }
        existing = (
            db.query(models.DailyReport).filter_by(report_date=target_date).first()
        )
        if existing:
            existing.content = report_content
            existing.source_job_id = job.id
        else:
            db.add(models.DailyReport(
                report_date=target_date,
                content=report_content,
                source_job_id=job.id,
            ))
        new_ctx = (result.get("context") or "").strip()
        if new_ctx:
            db.add(models.FamilyContext(content=new_ctx, source_job_id=job.id))


BEIJING = timezone(timedelta(hours=8))


def _enqueue_daily_report_if_due():
    """Lazy scheduler — runs on every worker loop iteration. Cheap: 3 indexed
    queries + early return in the common case."""
    now = datetime.now(BEIJING)
    if now.hour < 7 or (now.hour == 7 and now.minute < 30):
        return
    target_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        if db.query(models.DailyReport).filter_by(report_date=target_date).first():
            return
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        recent = (
            db.query(models.AiJob)
            .filter(models.AiJob.job_type == "daily_report")
            .filter(models.AiJob.created_at >= cutoff)
            .first()
        )
        if recent:
            return
        prompt = ai_mod.build_daily_report_prompt(db, target_date)
        db.add(models.AiJob(job_type="daily_report", event_id=None, prompt=prompt))
        db.commit()
    finally:
        db.close()


def _recover_zombies():
    """Any job stuck in 'processing' for > 5 min is a crashed attempt.
    Reset so we retry."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    db = SessionLocal()
    try:
        n = (
            db.query(models.AiJob)
            .filter(models.AiJob.status == "processing")
            .filter(models.AiJob.started_at < cutoff)
            .update({"status": "pending"}, synchronize_session=False)
        )
        if n:
            db.commit()
    finally:
        db.close()


async def _process_one_pending() -> bool:
    """Claim the oldest pending job, call the LLM, apply result, commit.
    Returns True if a job was processed, False if the queue was empty."""
    db = SessionLocal()
    try:
        job = (
            db.query(models.AiJob)
            .filter(models.AiJob.status == "pending")
            .order_by(models.AiJob.created_at.asc())
            .first()
        )
        if not job:
            return False

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        job.attempts = (job.attempts or 0) + 1
        db.commit()
        db.refresh(job)
        jid = job.id
        prompt = job.prompt or ""
    finally:
        db.close()

    # Call outside the DB session so we don't hold a connection for 20s+.
    try:
        result = await call_llm(prompt)
    except Exception as e:
        # Re-open and mark the failure
        db = SessionLocal()
        try:
            job = db.query(models.AiJob).get(jid)
            if not job:
                return True
            if (job.attempts or 0) < MAX_ATTEMPTS:
                job.status = "pending"  # retry next iteration
            else:
                job.status = "failed"
                if job.event_id:
                    ev = db.query(models.Event).get(job.event_id)
                    if ev:
                        ev.ai_status = "failed"
            job.error = str(e)[:2000]
            db.commit()
        finally:
            db.close()
        print(f"[ai-runner] job {jid} failed: {e}", flush=True)
        return True

    # Success path — reopen session and write result
    db = SessionLocal()
    try:
        job = db.query(models.AiJob).get(jid)
        if job:
            _apply_result(db, job, result)
            db.commit()
            print(f"[ai-runner] job {jid} done", flush=True)
    finally:
        db.close()
    return True


async def worker_loop():
    """Runs forever (until the machine is stopped). Drains pending jobs back-
    to-back when there's work, otherwise sleeps. Survives restarts because
    jobs live in Postgres."""
    print(f"[ai-runner] started · model={MODEL} · base={BASE_URL}", flush=True)
    housekeeping_counter = 0
    while True:
        try:
            # Every ~30s (10 idle polls), run cheap housekeeping:
            # - recover jobs stuck in 'processing' from crashed attempts
            # - enqueue today's daily_report if the 07:30 Beijing boundary passed
            if housekeeping_counter % 10 == 0:
                try:
                    _recover_zombies()
                    _enqueue_daily_report_if_due()
                except Exception as e:
                    print(f"[ai-runner] housekeeping: {e}", flush=True)
            housekeeping_counter += 1

            did_work = await _process_one_pending()
            if did_work:
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[ai-runner] loop error: {e}", flush=True)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
