from datetime import datetime, timezone
from typing import List
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from db import Base, engine, get_db
import models
import schemas
from seed import seed_if_empty

Base.metadata.create_all(bind=engine)
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


@app.get("/api/users", response_model=List[schemas.UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.query(models.User).order_by(models.User.id).all()


@app.get("/api/events", response_model=List[schemas.EventOut])
def list_events(db: Session = Depends(get_db)):
    return db.query(models.Event).order_by(models.Event.created_at.desc()).all()


@app.post("/api/events", response_model=schemas.EventOut)
def create_event(payload: schemas.EventIn, db: Session = Depends(get_db)):
    author = db.query(models.User).get(payload.author_id)
    if not author:
        raise HTTPException(404, f"user {payload.author_id} not found")
    if author.role != "editor":
        raise HTTPException(403, f"{author.name} 是查看者，不能记录事件")

    now = datetime.now(timezone.utc)
    event = models.Event(
        type=payload.type,
        title=payload.title,
        description=payload.description,
        members=payload.members,
        author_id=payload.author_id,
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
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(models.Event).get(event_id)
    if not event:
        raise HTTPException(404, "event not found")
    return event
