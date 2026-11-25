"""出去采蘑菇：让 Claude 联网搜索，带回 1–3 朵。链接必须出现在这次的搜索结果里，否则不带。"""
import json
import re
from urllib.parse import urlsplit, urlunsplit

import requests

from . import EDIBLE, SPECIES

API = "https://api.anthropic.com/v1/messages"

# 美元 / 百万 token（输入, 输出），用来估算每场雨花了多少钱
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
SEARCH_PRICE = 0.01

SYSTEM = """你在为「蘑菇·雨后」采蘑菇。雨刚停，你要到网上走一圈，带回 1 到 3 朵蘑菇，也就是 1 到 3 个真正值得他看的东西。

规则：
- 先用搜索工具去找，只带你在这次搜索结果里亲眼看到的页面。链接必须原样来自搜索结果，不许凭记忆写链接。
- 宁缺毋滥。找不到好的，就只带 1 朵。核实不了真假的东西不带。
- 优先最近两周的东西；经典的、不过时的好东西也可以。
- 不带营销软文、标题党、聚合搬运页；尽量链接到原始出处。
- 不重复已经带过的东西。
- 品种（必须从这五个里选）：
  猴头菇 = 值得慢读的长文、论文、深度报道
  鸡枞 = 新鲜事、新品、新消息
  竹荪 = 好看的东西，比如图、作品、设计
  松茸 = 稀有的好东西（只有真的很难得才用）
  野蘑菇 = 来自喂过的范围之外，但值得他走出去看一眼
- 能不能吃：可靠来源、可以多方核实的标「能吃」；单一来源、刚出的消息、观点性内容标「先闻闻」。
- 用中文写，平实、短。what 一句话说清是什么（40 字以内），why 说为什么带给他（50 字以内，点明对应喂过的哪一条；野蘑菇就说为什么值得走出去看）。
- 不要写开场白，不要解释你怎么找的。

最后只输出一个 JSON 代码块：
```json
{"mushrooms": [{"species": "猴头菇", "title": "原文标题", "what": "…", "why": "…", "url": "https://…", "source": "出处名", "edible": "能吃", "feed": "对应喂过的哪一条（简写）或 野"}]}
```"""


def norm_url(url):
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return ""
    scheme = "https" if p.scheme in ("http", "https") else p.scheme
    path = p.path.rstrip("/") or "/"
    return urlunsplit((scheme, p.netloc.lower().removeprefix("www."), path, p.query, ""))


def build_prompt(feed, feedback, rains, now, need_lost, matsutake_ok):
    lines = [f"现在是 {now:%Y-%m-%d %H:%M}（{now.tzinfo}）。", "", "【喂过的】"]
    lines += [f"- {x}" for x in feed["feed"]] or ["- （还没喂）"]
    if feed["avoid"]:
        lines += ["", "【不要的】"] + [f"- {x}" for x in feed["avoid"]]
    recent_fb = feedback[-20:]
    if recent_fb:
        lines += ["", "【他最近的反馈】（还想要 = 多来点这类；别再带 = 这类不要再带）"]
        lines += [f"- {f['verdict']}：{f.get('species', '')}《{f.get('title', '')}》（{f.get('feed', '')}）" for f in recent_fb]
    seen = [m for r in rains[-15:] for m in r.get("mushrooms", [])]
    if seen:
        lines += ["", "【已经带过的，不要重复】"] + [f"- {m['title']} {m['url']}" for m in seen[-40:]]
    lines += ["", "【这场雨】"]
    lines.append("- 这场雨要带一朵野蘑菇（喂过的范围之外）。" if need_lost else "- 这场雨不用带野蘑菇。")
    if not matsutake_ok:
        lines.append("- 这周已经有过松茸，这次不要用松茸。")
    lines.append("去吧。")
    return "\n".join(lines)


