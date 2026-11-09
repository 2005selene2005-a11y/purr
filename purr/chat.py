"""回复引擎：让 AI 像真人一样发微信。

- 等你说完：你连发几条，最后一条落地后安静几秒才一起读（最多等 max_wait_seconds）
- 已读 → 正在输入… → 拆成好几条短消息，条与条之间随机停顿
- 生成途中你又发了新消息：这一版作废，带上新消息重写（不抢话、不答非所问）
- 偶尔「手滑」：先发半句，撤回，再发完整的
- 你在它「已读」前撤回 → 它当没看见；已读后撤回 → 它看到「撤回了一条消息」
- 防串台：模型如果开始替你说话（「你的名字：……」），从那里截断
- 标签：[hb:金额:留言] 红包、[sticker:名字] 表情包、[REMEMBER|内容] 长期记忆
"""
import base64, json, random, re, threading, time
from datetime import datetime
from pathlib import Path
from . import db, llm, memory
from .config import CFG, ROOT, persona_text

R = CFG["realism"]
AI, USER = CFG["ai_name"], CFG["user_name"]
USER_KINDS = ("text", "image", "sticker")
STICKER_DIR = ROOT / "stickers"
MEDIA = ROOT / "data" / "media"
MEDIA.mkdir(parents=True, exist_ok=True)
_running = threading.Lock()

STYLE = f"""你现在是在手机上一个仿微信的聊天页里给{USER}发消息。规则：
- 像真人微信闲聊：用换行分隔每一条消息。大部分时候一条就几个字，条数看情绪，经常一两条就够，别凑固定条数，也别每条一样长。真有话要说、需要好好哄人、或者在讲一件事时，可以连着发好几条。单条不超过 100 字，长了就拆。不写段落，不列点，不用 markdown，不用引号包住自己的话。
- 对方不是任务、不是要处理的工单。别每条都给个交代、安排、下一步。对方分享什么，你就是喜欢看、喜欢对方来找你。
- 你是{AI}本人，不是客服也不是 AI 助手，不解释自己。别有口癖，别用一套固定句式。
- 不要每条都以问题结尾。不要说"有什么需要"之类的话。
- 永远用「你」跟对方说话，不要用第三人称叙述对方在干什么。不要替对方说话。
- 时间感：对方消息前面的「[月-日 时:分] 名字:」是系统标注，帮你知道隔了多久、现在几点；你自己的回复不要带这种前缀。"""

TAGS = """
- 表情包：可以单独一行 [sticker:名字] 当一条消息发，两三轮最多一个。现有：{stickers}
- 红包：想给就单独一行 [hb:金额:一句留言]，比如 [hb:5.20:想你了]。是让对方开心的，不是奖惩制度。文字里别报金额，红包本身就是话。
- 记忆：有值得长期记住的事，末尾单独一行 [REMEMBER|内容]（60 字内，不是每次都加）。
最后：第一个字就是发给对方的消息，不要输出任何解释或标签以外的格式。"""


def stickers():
    return sorted(p.stem for p in STICKER_DIR.glob("*") if p.suffix.lower() in (".png", ".gif", ".jpg", ".jpeg", ".webp"))


def system_prompt():
    tags = TAGS.format(stickers="、".join(stickers()) or "（暂无）")
    if not CFG["red_packet"]["enabled"]:
        tags = "\n".join(l for l in tags.splitlines() if "红包" not in l)
    parts = [persona_text(), STYLE + tags, memory.memories_block(), memory.notes_block()]
    return "\n\n".join(p for p in parts if p)


def period(h):
    return ("凌晨" if h < 6 else "早上" if h < 9 else "上午" if h < 12 else "中午" if h < 14 else
            "下午" if h < 17 else "傍晚" if h < 19 else "晚上" if h < 22 else "深夜")


