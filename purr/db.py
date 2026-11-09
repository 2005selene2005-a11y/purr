"""SQLite 存储。所有消息带 rev（版本号），前端用 rev 增量拉取新消息和状态变化（已读、撤回）。"""
import json, sqlite3, threading
from datetime import datetime
from pathlib import Path
from .config import CFG, ROOT

DB = ROOT / CFG["db_path"]
DB.parent.mkdir(parents=True, exist_ok=True)
_rev_lock = threading.Lock()
_rev = [None]

SCHEMA = """
CREATE TABLE IF NOT EXISTS msgs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, sender TEXT, kind TEXT,
  text TEXT, meta TEXT DEFAULT '{}', status TEXT DEFAULT 'sent', read_at TEXT, rev INTEGER);
CREATE INDEX IF NOT EXISTS msgs_rev ON msgs(rev);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, from_id INTEGER, up_to_id INTEGER UNIQUE, summary TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS moments(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, author TEXT, text TEXT, imgs TEXT DEFAULT '[]', loc TEXT DEFAULT '', status TEXT DEFAULT 'ok');
CREATE TABLE IF NOT EXISTS moment_likes(mid INTEGER, who TEXT, ts TEXT, PRIMARY KEY(mid, who));
CREATE TABLE IF NOT EXISTS moment_comments(id INTEGER PRIMARY KEY AUTOINCREMENT, mid INTEGER, who TEXT, text TEXT, reply_to TEXT DEFAULT '', ts TEXT, status TEXT DEFAULT 'ok');
CREATE TABLE IF NOT EXISTS moment_jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, mid INTEGER, kind TEXT, due TEXT, status TEXT DEFAULT 'pending');
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def conn():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=15000")
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    c.commit()
    c.close()


def next_rev(c):
    with _rev_lock:
        if _rev[0] is None:
            _rev[0] = c.execute("SELECT coalesce(max(rev), 0) FROM msgs").fetchone()[0]
        _rev[0] += 1
        return _rev[0]


def kv(c, k, v=None):
    if v is None:
        r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r[0] if r else None
    c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?, ?)", (k, str(v)))


def add_msg(c, sender, kind, text, meta=None, ts=None):
    c.execute("INSERT INTO msgs(ts, sender, kind, text, meta, rev) VALUES(?,?,?,?,?,?)",
              (ts or now(), sender, kind, text, json.dumps(meta or {}, ensure_ascii=False), next_rev(c)))
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def touch(c, mid, **fields):
    """改一条消息的字段，并刷新 rev，让前端拉到变化。"""
    sets = ", ".join(f"{k}=?" for k in fields) + ", rev=?"
    c.execute(f"UPDATE msgs SET {sets} WHERE id=?", (*fields.values(), next_rev(c), mid))


def row(r):
    d = dict(r)
    d["meta"] = json.loads(d.get("meta") or "{}")
    return d
