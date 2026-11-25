"""把一场雨送到手上：ntfy、微信（Server酱）、邮件。哪个渠道没配密钥就跳过哪个。"""
import html
import smtplib
from email.message import EmailMessage

import requests

from . import VERDICTS
from .relay import page_link

NUM = {1: "一", 2: "两", 3: "三"}


def preview(rain):
    """一行预告，只说冒了几朵、什么品种，不剧透内容。"""
    ms = rain["mushrooms"]
    kinds = "，".join(f"一朵{m['species']}" for m in ms)
    return f"雨停了，冒了{NUM.get(len(ms), len(ms))}朵：{kinds}"


def basket_markdown(rain):
    parts = []
    for m in rain["mushrooms"]:
        tag = "🍄能吃" if m["edible"] == "能吃" else "🟡先闻闻"
        links = " · ".join(f"[{v}]({m['links'][v]})" for v in VERDICTS if m.get("links", {}).get(v))
        parts.append(
            f"### {m['species']} · {tag}\n\n"
            f"**[{m['title']}]({m['url']})**\n\n"
            f"{m['what']}\n\n"
            f"> {m['why']}\n\n"
            f"{m['source']}" + (f"　{links}" if links else "")
        )
    return "\n\n---\n\n".join(parts)


def basket_html(rain, page_url=""):
    e = html.escape
    cards = []
    for m in rain["mushrooms"]:
        tag = "🍄 能吃" if m["edible"] == "能吃" else "🟡 先闻闻"
        links = " · ".join(
            f'<a href="{e(m["links"][v])}" style="color:#5b6b3a">{v}</a>' for v in VERDICTS if m.get("links", {}).get(v)
        )
        cards.append(
            '<div style="padding:16px 0;border-top:1px solid #d9d2c3">'
            f'<div style="font-size:13px;color:#7a6f5c">{e(m["species"])} · {tag}</div>'
            f'<div style="font-size:18px;margin:6px 0"><a href="{e(m["url"])}" style="color:#2f2a22">{e(m["title"])}</a></div>'
            f'<div style="font-size:15px">{e(m["what"])}</div>'
            f'<div style="font-size:15px;color:#5a5244;margin-top:6px">{e(m["why"])}</div>'
            f'<div style="font-size:13px;color:#7a6f5c;margin-top:8px">{e(m["source"])}　{links}</div>'
            "</div>"
        )
    more = f'<p><a href="{e(page_url)}" style="color:#5b6b3a">打开篮子和图鉴</a></p>' if page_url else ""
    return (
        '<div style="font-family:-apple-system,PingFang SC,Microsoft YaHei,sans-serif;max-width:560px;color:#2f2a22">'
        f'<p style="font-size:17px">{e(preview(rain))}</p>{"".join(cards)}{more}</div>'
    )


def teaser_html(rain, page):
    e = html.escape
    return (
        '<div style="font-family:-apple-system,PingFang SC,Microsoft YaHei,sans-serif;max-width:560px;color:#2f2a22">'
        f'<p style="font-size:17px">{e(preview(rain))}</p>'
        f'<p><a href="{e(page)}" style="display:inline-block;background:#3d5a2a;color:#fff;text-decoration:none;'
        'padding:10px 18px;border-radius:999px">去采蘑菇</a></p></div>'
    )


def send_ntfy(rain, cfg, env, post=requests.post):
    topic = env.get("NTFY_TOPIC")
    if not topic:
        return "跳过（没配 NTFY_TOPIC）"
    server = (env.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    page = page_link(cfg, env)
    ms = rain["mushrooms"]
    if page:
        message, click = preview(rain), page
    else:  # 没有网页时，把标题也放进通知里
        message = preview(rain) + "\n" + "\n".join(f"{m['species']}｜{m['title']}" for m in ms)
        click = ms[0]["url"]
    if page:  # 只给一个入口：去网页上采
        actions = [{"action": "view", "label": "去采蘑菇", "url": page, "clear": True}]
    else:
        actions = [{"action": "view", "label": m["species"], "url": m["url"]} for m in ms[:3]]
    body = {"topic": topic, "title": "蘑菇·雨后", "message": message, "tags": ["mushroom"], "click": click, "actions": actions}
    headers = {"Authorization": f"Bearer {env['NTFY_TOKEN']}"} if env.get("NTFY_TOKEN") else {}
    resp = post(server, json=body, headers=headers, timeout=30)
    return f"ntfy {resp.status_code}"


def send_serverchan(rain, cfg, env, post=requests.post):
    key = env.get("SERVERCHAN_KEY")
    if not key:
        return "跳过（没配 SERVERCHAN_KEY）"
    n = NUM.get(len(rain["mushrooms"]), len(rain["mushrooms"]))
    page = page_link(cfg, env)
    # 有网页时只预告，不剧透：蘑菇要到网页上亲手采。没网页才把整篮内容写进消息。
    desp = f"{preview(rain)}\n\n[去采蘑菇]({page})" if page else basket_markdown(rain)
    data = {"title": f"蘑菇·雨后｜冒了{n}朵"[:32], "desp": desp, "short": preview(rain)[:64]}
    resp = post(f"https://sctapi.ftqq.com/{key}.send", data=data, timeout=30)
    return f"Server酱 {resp.status_code}"


def send_email(rain, cfg, env, smtp_cls=smtplib.SMTP_SSL):
    need = ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_TO")
    if not all(env.get(k) for k in need):
        return "跳过（邮件的 SMTP_HOST / SMTP_USER / SMTP_PASS / MAIL_TO 没配齐）"
    msg = EmailMessage()
    msg["Subject"] = f"蘑菇·雨后｜{preview(rain)}"
    msg["From"] = env["SMTP_USER"]
    msg["To"] = env["MAIL_TO"]
    page = page_link(cfg, env)
    if page:  # 只预告，去网页上采
        msg.set_content(f"{preview(rain)}\n\n去采蘑菇：{page}")
        msg.add_alternative(teaser_html(rain, page), subtype="html")
    else:
        msg.set_content(preview(rain) + "\n\n" + "\n\n".join(f"{m['species']}｜{m['title']}\n{m['what']}\n{m['url']}" for m in rain["mushrooms"]))
        msg.add_alternative(basket_html(rain), subtype="html")
    with smtp_cls(env["SMTP_HOST"], int(env.get("SMTP_PORT") or 465), timeout=30) as s:
        s.login(env["SMTP_USER"], env["SMTP_PASS"])
        s.send_message(msg)
    return "邮件 已发"


def send_all(rain, cfg, env, post=requests.post, smtp_cls=smtplib.SMTP_SSL):
    results = {}
    for name, fn in (("ntfy", lambda: send_ntfy(rain, cfg, env, post)),
                     ("微信", lambda: send_serverchan(rain, cfg, env, post)),
                     ("邮件", lambda: send_email(rain, cfg, env, smtp_cls))):
        try:
            results[name] = fn()
        except Exception as exc:  # 一个渠道坏了不影响别的
            results[name] = f"失败：{exc}"
    return results
