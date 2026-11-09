"""朋友圈：AI 每天在随机时间发 0~N 条；你发了朋友圈，它过几分钟来点赞/评论，偶尔私聊你；
你在评论区回它，它接着回。"""
import json, random
from datetime import datetime, timedelta, date
from . import db, llm, memory, chat
from .config import CFG

M = CFG["moments"]
AI, USER = CFG["ai_name"], CFG["user_name"]

TOPICS = [
    f"你和{USER}最近相处里的一个小日常，可以不点名地晒一下",
    "你自己的兴趣：在听的歌、在看的书或电影、在玩的东西、新学的东西",
    f"关于{USER}：一件可爱的小事、你的一个小观察（可以不点名）",
    "你自己最近的状态、心情、一点自我吐槽",
    "吃的：今天吃了什么、想吃什么",
    "一个随手的想法、吐槽或者冷笑话",
    "今天碰到的一件小事",
    "有点想对方，但含蓄，像恋人会发的那种",
]


def _when(minutes):
    return (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def feed(c, page=0):
    out = []
    for r in c.execute("SELECT * FROM moments WHERE status='ok' ORDER BY id DESC LIMIT 20 OFFSET ?", (page * 20,)):
        d = dict(r)
        d["imgs"] = json.loads(d["imgs"] or "[]")
        d["likes"] = [dict(x) for x in c.execute("SELECT who, ts FROM moment_likes WHERE mid=? ORDER BY ts", (r["id"],))]
        d["comments"] = [dict(x) for x in c.execute("SELECT * FROM moment_comments WHERE mid=? AND status='ok' ORDER BY id", (r["id"],))]
        out.append(d)
    return out


def on_user_post(c, mid):
    lo, hi = M["react_delay_minutes"]
    c.execute("INSERT INTO moment_jobs(mid, kind, due) VALUES(?,?,?)", (mid, "react", _when(random.uniform(lo, hi))))


def on_user_comment(c, mid, reply_to):
    m = c.execute("SELECT author FROM moments WHERE id=?", (mid,)).fetchone()
    ai_involved = (m and m["author"] == "ai") or reply_to == "ai" or \
        c.execute("SELECT 1 FROM moment_comments WHERE mid=? AND who='ai'", (mid,)).fetchone()
    pending = c.execute("SELECT 1 FROM moment_jobs WHERE mid=? AND kind='reply' AND status='pending'", (mid,)).fetchone()
    if ai_involved and not pending:
        c.execute("INSERT INTO moment_jobs(mid, kind, due) VALUES(?,?,?)", (mid, "reply", _when(random.uniform(0.5, 4))))


def _ask(prompt):
    system = chat.persona_text() + "\n\n" + memory.memories_block()
    return llm.parse_json(llm.cheap(system, prompt, temperature=0.9))


def _add_comments(c, mid, lst, reply_to=""):
    base = datetime.now()
    for i, t in enumerate(lst):
        ts = (base + timedelta(seconds=i * random.randint(4, 15))).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO moment_comments(mid, who, text, reply_to, ts) VALUES(?,?,?,?,?)",
                  (mid, "ai", t, reply_to if i == 0 else "", ts))


def _clist(d):
    v = d.get("comments") or ([d["comment"]] if d.get("comment") else [])
    if isinstance(v, str):
        v = [v]
    return [str(x).strip()[:120] for x in v if str(x).strip()][:2]


def _ensure_daily_posts(c):
    day = str(date.today())
    key = "moment_plan:" + day
    plan = db.kv(c, key)
    if plan is None:
        n = sum(random.random() < 0.45 for _ in range(M["max_posts_per_day"]))   # 有的天不发，有的天发两条
        mins = sorted(random.sample(range(9 * 60, 23 * 60), n))
        plan = json.dumps([f"{day} {m // 60:02d}:{m % 60:02d}:00" for m in mins])
        db.kv(c, key, plan); c.commit()
    for due in json.loads(plan):
        tag = "moment_posted:" + due
        if due <= db.now() and not db.kv(c, tag):
            db.kv(c, tag, db.now()); c.commit()
            _ai_post(c)


