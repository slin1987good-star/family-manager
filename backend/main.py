import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from db import Base, engine, get_db
import models
import schemas
import auth
import ai as ai_mod
import ai_runner
from seed import seed_if_empty

# Family lives in Beijing (UTC+8). The daily report fires when local time
# crosses 07:30, reports on the previous calendar day.
BEIJING = timezone(timedelta(hours=8))


def _beijing_now():
    return datetime.now(BEIJING)


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


def recover_stale_jobs():
    """On startup, any job still in 'processing' is a zombie from a crashed
    worker run — reset it so the current worker can retry."""
    from db import SessionLocal as _S
    db = _S()
    try:
        n = (
            db.query(models.AiJob)
            .filter(models.AiJob.status == "processing")
            .update({"status": "pending"}, synchronize_session=False)
        )
        if n:
            db.commit()
            print(f"[startup] recovered {n} stale processing job(s)")
    finally:
        db.close()


recover_stale_jobs()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(ai_runner.worker_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Family Manager API", lifespan=_lifespan)

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


# Serve the SPA frontend at / so the app runs same-origin on fly.dev.
_STATIC_INDEX = Path(__file__).parent / "static" / "index.html"


@app.get("/")
def index():
    if _STATIC_INDEX.exists():
        return FileResponse(_STATIC_INDEX, media_type="text/html")
    return Response(status_code=404, content="frontend not bundled")


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


class EventUpdate(BaseModel):
    type: Optional[str] = None
    description: Optional[str] = None
    members: Optional[List[str]] = None
    mood: Optional[str] = None


@app.patch("/api/events/{event_id}", response_model=schemas.EventOut)
def update_event(
    event_id: int,
    payload: EventUpdate,
    db: Session = Depends(get_db),
    me: models.User = Depends(auth.current_user),
):
    """Editors only. Title isn't editable (AI maintains it); click 🔄 after
    saving if you want the AI analysis refreshed for the new content."""
    if me.role != "editor":
        raise HTTPException(403, "只有爸爸/妈妈可以修改事件")
    event = db.query(models.Event).get(event_id)
    if not event:
        raise HTTPException(404, "event not found")
    if payload.type is not None:
        event.type = payload.type
    if payload.description is not None:
        event.description = payload.description
    if payload.members is not None:
        event.members = payload.members
    if payload.mood is not None:
        event.mood = payload.mood or None
    db.commit()
    db.refresh(event)
    return event


@app.delete("/api/events/{event_id}")
def delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    me: models.User = Depends(auth.current_user),
):
    if me.role != "editor":
        raise HTTPException(403, "只有爸爸/妈妈可以删除事件")
    event = db.query(models.Event).get(event_id)
    if not event:
        raise HTTPException(404, "event not found")
    # purge related ai_jobs first to avoid FK violation
    db.query(models.AiJob).filter(models.AiJob.event_id == event_id).delete(synchronize_session=False)
    db.delete(event)
    db.commit()
    return {"ok": True}


@app.post("/api/events/{event_id}/reanalyze", response_model=schemas.EventOut)
def reanalyze_event(
    event_id: int,
    db: Session = Depends(get_db),
    me: models.User = Depends(auth.current_user),
):
    """Re-run AI analysis for an event. Old summary/cause/suggest/script stay
    visible while the new job is running so the user keeps context."""
    if me.role != "editor":
        raise HTTPException(403, "只有爸爸/妈妈可以重新生成 AI 分析")
    event = db.query(models.Event).get(event_id)
    if not event:
        raise HTTPException(404, "event not found")

    # Supersede any in-flight analysis for this event so the worker doesn't
    # race us.
    (
        db.query(models.AiJob)
        .filter(models.AiJob.event_id == event_id)
        .filter(models.AiJob.job_type == "event_analysis")
        .filter(models.AiJob.status.in_(["pending", "processing"]))
        .update({"status": "failed", "error": "superseded by reanalyze"},
                synchronize_session=False)
    )

    event.ai_status = "pending"
    prompt = ai_mod.build_event_analysis_prompt(db, event)
    db.add(models.AiJob(job_type="event_analysis", event_id=event.id, prompt=prompt))
    db.commit()
    db.refresh(event)
    return event


# The previous /api/worker/* endpoints (used by the external Mac worker)
# were removed once the backend started running `ai_runner.worker_loop()`
# internally — all AI dispatch now happens in-process on Fly.
