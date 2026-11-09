import json, threading, time, uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Body
from fastapi.responses import FileResponse, HTMLResponse
from . import db, chat, moments
from .config import CFG, ROOT

app = FastAPI(title="purr")
STATIC = ROOT / "static"


@app.middleware("http")
async def guard(request: Request, call_next):
    key = CFG["access_key"]
    if key and request.url.path != "/favicon.ico":
        got = request.query_params.get("key") or request.headers.get("x-purr-key") or request.cookies.get("purr_key")
        if got != key:
            return HTMLResponse("need ?key=", status_code=401)
    resp = await call_next(request)
    if key and request.query_params.get("key") == key:
        resp.set_cookie("purr_key", key, max_age=3600 * 24 * 365, httponly=True, samesite="lax")
    return resp


@app.get("/")
def index():
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/api/info")
def info():
    c = db.conn()
    bal = float(db.kv(c, "balance") or CFG["red_packet"]["start_balance"])
    avatars = {w: db.kv(c, "avatar_" + w) or "" for w in ("user", "ai")}
    c.close()
    return {"ai_name": CFG["ai_name"], "user_name": CFG["user_name"], "stickers": chat.stickers(),
            "balance": bal, "moments": CFG["moments"]["enabled"], "red_packet": CFG["red_packet"]["enabled"],
            "avatars": avatars}


async def _save_image(file, prefix):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "不是图片")
    name = prefix + "_" + uuid.uuid4().hex[:10] + (Path(file.filename or "x.jpg").suffix.lower()[:6] or ".jpg")
    (chat.MEDIA / name).write_bytes(await file.read())
    return "/media/" + name


@app.post("/api/avatar")
async def avatar(who: str = Form(...), file: UploadFile = File(...)):
    """换头像：who = user（你）或 ai"""
    if who not in ("user", "ai"):
        raise HTTPException(400)
    url = await _save_image(file, "avatar_" + who)
    c = db.conn(); db.kv(c, "avatar_" + who, url); c.commit(); c.close()
    return {"url": url}


@app.get("/api/poll")
def poll(rev: int = 0):
    c = db.conn()
    rows = [db.row(r) for r in c.execute("SELECT * FROM msgs WHERE rev>? ORDER BY id", (rev,))]
    if rev == 0:
        rows = rows[-200:]
    typing = time.time() - float(db.kv(c, "typing") or 0) < 40
    top = c.execute("SELECT coalesce(max(rev), 0) FROM msgs").fetchone()[0]
    unseen = c.execute("SELECT count(*) FROM moment_comments WHERE who='ai' AND ts>?",
                       (db.kv(c, "moments_seen") or "",)).fetchone()[0] if CFG["moments"]["enabled"] else 0
    c.close()
    return {"msgs": rows, "typing": typing, "rev": top, "moments_unseen": unseen}


@app.post("/api/say")
def say(p: dict = Body(...)):
    text = str(p.get("text") or "").strip()[:2000]
    kind = p.get("kind") or "text"
    if kind not in ("text", "sticker") or not text:
        raise HTTPException(400)
    c = db.conn()
    mid = db.add_msg(c, "user", kind, text)
    c.commit(); c.close()
    chat.kick()
    return {"id": mid}


@app.post("/api/image")
async def image(file: UploadFile = File(...), caption: str = Form("")):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400)
    name = uuid.uuid4().hex + Path(file.filename or "x.jpg").suffix.lower()[:6]
    (chat.MEDIA / name).write_bytes(await file.read())
    c = db.conn()
    mid = db.add_msg(c, "user", "image", name, {"caption": caption[:200]})
    c.commit(); c.close()
    chat.kick()
    return {"id": mid}


@app.get("/media/{name}")
def media(name: str):
    p = chat.MEDIA / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/sticker/{name}")
def sticker(name: str):
    for p in (ROOT / "stickers").glob(Path(name).name + ".*"):
        return FileResponse(p)
    raise HTTPException(404)


@app.post("/api/recall")
def recall(p: dict = Body(...)):
    c = db.conn()
    r = c.execute("SELECT * FROM msgs WHERE id=? AND sender='user'", (int(p.get("id", 0)),)).fetchone()
    if not r:
        raise HTTPException(404)
    db.touch(c, r["id"], status="recalled")
    c.commit(); c.close()
    return {"ok": True}