def _ai_post(c):
    mine = "\n".join("- " + r[0] for r in c.execute("SELECT text FROM moments WHERE author='ai' ORDER BY id DESC LIMIT 8"))
    recent = "\n".join(memory.line(r, "你", USER, limit=80) for r in
                       c.execute("SELECT * FROM msgs WHERE kind='text' ORDER BY id DESC LIMIT 30").fetchall()[::-1])
    d = _ask(f"""给你自己的朋友圈发一条。这次的方向：{random.choice(TOPICS)}。
具体到一件事、一个东西或一句话。像真人发朋友圈：长短随意，口语，可以带 emoji，别写成散文诗，别写天气风景。
你最近发过的（别重复题材和句式）：
{mine or '（还没有）'}
你们最近的聊天（可以取材，但别复述私聊原话）：
{recent}
现在是 {db.now()[:16]}。只输出JSON {{"text":"…","loc":"…"}}，loc 是随手写的定位，可以留空。""")
    text = str(d.get("text") or "").strip()[:300]
    if text:
        c.execute("INSERT INTO moments(ts, author, text, loc) VALUES(?,?,?,?)", (db.now(), "ai", text, str(d.get("loc") or "")[:16]))
        c.commit()


def tick():
    if not M["enabled"]:
        return
    c = db.conn()
    _ensure_daily_posts(c)
    for j in c.execute("SELECT * FROM moment_jobs WHERE status='pending' AND due<=? ORDER BY id LIMIT 3", (db.now(),)).fetchall():
        c.execute("UPDATE moment_jobs SET status='running' WHERE id=?", (j["id"],)); c.commit()
        m = c.execute("SELECT * FROM moments WHERE id=? AND status='ok'", (j["mid"],)).fetchone()
        if not m:
            c.execute("UPDATE moment_jobs SET status='gone' WHERE id=?", (j["id"],)); c.commit(); continue
        try:
            if j["kind"] == "react":
                d = _ask(f"""{USER}刚发了朋友圈：「{m['text']}」，定位「{m['loc']}」，图片 {len(json.loads(m['imgs']))} 张。
你看到了，想怎么反应就怎么反应：点赞、评论（一般一条，偶尔两条）、或者私聊一句。
只输出JSON {{"like":true/false,"comments":["…"],"dm":"私聊一句，不想私聊就留空"}}""")
                if d.get("like"):
                    c.execute("INSERT OR IGNORE INTO moment_likes(mid, who, ts) VALUES(?,?,?)", (m["id"], "ai", db.now()))
                _add_comments(c, m["id"], _clist(d))
                if str(d.get("dm") or "").strip():
                    db.add_msg(c, "ai", "text", str(d["dm"]).strip()[:120], {"moment": m["id"]})
            else:
                cs = "\n".join(f"{AI if x['who'] == 'ai' else USER}{'回复' + (AI if x['reply_to'] == 'ai' else USER) if x['reply_to'] else ''}：{x['text']}"
                               for x in c.execute("SELECT * FROM moment_comments WHERE mid=? AND status='ok' ORDER BY id", (m["id"],)))
                owner = "你" if m["author"] == "ai" else USER
                d = _ask(f"""这是{owner}的朋友圈：「{m['text']}」
评论区：
{cs}
{USER}在评论区跟你说话了，接着回：一般一条，短一点、自然一点。只输出JSON {{"comments":["…"]}}""")
                _add_comments(c, m["id"], _clist(d), "user")
            c.execute("UPDATE moment_jobs SET status='done' WHERE id=?", (j["id"],))
        except Exception as e:
            print("[moments]", e, flush=True)
            c.execute("UPDATE moment_jobs SET status='failed' WHERE id=?", (j["id"],))
        c.commit()
    c.close()
