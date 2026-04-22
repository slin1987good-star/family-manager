"""
Demo seed data. Replace everything below with your own family before running
your instance. `seed_if_empty` only writes when the users table is empty, so
re-deploying with different values will NOT overwrite an existing deployment.

Also change DEFAULT_PINS before deploying — the values below are placeholders
any reader of this file can see.
"""
from db import SessionLocal
from models import User
from auth import hash_pin

# 示例 PIN — 自部署前请改成只有家里人知道的数字
DEFAULT_PINS = {"dad": "1111", "mom": "2222", "daughter": "3333", "son": "4444"}

FAMILY_SEED = [
    {
        "id": "dad", "name": "爸爸", "nickname": "示例爸",
        "emoji": "👨", "cls": "member-dad", "role": "editor",
        "profile": {
            "age": 40, "birthday": "1986-01-01", "zodiac": "♑", "mbti": "ISTJ",
            "occupation": "公司职员",
            "company": "示例公司",
            "workHours": "周一至周五 09:00 – 18:00",
            "commute": "通勤 30 分钟",
            "strengths": ["做事可靠", "有耐心"],
            "improvements": ["示例待改进项 1", "示例待改进项 2"],
            "hobbies": ["散步", "看电影"],
            "favFood": "家常菜",
        },
    },
    {
        "id": "mom", "name": "妈妈", "nickname": "示例妈",
        "emoji": "👩", "cls": "member-mom", "role": "editor",
        "profile": {
            "age": 38, "birthday": "1988-01-01", "zodiac": "♑", "mbti": "ESFJ",
            "occupation": "公司职员",
            "company": "示例公司",
            "workHours": "周一至周五 09:00 – 18:00",
            "commute": "通勤 20 分钟",
            "strengths": ["爱家", "会生活"],
            "improvements": ["示例待改进项 1", "示例待改进项 2"],
            "hobbies": ["烘焙", "瑜伽"],
            "favFood": "水果、甜点",
        },
    },
    {
        "id": "daughter", "name": "女儿", "nickname": "果果",
        "emoji": "👧", "cls": "member-daughter", "role": "viewer",
        "profile": {
            "age": 11, "birthday": "2015-01-01", "zodiac": "♑", "mbti": "INFP",
            "occupation": "小学生",
            "company": "示例小学",
            "workHours": "在校 08:00 – 16:00",
            "commute": "步行",
            "grade": {
                "rank": "示例名次",
                "subjects": [
                    {"k": "语文", "s": 90, "trend": "flat"},
                    {"k": "数学", "s": 90, "trend": "flat"},
                    {"k": "英语", "s": 90, "trend": "flat"},
                ],
            },
            "strengths": ["示例闪光点"],
            "improvements": ["示例成长空间"],
            "hobbies": ["画画", "阅读"],
            "favFood": "水果",
        },
    },
    {
        "id": "son", "name": "儿子", "nickname": "豆豆",
        "emoji": "👦", "cls": "member-son", "role": "viewer",
        "profile": {
            "age": 8, "birthday": "2018-01-01", "zodiac": "♑", "mbti": "ESFP",
            "occupation": "小学生",
            "company": "示例小学",
            "workHours": "在校 08:00 – 15:30",
            "commute": "家长接送",
            "grade": {
                "rank": "示例名次",
                "subjects": [
                    {"k": "语文", "s": 90, "trend": "flat"},
                    {"k": "数学", "s": 90, "trend": "flat"},
                ],
            },
            "strengths": ["示例闪光点"],
            "improvements": ["示例成长空间"],
            "hobbies": ["积木", "户外活动"],
            "favFood": "面食",
        },
    },
]


def seed_if_empty():
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            for u in FAMILY_SEED:
                db.add(User(pin_hash=hash_pin(DEFAULT_PINS[u["id"]]), **u))
            db.commit()
            print(f"Seeded {len(FAMILY_SEED)} family members.")
            return
        # backfill missing pin_hash for existing users (from before pin was added)
        missing = db.query(User).filter(User.pin_hash.is_(None)).all()
        for u in missing:
            if u.id in DEFAULT_PINS:
                u.pin_hash = hash_pin(DEFAULT_PINS[u.id])
        if missing:
            db.commit()
            print(f"Backfilled pin_hash for {len(missing)} users.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_if_empty()