def call_claude(prompt, cfg, api_key, post=requests.post):
    """调用 Messages API，带 web search 工具；处理 pause_turn。返回 (所有内容块, 用量)。"""
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": cfg["max_searches"]}]
    messages = [{"role": "user", "content": prompt}]
    blocks, usage = [], {"input_tokens": 0, "output_tokens": 0, "searches": 0}
    for _ in range(4):
        body = {"model": cfg["model"], "max_tokens": 4000, "system": SYSTEM, "messages": messages, "tools": tools}
        resp = post(API, headers=headers, json=body, timeout=300)
        if resp.status_code >= 400:
            raise RuntimeError(f"Claude API 出错 {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        blocks += data.get("content", [])
        u = data.get("usage") or {}
        usage["input_tokens"] += u.get("input_tokens", 0)
        usage["output_tokens"] += u.get("output_tokens", 0)
        usage["searches"] += (u.get("server_tool_use") or {}).get("web_search_requests", 0)
        if data.get("stop_reason") != "pause_turn":
            break
        messages = [{"role": "user", "content": prompt}, {"role": "assistant", "content": blocks}]
    return blocks, usage


def estimate_cost(usage, model):
    pin, pout = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    return round(usage["input_tokens"] / 1e6 * pin + usage["output_tokens"] / 1e6 * pout + usage["searches"] * SEARCH_PRICE, 4)


def seen_urls(blocks):
    urls = set()
    for b in blocks:
        if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            for r in b["content"]:
                if r.get("type") == "web_search_result" and r.get("url"):
                    urls.add(norm_url(r["url"]))
        if b.get("type") == "text":
            for c in b.get("citations") or []:
                if c.get("url"):
                    urls.add(norm_url(c["url"]))
    return urls


def extract_json(blocks):
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    candidates = list(reversed(fenced))
    if "{" in text:
        candidates.append(text[text.find("{"): text.rfind("}") + 1])
    for c in candidates:
        try:
            value = json.loads(c)
            if isinstance(value, dict) and isinstance(value.get("mushrooms"), list):
                return value
        except json.JSONDecodeError:
            continue
    return {"mushrooms": []}


def _clip(value, n):
    s = " ".join(str(value or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def validate(raw, found, history, matsutake_ok):
    """过一遍筛子：链接必须来自搜索结果、没带过、品种和标签合法。返回 (留下的, 扔掉的原因)。"""
    kept, dropped, taken = [], [], set()
    for m in raw.get("mushrooms", [])[:6]:
        url = str(m.get("url") or "").strip()
        key = norm_url(url)
        title = _clip(m.get("title"), 80) or "（无题）"
        if not url.startswith(("https://", "http://")):
            dropped.append(f"{title}：没有链接")
            continue
        if key not in found:
            dropped.append(f"{title}：链接不在这次的搜索结果里")
            continue
        if key in history or key in taken:
            dropped.append(f"{title}：带过了")
            continue
        species = m.get("species") if m.get("species") in SPECIES else "鸡枞"
        if species == "松茸" and not matsutake_ok:
            species = "猴头菇"
        lost = species == "野蘑菇" or str(m.get("feed", "")).strip() == "野"
        if lost:
            species = "野蘑菇"
        kept.append({
            "species": species,
            "title": title,
            "what": _clip(m.get("what"), 60),
            "why": _clip(m.get("why"), 70),
            "url": url,
            "source": _clip(m.get("source"), 30) or urlsplit(url).netloc,
            "edible": m.get("edible") if m.get("edible") in EDIBLE else "先闻闻",
            "feed": "野" if lost else _clip(m.get("feed"), 24),
            "lost": lost,
        })
        taken.add(key)
        if len(kept) == 3:
            break
    return kept, dropped


def forage(feed, feedback, rains, now, cfg, api_key, need_lost, matsutake_ok, post=requests.post):
    prompt = build_prompt(feed, feedback, rains, now, need_lost, matsutake_ok)
    blocks, usage = call_claude(prompt, cfg, api_key, post=post)
    history = {norm_url(m["url"]) for r in rains for m in r.get("mushrooms", [])}
    kept, dropped = validate(extract_json(blocks), seen_urls(blocks), history, matsutake_ok)
    return kept, {"dropped": dropped, "searches": usage["searches"], "cost_usd": estimate_cost(usage, cfg["model"])}
