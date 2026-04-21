import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from db import Base, engine, get_db
import models
import schemas
import auth
import ai as ai_mod
from seed import seed_if_empty

WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")

# Family lives in Beijing (UTC+8). The daily report fires when local time
# crosses 07:30, reports on the previous calendar day.
BEIJING = timezone(timedelta(hours=8))


def _beijing_now():
    return datetime.now(BEIJING)


def enqueue_daily_report_if_due(db: Session):
    """Called on every worker poll. Cheap: constant-time checks + an index
    hit on daily_reports.report_date."""
    now = _beijing_now()
    # Before 07:30, don't queue anything.
    if now.hour < 7 or (now.hour == 7 and now.minute < 30):
        return
    target_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    # Already have a report for yesterday?
    existing = db.query(models.DailyReport).filter_by(report_date=target_date).first()
    if existing:
        return
    # Any daily_report job (pending / processing / failed) created in the last
    # 2 hours? Skip. Prevents infinite re-enqueuing during rate-limit storms.
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
    db.add(models.AiJob(
        job_type="daily_report",
        event_id=None,
        prompt=prompt,
    ))
    # Store target_date on the job via a simple prefix marker we can read on
    # complete. Using a dedicated column would be cleaner; marker keeps the
    # migration surface minimal.
    db.flush()
    db.commit()


def ensure_columns():
    inspector = inspect(engine)
    if "users" in inspector.get_table_names():
        cols = [c["name"] for c in inspector.get_columns("users")]
        if "pin_hash" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN pin_hash VARCHAR"))


Base.metadata.create_all(bind=engine)
ensure_columns()
seed_if_empty()

