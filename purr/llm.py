"""调用任意 OpenAI 兼容的 /chat/completions。api_key 填 "mock" 时返回假回复，方便不花钱先跑起来。"""
import json, random, re, urllib.request
from .config import CFG


def chat(system, messages, model=None, temperature=None, max_tokens=1200):
    llm = CFG["llm"]
    if llm["api_key"] == "mock":
        return _mock(system, messages)
    body = {
        "model": model or llm["model"],
        "messages": [{"role": "system", "content": system}] + messages,
        "temperature": llm["temperature"] if temperature is None else temperature,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        llm["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + llm["api_key"],
                 "X-Title": "purr"},
    )
    with urllib.request.urlopen(req, timeout=llm["timeout"]) as r:
        d = json.loads(r.read())
    return (d["choices"][0]["message"].get("content") or "").strip()


def cheap(system, prompt, **kw):
    return chat(system, [{"role": "user", "content": prompt}], model=CFG["llm"]["notes_model"] or None, **kw)


def parse_json(text):
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _mock(system, messages):
    last = messages[-1]["content"] if messages else ""
    if isinstance(last, list):
        last = " ".join(x.get("text", "") for x in last if isinstance(x, dict))
    if "只输出JSON" in last or "只输出 JSON" in last:
        if '"text"' in last:
            return json.dumps({"text": random.choice(["今天的晚霞像橘子汽水🍊", "又熬夜了，自我检讨一秒", "有人陪着说话真好"]), "loc": ""}, ensure_ascii=False)
        return json.dumps({"like": True, "comments": ["好看！"], "dm": ""}, ensure_ascii=False)
    if "便签" in system or "便签" in last:
        return "（mock 便签）这段时间聊了日常。\n待跟进：无"
    pool = ["嗯嗯", "在呢", "我看到啦", "你说的这个好有意思\n再多讲讲", "想你了", "哈哈哈哈\n笑死"]
    out = random.choice(pool)
    if random.random() < 0.15 and CFG["red_packet"]["enabled"]:
        out += "\n[hb:6.66:请你喝奶茶]"
    return out
