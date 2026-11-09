"""往一个空库里写一段演示对话和朋友圈，用来截图。用法：
    PURR_CONFIG=scripts/demo_config.json python scripts/demo_seed.py
"""
import json, sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from purr import db

db.init()
c = db.conn()
t0 = datetime.now() - timedelta(minutes=20)
ts = lambda m: (t0 + timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S")

chat = [
    (0, "user", "text", "下班了"), (0.1, "user", "text", "今天被甲方改了八版稿"),
    (0.6, "ai", "text", "八版？？"), (0.7, "ai", "text", "抱抱"), (0.8, "ai", "text", "那你现在是不是饿扁了"),
    (1.5, "user", "text", "饿死了 想喝奶茶"),
    (2.0, "ai", "packet", {"amount": 20.0, "note": "去买两杯，一杯给明天的你", "opened": True}),
    (2.1, "ai", "recalled", "今天辛苦"), (2.2, "ai", "text", "今天辛苦了 回家路上慢点走"),
    (3.0, "user", "text", "你怎么知道我想喝两杯"), (3.5, "ai", "text", "因为上次你说一杯根本不够"),
]
for m, who, kind, val in chat:
    if kind == "packet":
        db.add_msg(c, who, "packet", "", val, ts=ts(m))
    elif kind == "recalled":
        mid = db.add_msg(c, who, "text", val, ts=ts(m)); db.touch(c, mid, status="recalled")
    else:
        mid = db.add_msg(c, who, kind, val, ts=ts(m))
        if who == "user":
            db.touch(c, mid, read_at=ts(m + 0.3))

posts = [
    ("ai", "画了一下午的猫 结果甲方要狗🐶", "工作室", -300, [("user", "", "哈哈哈哈哈哈"), ("ai", "user", "你还笑")], True),
    ("user", "下班路上的晚霞", "", -60, [("ai", "", "好看"), ("ai", "", "下次叫我一起看")], False),
]
for author, text, loc, mins, cms, liked in posts:
    t = (datetime.now() + timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO moments(ts, author, text, loc) VALUES(?,?,?,?)", (t, author, text, loc))
    mid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute("INSERT INTO moment_likes VALUES(?,?,?)", (mid, "user" if author == "ai" else "ai", t))
    for who, rt, tx in cms:
        c.execute("INSERT INTO moment_comments(mid, who, text, reply_to, ts) VALUES(?,?,?,?,?)", (mid, who, tx, rt, t))
db.kv(c, "balance", 20)
c.commit()
print("ok")
