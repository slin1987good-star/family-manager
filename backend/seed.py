from db import SessionLocal
from models import User

FAMILY_SEED = [
    {
        "id": "dad", "name": "爸爸", "nickname": "老陈", "emoji": "👨",
        "cls": "member-dad", "role": "editor",
        "profile": {
            "age": 42, "birthday": "1984-03-12", "zodiac": "♓", "mbti": "INTJ",
            "occupation": "软件架构师",
            "company": "一家互联网公司 · 技术团队",
            "workHours": "周一至周五 09:30 – 19:30（常加班到 21:00）",
            "commute": "通勤 45 分钟 · 地铁",
            "strengths": ["逻辑清晰", "动手能力强", "耐心"],
            "improvements": ["容易忽略情绪表达", "周末容易看手机过久"],
            "hobbies": ["摄影", "骑行", "科幻小说", "做木工"],
            "favFood": "手冲咖啡 · 家里妈妈做的红烧肉",
        },
    },
    {
        "id": "mom", "name": "妈妈", "nickname": "小林", "emoji": "👩",
        "cls": "member-mom", "role": "editor",
        "profile": {
            "age": 39, "birthday": "1987-06-20", "zodiac": "♊", "mbti": "ENFJ",
            "occupation": "中学语文老师",
            "company": "市第三中学 · 初二年级",
            "workHours": "周一至周五 07:40 – 17:30（周三有晚自习到 20:30）",
            "commute": "通勤 15 分钟 · 电瓶车",
            "strengths": ["共情力强", "会做饭", "时间规划好"],
            "improvements": ["对孩子成绩偶尔过于焦虑", "不太会拒绝同事"],
            "hobbies": ["插花", "瑜伽", "读散文", "看《向往的生活》"],
            "favFood": "抹茶蛋糕 · 清蒸鲈鱼",
        },
    },
    {
        "id": "daughter", "name": "女儿", "nickname": "小米", "emoji": "👧",
        "cls": "member-daughter", "role": "viewer",
        "profile": {
            "age": 12, "birthday": "2013-09-08", "zodiac": "♍", "mbti": "INFP",
            "occupation": "初一学生",
            "company": "市实验中学 · 初一 (3) 班",
            "workHours": "在校 07:50 – 16:40 · 晚自习 自愿到 19:00",
            "commute": "步行 10 分钟",
            "grade": {
                "rank": "年级前 30",
                "subjects": [
                    {"k": "语文", "s": 92, "trend": "up"},
                    {"k": "数学", "s": 85, "trend": "flat"},
                    {"k": "英语", "s": 95, "trend": "up"},
                    {"k": "物理", "s": 78, "trend": "down"},
                ],
            },
            "strengths": ["文笔好", "观察力细腻", "有责任感"],
            "improvements": ["物理有点吃力", "偶尔熬夜看书", "情绪比较敏感"],
            "hobbies": ["画画", "看小说（最近在读《夏洛的网》）", "养多肉", "追动画"],
            "favFood": "芒果布丁 · 番茄鸡蛋面",
        },
    },
    {
        "id": "son", "name": "儿子", "nickname": "小宝", "emoji": "👦",
        "cls": "member-son", "role": "viewer",
        "profile": {
            "age": 8, "birthday": "2017-11-25", "zodiac": "♐", "mbti": "ESFP",
            "occupation": "二年级学生",
            "company": "市实验小学 · 二年级 (1) 班",
            "workHours": "在校 08:10 – 15:30 · 托管到 17:00",
            "commute": "妈妈接送 · 车程 10 分钟",
            "grade": {
                "rank": "班级前 10",
                "subjects": [
                    {"k": "语文", "s": 95, "trend": "flat"},
                    {"k": "数学", "s": 98, "trend": "up"},
                    {"k": "英语", "s": 88, "trend": "up"},
                ],
            },
            "strengths": ["活泼", "数感强", "乐于分享"],
            "improvements": ["坐不住", "写字有点潦草", "挑食"],
            "hobbies": ["乐高（最爱城堡系列）", "恐龙百科", "足球", "拼图"],
            "favFood": "炸鸡块 · 草莓 · 巧克力牛奶",
        },
    },
]


def seed_if_empty():
    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            return
        for u in FAMILY_SEED:
            db.add(User(**u))
        db.commit()
        print(f"Seeded {len(FAMILY_SEED)} family members.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_if_empty()
