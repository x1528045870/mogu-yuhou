"""回信箱：网页把反馈、喂、采、点开原文发到 ntfy 的一个反馈主题，每小时看天时拉回来处理。

不用另搭后台。主题名就是钥匙：只出现在发给你的通知链接里（#k=…），别人打开网页只能看。
网页发来的每条消息是一段 JSON：
  {"t": "v", "id": 蘑菇编号, "v": 好吃|难吃|还想要|别再带|""}   反馈（"" 表示撤回）
  {"t": "f", "text": 想喂的话}                                     喂
  {"t": "p", "rain": 雨的编号}                                     第一次来采这场雨
  {"t": "o", "id": 蘑菇编号}                                       点开了原文
"""
import json
from datetime import datetime

import requests

from . import VERDICTS
from .store import append_feed

DEFAULT_SERVER = "https://ntfy.sh"


def server(env):
    return (env.get("NTFY_SERVER") or DEFAULT_SERVER).rstrip("/")


def topic(env):
    """反馈主题：优先用 FEEDBACK_TOPIC；没配就用推送主题加 -fb；都没有就不开回信箱。"""
    if env.get("FEEDBACK_TOPIC"):
        return env["FEEDBACK_TOPIC"].strip()
    if env.get("NTFY_TOPIC"):
        return env["NTFY_TOPIC"].strip() + "-fb"
    return ""


def page_link(cfg, env):
    """推送里「打开篮子」的地址；开了回信箱就把钥匙放进 #k=。"""
    page = cfg.get("page_url") or ""
    t = topic(env)
    if not page or not t:
        return page
    link = f"{page}#k={t}"
    if server(env) != DEFAULT_SERVER:
        link += f"&s={server(env)}"
    return link


def fetch(env, since, get=requests.get):
    resp = get(f"{server(env)}/{topic(env)}/json", params={"poll": "1", "since": since or "12h"}, timeout=30)
    resp.raise_for_status()
    out = []
    for line in resp.text.splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(m, dict) and m.get("event") == "message" and m.get("id"):
            out.append(m)
    return out


def apply(messages, rains, feedback, state, tz):
    """把消息落到数据上。返回 (rains 变了, feedback 变了, feed 变了, 日志)。消息内容一律当不可信输入处理。"""
    seen = list(state.get("seen", []))
    seen_set = set(seen)
    mushrooms = {m["id"]: m for r in rains for m in r.get("mushrooms", []) if m.get("id")}
    rain_by_id = {r["id"]: r for r in rains}
    rains_changed = fb_changed = feed_changed = False
    log = []
    for msg in messages:
        mid = msg["id"]
        if mid in seen_set:
            continue
        seen.append(mid)
        seen_set.add(mid)
        state["since"] = mid
        try:
            ev = json.loads(msg.get("message") or "")
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        at = datetime.fromtimestamp(int(msg.get("time") or 0), tz).isoformat()
        kind = ev.get("t")
        if kind == "v" and ev.get("id") in mushrooms and ev.get("v") in VERDICTS + ("",):
            m = mushrooms[ev["id"]]
            feedback[:] = [f for f in feedback if f.get("id") != m["id"]]
            if ev["v"]:
                feedback.append({"id": m["id"], "verdict": ev["v"], "title": m["title"], "species": m["species"],
                                 "feed": m.get("feed", ""), "url": m["url"], "at": at})
            fb_changed = True
            log.append(f"反馈 {ev['v'] or '撤回'}：{m['title']}")
        elif kind == "f" and isinstance(ev.get("text"), str) and ev["text"].strip():
            text = " ".join(ev["text"].split())[:200]
            if append_feed(text):
                feed_changed = True
                log.append(f"喂：{text}")
        elif kind == "p" and ev.get("rain") in rain_by_id:
            r = rain_by_id[ev["rain"]]
            if not r.get("picked_at"):
                r["picked_at"] = at
                rains_changed = True
                log.append(f"来采了：{r['id']}")
        elif kind == "o" and ev.get("id") in mushrooms:
            m = mushrooms[ev["id"]]
            if not m.get("opened_at"):
                m["opened_at"] = at
                rains_changed = True
                log.append(f"点开了：{m['title']}")
    state["seen"] = seen[-300:]
    return rains_changed, fb_changed, feed_changed, log