@app.post("/api/packet/open")
def open_packet(p: dict = Body(...)):
    c = db.conn()
    r = c.execute("SELECT * FROM msgs WHERE id=? AND kind='packet'", (int(p.get("id", 0)),)).fetchone()
    if not r:
        raise HTTPException(404)
    meta = json.loads(r["meta"])
    if not meta.get("opened"):
        meta["opened"] = True
        meta["opened_at"] = db.now()
        bal = float(db.kv(c, "balance") or CFG["red_packet"]["start_balance"]) + float(meta["amount"])
        db.kv(c, "balance", round(bal, 2))
        db.touch(c, r["id"], meta=json.dumps(meta, ensure_ascii=False))
        c.commit()
    bal = float(db.kv(c, "balance") or 0)
    c.close()
    return {"amount": meta["amount"], "note": meta.get("note", ""), "balance": bal}


# ---------- 朋友圈 ----------
@app.get("/api/moments")
def moments_feed(page: int = 0):
    c = db.conn()
    out = moments.feed(c, page)
    cover = db.kv(c, "moments_cover") or ""
    c.close()
    return {"items": out, "cover": cover}


@app.post("/api/moments/cover")
async def moments_cover(file: UploadFile = File(...)):
    """换朋友圈背景"""
    url = await _save_image(file, "cover")
    c = db.conn(); db.kv(c, "moments_cover", url); c.commit(); c.close()
    return {"url": url}


@app.post("/api/moments/comment/delete")
def moments_comment_delete(p: dict = Body(...)):
    """删除单条评论（你的或 AI 的都能删）"""
    c = db.conn()
    c.execute("UPDATE moment_comments SET status='deleted' WHERE id=?", (int(p["id"]),))
    c.commit(); c.close()
    return {"ok": True}


@app.post("/api/moments/seen")
def moments_seen():
    c = db.conn(); db.kv(c, "moments_seen", db.now()); c.commit(); c.close()
    return {"ok": True}


@app.post("/api/moments/post")
async def moments_post(request: Request):
    form = await request.form()
    text = str(form.get("text") or "")[:1000]
    imgs = []
    for f in list(form.getlist("images"))[:9]:
        if hasattr(f, "read") and (getattr(f, "content_type", "") or "").startswith("image/"):
            name = uuid.uuid4().hex + ".jpg"
            (chat.MEDIA / name).write_bytes(await f.read())
            imgs.append("/media/" + name)
    if not text.strip() and not imgs:
        raise HTTPException(400, "空的")
    c = db.conn()
    c.execute("INSERT INTO moments(ts, author, text, imgs, loc) VALUES(?,?,?,?,?)",
              (db.now(), "user", text, json.dumps(imgs), str(form.get("loc") or "")[:30]))
    mid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    moments.on_user_post(c, mid)
    c.commit(); c.close()
    return {"id": mid}


@app.post("/api/moments/like")
def moments_like(p: dict = Body(...)):
    c = db.conn(); mid = int(p["mid"])
    if c.execute("SELECT 1 FROM moment_likes WHERE mid=? AND who='user'", (mid,)).fetchone():
        c.execute("DELETE FROM moment_likes WHERE mid=? AND who='user'", (mid,))
    else:
        c.execute("INSERT INTO moment_likes(mid, who, ts) VALUES(?,?,?)", (mid, "user", db.now()))
    c.commit(); c.close()
    return {"ok": True}


@app.post("/api/moments/comment")
def moments_comment(p: dict = Body(...)):
    text = str(p.get("text") or "").strip()[:300]
    if not text:
        raise HTTPException(400)
    c = db.conn(); mid = int(p["mid"]); reply_to = str(p.get("reply_to") or "")
    c.execute("INSERT INTO moment_comments(mid, who, text, reply_to, ts) VALUES(?,?,?,?,?)", (mid, "user", text, reply_to, db.now()))
    moments.on_user_comment(c, mid, reply_to)
    c.commit(); c.close()
    return {"ok": True}


@app.post("/api/moments/delete")
def moments_delete(p: dict = Body(...)):
    c = db.conn(); c.execute("UPDATE moments SET status='deleted' WHERE id=? AND author='user'", (int(p["id"]),)); c.commit(); c.close()
    return {"ok": True}


# ---------- 后台 ----------
def _loop():
    while True:
        for fn in (moments.tick, chat.proactive_tick):
            try:
                fn()
            except Exception as e:
                print("[tick]", fn.__name__, e, flush=True)
        time.sleep(60)


@app.on_event("startup")
def _start():
    db.init()
    c = db.conn()
    if c.execute("SELECT 1 FROM msgs WHERE sender='user' AND read_at IS NULL AND status='sent'").fetchone():
        chat.kick()
    c.close()
    threading.Thread(target=_loop, daemon=True).start()
