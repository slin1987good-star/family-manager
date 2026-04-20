from datetime import datetime, timezone
from typing import List
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from db import Base, engine, get_db
import models
import schemas
import auth
from seed import seed_if_empty


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
