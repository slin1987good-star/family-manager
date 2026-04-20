import os
from datetime import datetime, timezone
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

    now = datetime.now(timezone.utc)
    event = models.Event(
        type=payload.type,
        title=payload.title,
        description=payload.description,
        members=payload.members,
        author_id=user.id,
        time_label=payload.time_label or now.strftime("今天 %H:%M"),
        event_date=payload.event_date or now.strftime("%Y-%m-%d"),
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
            ev.ai_summary = r.get("summary") or None
            ev.ai_cause = r.get("cause") or None
            ev.ai_suggest = r.get("suggest") or None
            ev.ai_script = r.get("script") or None
            ev.ai_status = "done"

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
