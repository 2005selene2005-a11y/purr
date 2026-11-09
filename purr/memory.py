"""三层记忆：
1. 最近 N 条消息原样进上下文；
2. 更早但在 digest_hours 小时内的消息，每条压成「HH:MM 谁：大意」一行；
3. 便签：每聊满 notes.every 条你的消息，让模型写一张便签（事实、约定、已问过的、待跟进），最近几张进系统提示；
另外 AI 可以在回复末尾写 [REMEMBER|内容] 存长期记忆。
"""
import json, threading
from datetime import datetime, timedelta
from . import db, llm
from .config import CFG

USER_KINDS = ("text", "image", "sticker")
_note_lock = threading.Lock()


def line(r, ai, user, limit=None):
    meta = json.loads(r["meta"] or "{}")
    k = r["kind"]
    t = r["text"] or ""
    if k == "image":
        t = "[图片]" + (" " + meta["caption"] if meta.get("caption") else "")
    elif k == "sticker":
        t = f"[表情：{r['text']}]"
    elif k == "packet":
        t = f"[红包 ¥{meta.get('amount')}：{meta.get('note', '')}]" + ("（已领）" if meta.get("opened") else "")
    if r["status"] == "recalled":
        t = "[撤回了一条消息]"
    t = t.strip()
    if limit:
        t = t[:limit]
    who = ai if r["sender"] == "ai" else user
    return f"[{r['ts'][5:16]}] {who}: {t}" if t else ""


def remember(text):
    c = db.conn()
    c.execute("INSERT INTO memories(ts, text) VALUES(?, ?)", (db.now(), text[:200]))
    c.commit(); c.close()


def memories_block(n=40):
    c = db.conn()
    rows = c.execute("SELECT ts, text FROM memories ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    c.close()
    if not rows:
        return ""
    return "你记得的事：\n" + "\n".join(f"- ({r['ts'][:10]}) {r['text']}" for r in reversed(rows))


def notes_block():
    if not CFG["notes"]["enabled"]:
        return ""
    c = db.conn()
    rows = c.execute("SELECT summary FROM notes ORDER BY id DESC LIMIT ?", (CFG["notes"]["show"],)).fetchall()
    c.close()
    if not rows:
        return ""
    return "你之前给最近的聊天写的便签（从旧到新）：\n\n" + "\n\n---\n\n".join(r[0] for r in reversed(rows))


def digest(c, before_id):
    since = (datetime.now() - timedelta(hours=CFG["realism"]["digest_hours"])).strftime("%Y-%m-%d %H:%M:%S")
    rows = c.execute("SELECT * FROM msgs WHERE id<? AND ts>=? ORDER BY id", (before_id, since)).fetchall()
    out, total = [], 0
    for r in reversed(rows):
        ln = line(r, "你", CFG["user_name"], limit=60)
        if not ln:
            continue
        if total + len(ln) > 1500:
            out.append("…"); break
        out.append(ln); total += len(ln)
    if not out:
        return ""
    return "今天早些时候聊过（摘要）：\n" + "\n".join(reversed(out))


def write_notes_async():
    if CFG["notes"]["enabled"]:
        threading.Thread(target=_write_notes, daemon=True).start()


def _write_notes():
    if not _note_lock.acquire(blocking=False):
        return
    try:
        c = db.conn()
        every = CFG["notes"]["every"]
        last = c.execute("SELECT max(up_to_id) FROM notes").fetchone()[0] or 0
        rows = c.execute("SELECT * FROM msgs WHERE id>? ORDER BY id", (last,)).fetchall()
        n, cut = 0, None
        for i, r in enumerate(rows):
            if r["sender"] == "user" and r["kind"] in USER_KINDS:
                n += 1
                if n == every:
                    cut = i; break
        if cut is None:
            c.close(); return
        j = cut + 1
        while j < len(rows) and rows[j]["sender"] == "ai":  # 把 AI 紧接着的回复也带上
            j += 1
        batch = rows[:j]
        src = "\n".join(x for x in (line(r, CFG["ai_name"], CFG["user_name"]) for r in batch) if x)[-30000:]
        prompt = (f"把下面这段{CFG['user_name']}和{CFG['ai_name']}的聊天做成一张独立便签，600 字以内。"
                  "写清楚：对方说过的事实（吃了什么、身体/情绪、在哪、在干嘛、接下来要干嘛）、问过什么和怎么答的、"
                  "两人约定和答应的事、对方的偏好和讨厌的东西；关键原话用引号照抄。开头写日期和大致时段。"
                  "结尾单列「已经问过，不用再问」和「待跟进」。不添加对话里没有的事实。只输出正文。\n\n" + src)
        summary = llm.cheap("你是忠实的对话便签整理器。", prompt, temperature=0.3)
        if summary:
            c.execute("INSERT OR IGNORE INTO notes(from_id, up_to_id, summary, ts) VALUES(?,?,?,?)",
                      (batch[0]["id"], batch[-1]["id"], summary[:1500], db.now()))
            c.commit()
        c.close()
    except Exception as e:
        print("[notes]", e, flush=True)
    finally:
        _note_lock.release()
