"""读取 config.json；缺的字段用默认值补上。"""
import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "ai_name": "小橘",                 # AI 在聊天里的名字
    "user_name": "你",                 # AI 怎么称呼你（写进提示词）
    "persona_file": "persona.md",      # 人设文件，找不到就用 persona.example.md
    "llm": {
        # 任何 OpenAI 兼容接口都行：OpenRouter / DeepSeek / OpenAI / 硅基流动 / Ollama ...
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "mock",             # 填 mock 可以不花钱先看界面效果
        "model": "anthropic/claude-sonnet-4.5",
        "notes_model": "",             # 写便签/朋友圈用的模型，留空=同上
        "temperature": 0.9,
        "vision": False,               # 模型能看图就开
        "timeout": 120,
    },
    "realism": {
        "quiet_seconds": 5,            # 你最后一条消息落地后安静这么久才开始回（等你说完）
        "max_wait_seconds": 15,        # 最多等这么久
        "read_delay": [0.5, 2.0],      # 已读前的随机延迟
        "between_messages": [0.8, 2.5],# 多条消息之间的间隔
        "typo_recall_chance": 0.04,    # 「手滑发了一半 → 撤回 → 重发」的概率
        "history_messages": 40,        # 原样带进上下文的最近消息条数
        "digest_hours": 8,             # 更早但在这些小时内的消息压成摘要
    },
    "notes": {"enabled": True, "every": 20, "show": 4},
    "moments": {"enabled": True, "max_posts_per_day": 2, "react_delay_minutes": [2, 15]},
    "proactive": {"enabled": True, "after_hours": 5, "active_hours": [9, 23]},
    "red_packet": {"enabled": True, "start_balance": 0},
    "access_key": "",                  # 部署到公网时务必设置：访问需带 ?key=xxx
    "host": "0.0.0.0",
    "port": 8765,
    "db_path": "data/purr.db",
}


def _merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out


def load():
    p = Path(os.environ.get("PURR_CONFIG", ROOT / "config.json"))
    user = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    cfg = _merge(DEFAULTS, user)
    if os.environ.get("PURR_API_KEY"):
        cfg["llm"]["api_key"] = os.environ["PURR_API_KEY"]
    return cfg


CFG = load()


def persona_text():
    for name in (CFG["persona_file"], "persona.example.md"):
        p = ROOT / name
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return f"你是{CFG['ai_name']}。"
