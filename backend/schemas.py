from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    nickname: Optional[str] = None
    emoji: Optional[str] = None
    cls: Optional[str] = None
    role: str
    profile: Optional[dict] = None


class EventIn(BaseModel):
    type: str
    title: Optional[str] = None  # AI fills this from `description` on analysis
    description: str = ""
    members: List[str] = Field(default_factory=list)
    time_label: Optional[str] = None
    event_date: Optional[str] = None
    mood: Optional[str] = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    type: str
    title: str
    description: str
    members: List[str]
    author_id: Optional[str] = None
    time_label: Optional[str] = None
    event_date: Optional[str] = None
    mood: Optional[str] = None
    ai_summary: Optional[str] = None
    ai_cause: Optional[List[str]] = None
    ai_suggest: Optional[List[str]] = None
    ai_script: Optional[str] = None
    ai_status: str = "pending"
    created_at: datetime