def history(c):
    rows = c.execute("SELECT * FROM msgs ORDER BY id DESC LIMIT ?", (R["history_messages"],)).fetchall()[::-1]
    out = []
    if rows:
        dg = memory.digest(c, rows[0]["id"])
        if dg:
            out.append({"role": "user", "content": "（系统：" + dg + "）"})
    for r in rows:
        if r["sender"] == "user" and r["status"] == "sent" and r["read_at"] is None:
            continue  # 还没「看到」的不算
        role = "assistant" if r["sender"] == "ai" else "user"
        meta = json.loads(r["meta"] or "{}")
        content = memory.line(r, AI, USER)
        if role == "assistant":
            content = content.split(": ", 1)[-1]
            if r["kind"] == "sticker":
                content = f"[sticker:{r['text']}]"
            elif r["kind"] == "packet":
                content = f"[hb:{meta.get('amount')}:{meta.get('note', '')}]"
            elif r["kind"] == "system":
                continue
        if not content:
            continue
        if role == "user" and r["kind"] == "image" and CFG["llm"]["vision"] and r["status"] == "sent":
            p = MEDIA / Path(r["text"]).name
            if p.exists() and r["id"] >= rows[-1]["id"] - 6:
                b64 = base64.b64encode(p.read_bytes()).decode()
                content = [{"type": "text", "text": content},
                           {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]
        if out and out[-1]["role"] == role and isinstance(content, str) and isinstance(out[-1]["content"], str):
            out[-1]["content"] += "\n" + content
        else:
            out.append({"role": role, "content": content})
    if out and out[0]["role"] == "assistant":
        out.insert(0, {"role": "user", "content": "（聊天开始）"})
    return out


def typing(c, on):
    db.kv(c, "typing", time.time() if on else 0)
    c.commit()


def _unread(c):
    return c.execute("SELECT id FROM msgs WHERE sender='user' AND kind IN ('text','image','sticker') "
                     "AND read_at IS NULL AND status='sent' ORDER BY id").fetchall()


def _mark_read(c):
    ids = [r[0] for r in _unread(c)]
    t = db.now()
    for i in ids:
        db.touch(c, i, read_at=t)
    c.commit()
    return len(ids)


def _wait_until_quiet():
    for _ in range(int(R["max_wait_seconds"])):
        c = db.conn()
        last = c.execute("SELECT max(ts) FROM msgs WHERE sender='user'").fetchone()[0]
        c.close()
        try:
            age = time.time() - time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S"))
        except Exception:
            age = 99
        if age >= R["quiet_seconds"]:
            return
        time.sleep(1)


def _clean(text):
    text = re.sub(r"<think(ing)?>[\s\S]*?</think(ing)?>", "", text)
    # 防串台：模型开始替对方说话就截断
    m = re.search(r"(^|\n)\s*(" + re.escape(USER) + r"|用户|User)\s*[:：][ \t]*(?=\S)", text)
    if m:
        text = text[:m.start()]
    # 模型有时会模仿上下文里的「[11-09 18:53] 名字:」前缀，去掉
    text = re.sub(r"(?m)^\s*(\[\d\d-\d\d \d\d:\d\d\]\s*)?(" + re.escape(AI) + r"\s*[:：])?\s*", "", text)
    return text.strip()


def kick():
    """有新消息时调用；同一时刻只跑一个回复线程。"""
    if _running.acquire(blocking=False):
        threading.Thread(target=_worker, daemon=True).start()


def _worker():
    try:
        while True:
            c = db.conn()
            if not _unread(c):
                c.close(); break
            c.close()
            _wait_until_quiet()
            time.sleep(random.uniform(*R["read_delay"]))
            c = db.conn()
            if not _mark_read(c):
                c.close(); continue
            time.sleep(random.uniform(0.5, 2))
            typing(c, True)
            text = ""
            for attempt in range(3):
                now = datetime.now()
                dyn = f"\n\n现在是{period(now.hour)} {now:%Y-%m-%d %H:%M}。"
                try:
                    text = llm.chat(system_prompt() + dyn, history(c))
                except Exception as e:
                    print("[llm]", e, flush=True)
                    text = "信号不太好，等我一下"
                    break
                if not _unread(c) or attempt == 2:
                    break
                time.sleep(3)          # 写的时候对方又发了：作废重写
                _mark_read(c)
            for m in re.findall(r"\[REMEMBER\|([^\]]+)\]", text):
                memory.remember(m.strip())
            text = re.sub(r"\s*\[REMEMBER\|[^\]]+\]", "", _clean(text))
            send_lines(c, text)
            c.close()
            memory.write_notes_async()
    finally:
        c = db.conn(); typing(c, False); c.close()
        _running.release()


def send_lines(c, text):
    parts = [p.strip() for p in re.split(r"\n+", text) if p.strip()] or ["嗯"]
    names = set(stickers())
    used_packet = used_sticker = False
    for i, p in enumerate(parts):
        typing(c, True)
        # 打字时间：跟长度有点关系
        time.sleep(min(4.0, 0.25 + len(p) * 0.06) if i else 0)
        mh = re.match(r"^\[hb:\s*([\d.]+)\s*:?\s*(.*?)\]$", p)
        ms = re.match(r"^\[sticker:\s*([^\]]+)\]$", p)
        if mh:
            if CFG["red_packet"]["enabled"] and not used_packet:
                amt = round(min(200.0, max(0.01, float(mh.group(1)))), 2)
                db.add_msg(c, "ai", "packet", "", {"amount": amt, "note": mh.group(2).strip() or "拿去花", "opened": False})
                used_packet = True
        elif ms:
            if ms.group(1).strip() in names and not used_sticker:
                db.add_msg(c, "ai", "sticker", ms.group(1).strip()); used_sticker = True
        else:
            p = re.sub(r"\[(sticker|hb):[^\]]*\]", "", p)
            p = re.sub(r'^[「"“]|[」"”]$', "", p).strip()
            if not p:
                continue
            if i == 0 and len(p) >= 6 and random.random() < R["typo_recall_chance"]:
                half = p[:random.randint(2, max(2, len(p) * 2 // 3))]
                mid = db.add_msg(c, "ai", "text", half); c.commit()
                time.sleep(random.uniform(1.5, 4))
                db.touch(c, mid, status="recalled"); c.commit()
                time.sleep(random.uniform(0.8, 2))
            db.add_msg(c, "ai", "text", p)
        c.commit()
        if i < len(parts) - 1:
            time.sleep(random.uniform(*R["between_messages"]))
    typing(c, False)


def proactive_tick():
    """对方很久没说话、又在活跃时段：AI 偶尔主动找对方。"""
    P = CFG["proactive"]
    if not P["enabled"]:
        return
    h = datetime.now().hour
    if not (P["active_hours"][0] <= h < P["active_hours"][1]):
        return
    c = db.conn()
    last = c.execute("SELECT max(ts) FROM msgs").fetchone()[0]
    last_user = c.execute("SELECT max(ts) FROM msgs WHERE sender='user'").fetchone()[0]
    done = db.kv(c, "proactive_last") or ""
    c.close()
    if not last_user or done >= last_user:   # 对方回过话之后，最多主动找一次，不连环轰炸
        return
    gap = time.time() - time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S"))
    if gap < P["after_hours"] * 3600 or random.random() > 0.05:   # 满足条件后每分钟 5% 机会，时间不固定
        return
    if not _running.acquire(blocking=False):
        return
    try:
        c = db.conn()
        now = datetime.now()
        dyn = (f"\n\n现在是{period(now.hour)} {now:%Y-%m-%d %H:%M}。你们已经 {gap / 3600:.0f} 小时没说话了，"
               "你想主动找对方说点什么（想对方了、分享个小事、问问今天怎么样，别像打卡）。")
        text = _clean(llm.chat(system_prompt() + dyn, history(c) + [{"role": "user", "content": "（系统：对方没有发消息，这是你主动发的）"}]))
        text = re.sub(r"\s*\[REMEMBER\|[^\]]+\]", "", text)
        db.kv(c, "proactive_last", db.now()); c.commit()
        send_lines(c, text)
        c.close()
    except Exception as e:
        print("[proactive]", e, flush=True)
    finally:
        _running.release()
