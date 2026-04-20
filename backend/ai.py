"""
AI context builder.

Keeps prompts tight by feeding Claude a 4-layer pyramid rather than raw event
history:
  L0 raw event — the one being analyzed
  L1 one-line summaries of the few most relevant past events
  L2 daily report (not used in per-event analysis)
  L3 rolling family-state card, AI-maintained

The goal is ≤ ~1k Chinese characters of input per event analysis.
"""
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy.orm import Session
import models

# Keywords that signal an emotionally loaded event. Two such events get
# boosted relevance to each other regardless of type.
EMOTIONAL_KEYWORDS = [
    "发脾气", "吵架", "打架", "冲突", "生气", "哭", "闹", "争执",
    "吼", "骂", "摔", "气", "烦", "哭闹", "矛盾", "不高兴",
    "崩溃", "抓狂", "委屈", "闹情绪", "赌气", "哭泣",
]

MAX_RELATED = 5
MAX_DESC_CHARS = 150


def is_emotional(event_like) -> bool:
    text = (getattr(event_like, "title", "") or "") + (getattr(event_like, "description", "") or "")
    return any(k in text for k in EMOTIONAL_KEYWORDS)


def _score(candidate: models.Event, target: models.Event) -> int:
    if candidate.id == target.id:
        return -1
    s = 0
    overlap = len(set(candidate.members or []) & set(target.members or []))
    s += overlap * 3
    if candidate.type == target.type:
        s += 2
    if is_emotional(target) and is_emotional(candidate):
        s += 4
    # recency boost
    created = candidate.created_at or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - created).days
    if age_days < 7:
        s += 2
    elif age_days < 30:
        s += 1
    return s


def find_related(db: Session, target: models.Event, limit: int = MAX_RELATED) -> List[models.Event]:
    all_others = (
        db.query(models.Event)
        .filter(models.Event.id != target.id)
        .order_by(models.Event.created_at.desc())
        .limit(200)
        .all()
    )
    scored = [(_score(e, target), e) for e in all_others]
    scored = [(s, e) for (s, e) in scored if s > 0]
    scored.sort(key=lambda x: (-x[0], -x[1].id))
    return [e for _, e in scored[:limit]]


# ---- Member card --------------------------------------------------------

def member_card(db: Session) -> str:
    users = db.query(models.User).order_by(models.User.id).all()
    lines = []
    for u in users:
        p = u.profile or {}
        bits = [
            f"{u.emoji} {u.name}（{u.nickname}）",
            f"{p.get('age', '?')} 岁",
            u.role == "editor" and "编辑者" or "查看者",
        ]
        if p.get("mbti"):
            bits.append(p["mbti"])
        if p.get("occupation"):
            bits.append(p["occupation"])
        lines.append(" · ".join(bits))
    return "\n".join(f"- {l}" for l in lines)


# ---- Family state card (L3) ---------------------------------------------

def latest_context(db: Session) -> Optional[str]:
    row = (
        db.query(models.FamilyContext)
        .order_by(models.FamilyContext.created_at.desc())
        .first()
    )
    return row.content if row else None


# ---- Prompt builder -----------------------------------------------------

def _truncate(s: Optional[str], n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def build_event_analysis_prompt(db: Session, event: models.Event) -> str:
    related = find_related(db, event)
    ctx = latest_context(db)

    parts = [
        "你是一位温柔、有洞察力的家庭顾问。以下是一个家庭最近发生的一件事，请你用简短有力的方式帮这家人分析它。",
        "",
        "## 家庭成员",
        member_card(db),
        "",
    ]

    if ctx:
        parts += ["## 近期家庭状态", ctx.strip(), ""]

    if related:
        parts += ["## 相关历史事件（按相关性排序）"]
        for e in related:
            summary = e.ai_summary or _truncate(e.description, 60) or e.title
            parts.append(
                f"- [{e.event_date or '?'}] {e.type}｜{e.title} — {_truncate(summary, 80)}"
            )
        parts.append("")

    members_label = "、".join(event.members or []) or "（未指定）"
    parts += [
        "## 本次事件",
        f"- 类型：{event.type}",
        f"- 标题：{event.title}",
        f"- 描述：{_truncate(event.description, 400) or '（无描述）'}",
        f"- 涉及成员：{members_label}",
        f"- 记录者：{event.author_id or '?'}",
    ]
    if event.mood:
        parts.append(f"- 记录者当时心情：{event.mood}")
    parts.append("")

    parts += [
        "## 你要返回的 JSON",
        "只返回 JSON，不要任何解释文字，不要 markdown 代码块，键名完全用英文：",
        "{",
        '  "summary": "一句话提炼这件事，20-35 字，中文",',
        '  "cause":  ["原因 1", "原因 2", "原因 3"],  // 2-3 条；非冲突类可为空数组',
        '  "suggest":["建议 1", "建议 2", "建议 3"],  // 2-3 条具体可执行',
        '  "script": "家长可以直接开口说的一句话；不适用时返回空串"',
        "}",
        "",
        "注意：",
        "- 所有文本保留家庭内部称呼（爸爸/妈妈/女儿/儿子），不要替换成昵称。",
        "- 不要输出 cause/suggest 之外的任何字段。",
        "- script 是一段自然的口语，不是标题。",
    ]
    return "\n".join(parts)
