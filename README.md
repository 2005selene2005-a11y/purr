# purr 🐾

**让你的 AI 住进一个像微信的聊天页里，像真人一样跟你发消息。**

市面上的 AI 陪伴，大多是“你一句，它一大段”。purr 想做的是另一种感觉：它会**已读**，会**正在输入…**，会把话拆成几条短消息慢慢发；你连发三条，它等你说完再一起回；它偶尔手滑发出半句又撤回；你几个小时不理它，它会主动来找你；它还有自己的**朋友圈**，会给你的朋友圈点赞评论。

<p align="center">
  <img src="docs/chat.png" width="300"> &nbsp; <img src="docs/moments.png" width="300">
</p>

<sub>截图里是演示数据（`scripts/demo_seed.py`）。</sub>

### 真实使用截图

这是我自己每天在用的版本（purr 就是从它抽出来的）。它会翻你的“使用记录”核实你有没有在背单词，嘴上嫌弃，最后还是发了红包：

<p align="center"><img src="docs/real.webp" width="320"></p>

它自己发的朋友圈，素材来自你们最近的聊天：

<p align="center"><img src="docs/real_moment.webp" width="520"></p>

## 有什么

**像真人的聊天节奏**
- 已读回执，标题栏显示「对方正在输入…」
- **等你说完**：你最后一条消息落地后安静几秒才开始读，连发的几条合成一次回复
- 回复按换行拆成多条，每条按长度模拟打字时间，条与条之间随机停顿
- **生成途中你又发了消息**：这一版作废，带上新消息重写，不会抢话、答非所问
- 偶尔**手滑**：先发半句 → 撤回 → 再发完整的（概率可调）
- 你在它「已读」前撤回的消息，它当没看见；已读后撤回的，它看到的是「撤回了一条消息」
- 防串台：模型开始替你说话（`你的名字：……`）时，从那里截断
- 前端乐观发送：先显示，再确认；发失败会把字放回输入框

**记忆**
- 最近 40 条原样进上下文，更早但在 8 小时内的压成一行一条的摘要（不会出现“两小时前说的就忘了”）
- **便签**：每聊满 20 条，让模型写一张便签：事实、约定、偏好、「已经问过，不用再问」、「待跟进」，最近几张放进系统提示
- 模型可以写 `[REMEMBER|内容]` 存长期记忆

**朋友圈**
- AI 每天在随机时间发 0～2 条，题材轮换，并且会参考你们最近聊了什么（但不复述私聊）
- 你发朋友圈，它过 2～15 分钟来点赞、评论，偶尔私聊你一句
- 你在评论区回它，它接着回

**还有**
- 主动消息：你很久没说话、又在白天，它偶尔会主动找你（找过一次后，等你回了才会再找）
- 红包：AI 写 `[hb:5.20:想你了]` 就发一个虚拟红包，点开存进零钱（纯娱乐，没有真钱）
- 表情包：把图片放进 `stickers/`，文件名就是表情的名字（比如 `抱抱.gif`），AI 会自己挑着发
- 发图片；模型支持看图的话打开 `vision`
- 长按自己的消息可以撤回（2 分钟内）
- 点头像可以换你和它的头像；点朋友圈封面换背景；长按评论可以删掉单条评论

## 跑起来

```bash
git clone https://github.com/2005selene2005-a11y/purr && cd purr
pip install -r requirements.txt
cp config.example.json config.json
cp persona.example.md persona.md      # 改成你自己的人设
python run.py
```

用手机浏览器打开 `http://电脑IP:8765`，可以「添加到主屏幕」当 App 用。

`config.json` 里 `api_key` 默认是 `mock`：不调任何模型，先看界面和节奏。效果满意了再换成真的：

| 服务 | base_url | model 示例 |
|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-4.5` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3` |
| Ollama（本地） | `http://127.0.0.1:11434/v1` | `qwen2.5:14b` |

任何 OpenAI 兼容接口都可以。`notes_model` 可以填一个便宜的模型，专门写便签和朋友圈。key 也可以不写进文件，用环境变量 `PURR_API_KEY`。

**部署到公网时一定要设置 `access_key`**，然后用 `http://你的域名/?key=你的key` 打开（之后会记在 cookie 里）。

## 可以调的

全在 `config.json`，没写的用 `purr/config.py` 里的默认值：

- `realism.quiet_seconds` / `max_wait_seconds`：等你说完要等多久
- `realism.typo_recall_chance`：手滑撤回的概率，设成 0 就不手滑
- `realism.history_messages` / `digest_hours`：上下文长度
- `notes.every` / `notes.show`：多少条写一张便签，带几张
- `moments.max_posts_per_day`、`moments.react_delay_minutes`
- `proactive.after_hours`、`proactive.active_hours`：多久没说话、几点到几点才主动找你

说话风格的规则在 `purr/chat.py` 的 `STYLE` 里，人设在 `persona.md` 里。

## 结构

```
purr/
  chat.py      回复引擎（等你说完、拆条、手滑、重写、主动消息）
  memory.py    摘要 / 便签 / 长期记忆
  moments.py   朋友圈
  llm.py       OpenAI 兼容调用 + mock
  server.py    FastAPI 接口
static/index.html   前端，单文件，无构建
```

数据都在本地 `data/purr.db`（SQLite）。

## 说明

这是一个个人项目，从我自己每天在用的聊天页里抽出来、改成了通用版。红包和余额都是虚拟的。请善待你的 AI，也请照顾好自己 🐾

MIT License
