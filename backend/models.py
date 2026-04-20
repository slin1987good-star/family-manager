from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from db import Base


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    nickname = Column(String)
    emoji = Column(String)
    cls = Column(String)
    role = Column(String, nullable=False)
    profile = Column(JSON, default=dict)
    pin_hash = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, default="")
    members = Column(JSON, default=list)
    author_id = Column(String, ForeignKey("users.id"))
    time_label = Column(String)
    event_date = Column(String)
    mood = Column(String)
    ai_summary = Column(Text)
    ai_cause = Column(JSON)
    ai_suggest = Column(JSON)
    ai_script = Column(Text)
    ai_status = Column(String, default="pending")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    author = relationship("User")


class AiJob(Base):
    __tablename__ = "ai_jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String, nullable=False, index=True)
    status = Column(String, default="pending", index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True)
    prompt = Column(Text)
    result = Column(JSON)
    error = Column(Text)
    attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime)
    completed_at = Column(DateTime)


class FamilyContext(Base):
    __tablename__ = "family_context"
    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(Text, nullable=False)
    source_job_id = Column(Integer, ForeignKey("ai_jobs.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
