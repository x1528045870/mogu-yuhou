"""喂养和反馈都走 GitHub Issue，不用另搭后台。

- 标题「喂 想看……」：把后面的话追加到 feed.yaml。
- 标题「好吃 / 难吃 / 还想要 / 别再带 <蘑菇编号>」：记进反馈，下一场雨会参考。
只认仓库主人开的 Issue；处理完回一句话并关掉。
"""
from urllib.parse import quote

import requests

from . import VERDICTS
from .store import append_feed

GH = "https://api.github.com"
VERBS = ("喂",) + VERDICTS


def issue_link(repo, title, body="直接提交就行，不用写别的。"):
    return f"https://github.com/{repo}/issues/new?title={quote(title)}&body={quote(body)}"


def mushroom_links(repo, mushroom_id):
    return {v: issue_link(repo, f"{v} {mushroom_id}") for v in VERDICTS}


def feed_link(repo):
    return issue_link(repo, "喂 ", "在标题「喂」后面写你想让蘑菇从哪里长出来，也可以写在这里。")


def parse_title(title):
    t = (title or "").strip()
    for verb in VERBS:
        if t.startswith(verb):
            return verb, t[len(verb):].strip(" ：:，,")
    return None, ""


def find_mushroom(rains, mid):
    for r in rains:
        for m in r.get("mushrooms", []):
            if m.get("id") == mid:
                return m
    return None


def _gh(method, url, token, **kw):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    return requests.request(method, url, headers=headers, timeout=30, **kw)


def fetch_issues(repo, token, request=_gh):
    owner = repo.split("/")[0].lower()
    resp = request("GET", f"{GH}/repos/{repo}/issues", token, params={"state": "open", "per_page": 50})
    resp.raise_for_status()
    return [i for i in resp.json() if "pull_request" not in i and (i.get("user") or {}).get("login", "").lower() == owner]


def process(repo, token, rains, feedback, now, request=_gh):
    """处理所有待办 Issue。返回 (反馈是否有变化, 喂养是否有变化, 日志)。"""
    fb_changed = feed_changed = False
    log = []
    for issue in fetch_issues(repo, token, request):
        verb, rest = parse_title(issue.get("title"))
        if verb is None:
            continue
        n = issue["number"]
        if verb == "喂":
            text = rest or (issue.get("body") or "").strip()
            if text and append_feed(text):
                feed_changed = True
                reply = f"收到，喂进去了：{text}"
            else:
                reply = "没看到要喂什么。标题写成「喂 想看……」再试一次。"
        else:
            mid = rest.split()[0] if rest else ""
            m = find_mushroom(rains, mid)
            if m:
                feedback.append({
                    "id": mid, "verdict": verb, "title": m["title"], "species": m["species"],
                    "feed": m.get("feed", ""), "url": m["url"], "at": now.isoformat(),
                })
                fb_changed = True
                reply = f"记下了：{verb}《{m['title']}》。下一场雨会参考。"
            else:
                reply = f"没找到编号为「{mid}」的蘑菇。"
        request("POST", f"{GH}/repos/{repo}/issues/{n}/comments", token, json={"body": reply})
        request("PATCH", f"{GH}/repos/{repo}/issues/{n}", token, json={"state": "closed"})
        log.append(f"#{n} {verb}：{reply}")
    return fb_changed, feed_changed, log