app = FastAPI(title="Family Manager API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
@app.head("/health")
def health():
    return {"status": "ok"}


class LoginIn(BaseModel):
    user_id: str
    pin: str = Field(min_length=4, max_length=8)


class LoginOut(BaseModel):
    token: str
    user: schemas.UserOut


@app.post("/api/login", response_model=LoginOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.query(models.User).get(payload.user_id)
    if not user or not auth.verify_pin(payload.pin, user.pin_hash):
        raise HTTPException(401, "账号或 PIN 不正确")
    return {"token": auth.make_token(user.id), "user": user}


@app.get("/api/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(auth.current_user)):
    return user


class RosterItem(BaseModel):
    id: str
    name: str
    emoji: str | None = None
    cls: str | None = None
    role: str


@app.get("/api/roster", response_model=List[RosterItem])
def roster(db: Session = Depends(get_db)):
    """Public endpoint — just enough to render the login picker."""
    return db.query(models.User).order_by(models.User.id).all()


@app.get("/api/users", response_model=List[schemas.UserOut])
def list_users(db: Session = Depends(get_db), _: models.User = Depends(auth.current_user)):
    return db.query(models.User).order_by(models.User.id).all()


class UserUpdate(BaseModel):
    """Patch-style update. name/role/id/emoji stay immutable — they anchor
    the perspective rewriting logic and avatar styling."""
    nickname: Optional[str] = None
    profile: Optional[dict] = None  # merged (shallow) into existing profile


@app.patch("/api/users/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: str,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    me: models.User = Depends(auth.current_user),
):
    # Permission: editors (dad/mom) can edit anyone; viewers only themselves.
    if me.id != user_id and me.role != "editor":
        raise HTTPException(403, "只能编辑自己的档案；编辑者（爸/妈）可以编辑所有人")

    target = db.query(models.User).get(user_id)
    if not target:
        raise HTTPException(404, "user not found")

    if payload.nickname is not None:
        target.nickname = payload.nickname.strip() or target.nickname

    if payload.profile is not None:
        current = dict(target.profile or {})
        for k, v in payload.profile.items():
            # allow null/empty arrays to clear a field
            if v is None:
                current.pop(k, None)
            else:
                current[k] = v
        target.profile = current

    db.commit()
    db.refresh(target)
    return target


@app.get("/api/events", response_model=List[schemas.EventOut])
def list_events(db: Session = Depends(get_db), _: models.User = Depends(auth.current_user)):
    return db.query(models.Event).order_by(models.Event.created_at.desc()).all()


@app.post("/api/events", response_model=schemas.EventOut)
def create_event(
    payload: schemas.EventIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.current_user),
):
    if user.role != "editor":
        raise HTTPException(403, f"{user.name} 是查看者，不能记录事件")

    now_bj = _beijing_now()
    event = models.Event(
        type=payload.type,
        title=payload.title or "AI 正在生成标题…",
        description=payload.description,
        members=payload.members,
        author_id=user.id,
        time_label=payload.time_label or now_bj.strftime("今天 %H:%M"),
        event_date=payload.event_date or now_bj.strftime("%Y-%m-%d"),
        mood=payload.mood,
        ai_status="pending",
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # Enqueue AI analysis — the Mac worker picks this up within seconds.
    prompt = ai_mod.build_event_analysis_prompt(db, event)
    db.add(models.AiJob(job_type="event_analysis", event_id=event.id, prompt=prompt))
    db.commit()

    return event


class DailyReportOut(BaseModel):
    report_date: str
    highlight: str = ""
    good: List[str] = []
    watch: List[str] = []
    tip: str = ""


@app.get("/api/daily-report")
def get_daily_report(
    db: Session = Depends(get_db),
    _: models.User = Depends(auth.current_user),
):
    """Returns the most recent completed daily report. Frontend shows this
    on the home screen. Returns 204 if none generated yet."""
    row = (
        db.query(models.DailyReport)
        .order_by(models.DailyReport.report_date.desc())
        .first()
    )
    if not row:
        return Response(status_code=204)
    c = row.content or {}
    return {
        "report_date": row.report_date,
        "highlight": c.get("highlight", ""),
        "good": c.get("good", []),
        "watch": c.get("watch", []),
        "tip": c.get("tip", ""),
    }


@app.get("/api/events/{event_id}", response_model=schemas.EventOut)
def get_event(
    event_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(auth.current_user),
):
    event = db.query(models.Event).get(event_id)
    if not event:
        raise HTTPException(404, "event not found")
    return event


# ---- Worker endpoints --------------------------------------------------
# Polled by the Mac worker; authenticated with a shared token (not user PIN).

def require_worker(x_worker_token: str = Header(default="")):
    if not WORKER_TOKEN or x_worker_token != WORKER_TOKEN:
        raise HTTPException(401, "worker token invalid")


class JobOut(BaseModel):
    id: int
    job_type: str
    event_id: Optional[int] = None
    prompt: str
    attempts: int


@app.get("/api/worker/jobs/next")
def worker_next_job(
    response: Response,
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
):
    # Lazy scheduling: every poll, check if daily report is due. Cheap.
    try:
        enqueue_daily_report_if_due(db)
    except Exception as e:
        print(f"daily enqueue failed: {e}")
    job = (
        db.query(models.AiJob)
        .filter(models.AiJob.status == "pending")
        .order_by(models.AiJob.created_at.asc())
        .first()
    )
    if not job:
        response.status_code = 204
        return None
    job.status = "processing"
    job.started_at = datetime.now(timezone.utc)
    job.attempts = (job.attempts or 0) + 1
    db.commit()
    db.refresh(job)
    return JobOut(
        id=job.id, job_type=job.job_type, event_id=job.event_id,
        prompt=job.prompt or "", attempts=job.attempts,
    )


class JobComplete(BaseModel):
    result: dict


@app.post("/api/worker/jobs/{job_id}/complete")
def worker_complete(
    job_id: int,
    body: JobComplete,
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
):
    job = db.query(models.AiJob).get(job_id)
    if not job:
        raise HTTPException(404, "job not found")

    job.status = "done"
    job.result = body.result
    job.completed_at = datetime.now(timezone.utc)

    if job.job_type == "event_analysis" and job.event_id:
        ev = db.query(models.Event).get(job.event_id)
        if ev:
            r = body.result or {}
            ai_title = (r.get("title") or "").strip()
            if ai_title:
                ev.title = ai_title
            ev.ai_summary = r.get("summary") or None
            ev.ai_cause = r.get("cause") or None
            ev.ai_suggest = r.get("suggest") or None
            ev.ai_script = r.get("script") or None
            ev.ai_status = "done"

    elif job.job_type == "daily_report":
        r = body.result or {}
        # Yesterday, Beijing time, computed from job creation time
        job_created = (job.created_at or datetime.now(timezone.utc)).astimezone(BEIJING)
        target_date = (job_created - timedelta(days=1)).strftime("%Y-%m-%d")
        report_content = {
            "highlight": r.get("highlight", ""),
            "good": r.get("good", []) or [],
            "watch": r.get("watch", []) or [],
            "tip": r.get("tip", ""),
        }
        # Upsert by report_date
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
        # And update the rolling family_context L3 card
        new_ctx = (r.get("context") or "").strip()
        if new_ctx:
            db.add(models.FamilyContext(content=new_ctx, source_job_id=job.id))

    db.commit()
    return {"ok": True}


class JobFail(BaseModel):
    error: str


@app.post("/api/worker/jobs/{job_id}/fail")
def worker_fail(
    job_id: int,
    body: JobFail,
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
):
    job = db.query(models.AiJob).get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    # retry up to 3 attempts; otherwise mark failed
    if (job.attempts or 0) < 3:
        job.status = "pending"
    else:
        job.status = "failed"
        if job.event_id:
            ev = db.query(models.Event).get(job.event_id)
            if ev:
                ev.ai_status = "failed"
    job.error = body.error[:2000]
    db.commit()
    return {"ok": True, "retry": job.status == "pending"}
