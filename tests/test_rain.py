import json
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rain import feedback as fb
from rain import forage as fg
from rain import main as mainmod
from rain import notify, store, weather

TZ = ZoneInfo("Australia/Sydney")
CFG = {"timezone": "Australia/Sydney", "awake": [9, 23], "rains_per_week": 6, "min_gap_hours": 4,
       "max_per_day": 2, "drought_hours": 60, "lost_every": 3, "model": "claude-sonnet-5-5",
       "max_searches": 6, "page_url": ""}


class Fixed:
    def __init__(self, v):
        self.v = v

    def random(self):
        return self.v


def at(h, d=26, m=0):
    return datetime(2026, 11, d, h, m, tzinfo=TZ)


# ---------- 天气 ----------

def test_no_rain_while_asleep():
    assert weather.should_rain(at(3), [], CFG, Fixed(0.0))[0] is False
    assert weather.should_rain(at(23), [], CFG, Fixed(0.0))[0] is False


def test_gap_and_daily_cap():
    rains = [{"at": at(10).isoformat(), "mushrooms": []}]
    assert weather.should_rain(at(12), rains, CFG, Fixed(0.0)) == (False, "刚下过")
    rains.append({"at": at(15).isoformat(), "mushrooms": []})
    assert weather.should_rain(at(20), rains, CFG, Fixed(0.0)) == (False, "今天下够了")


def test_probability_and_drought():
    recent = at(10) - timedelta(hours=10)
    p = weather.rain_probability(CFG, recent, at(10))
    assert abs(p - 6 / (7 * 14)) < 1e-9
    assert weather.rain_probability(CFG, None, at(10)) == pytest.approx(p * 3)
    assert weather.should_rain(at(10), [], CFG, Fixed(0.01))[0] is True
    assert weather.should_rain(at(10), [{"at": recent.isoformat()}], CFG, Fixed(0.99))[0] is False


def test_weekly_rate_is_about_right():
    rng, rains = random.Random(7), []
    t = at(0, d=1).replace(month=1)
    for _ in range(24 * 7 * 20):  # 20 周，每小时看一次
        ok, _ = weather.should_rain(t, rains, CFG, rng)
        if ok:
            rains.append({"at": t.isoformat(), "mushrooms": []})
        t += timedelta(hours=1)
    per_week = len(rains) / 20
    assert 4 <= per_week <= 8, per_week


def test_lost_and_matsutake():
    plain = {"at": at(10).isoformat(), "mushrooms": [{"species": "鸡枞", "lost": False}]}
    assert weather.needs_lost([plain] * 3, CFG, Fixed(0.99)) is True
    assert weather.needs_lost([plain], CFG, Fixed(0.99)) is False
    m = {"at": at(10).isoformat(), "mushrooms": [{"species": "松茸"}]}
    assert weather.matsutake_allowed([m], at(12)) is False
    assert weather.matsutake_allowed([m], at(12) + timedelta(days=8)) is True


# ---------- 采蘑菇 ----------

def api_blocks(items):
    return [
        {"type": "server_tool_use", "id": "srv1", "name": "web_search", "input": {"query": "莫言 新书"}},
        {"type": "web_search_tool_result", "tool_use_id": "srv1", "content": [
            {"type": "web_search_result", "url": "https://www.example.com/a/", "title": "A", "encrypted_content": "x"},
            {"type": "web_search_result", "url": "https://news.example.org/b", "title": "B", "encrypted_content": "x"},
            {"type": "web_search_result", "url": "https://old.example.net/c", "title": "C", "encrypted_content": "x"},
        ]},
        {"type": "text", "text": "找到了。\n```json\n" + json.dumps({"mushrooms": items}, ensure_ascii=False) + "\n```"},
    ]


ITEMS = [
    {"species": "猴头菇", "title": "长文 A", "what": "一篇长文", "why": "对应文学", "url": "https://example.com/a",
     "source": "例子", "edible": "能吃", "feed": "文学"},
    {"species": "鸡枞", "title": "编出来的", "what": "x", "why": "x", "url": "https://made.up/x", "source": "x", "edible": "能吃"},
    {"species": "野蘑菇", "title": "野的 B", "what": "外面的", "why": "值得看", "url": "https://news.example.org/b",
     "source": "例子", "edible": "随便", "feed": "野"},
    {"species": "松茸", "title": "带过的 C", "what": "x", "why": "x", "url": "https://old.example.net/c", "source": "x", "edible": "能吃"},
]


def test_validate_keeps_only_seen_and_new():
    blocks = api_blocks(ITEMS)
    history = {fg.norm_url("https://old.example.net/c")}
    kept, dropped = fg.validate(fg.extract_json(blocks), fg.seen_urls(blocks), history, matsutake_ok=True)
    assert [m["title"] for m in kept] == ["长文 A", "野的 B"]
    assert kept[1]["lost"] is True and kept[1]["edible"] == "先闻闻"  # 非法标签降级
    assert any("不在这次的搜索结果里" in d for d in dropped)
    assert any("带过了" in d for d in dropped)


def test_matsutake_downgraded_when_used_this_week():
    item = dict(ITEMS[0], species="松茸")
    blocks = api_blocks([item])
    kept, _ = fg.validate(fg.extract_json(blocks), fg.seen_urls(blocks), set(), matsutake_ok=False)
    assert kept[0]["species"] == "猴头菇"


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_call_claude_handles_pause_turn():
    calls = []

    def post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return Resp({"content": api_blocks([])[:2], "stop_reason": "pause_turn",
                         "usage": {"input_tokens": 1000, "output_tokens": 50, "server_tool_use": {"web_search_requests": 1}}})
        return Resp({"content": api_blocks([ITEMS[0]])[2:], "stop_reason": "end_turn",
                     "usage": {"input_tokens": 2000, "output_tokens": 300}})

    blocks, usage = fg.call_claude("去吧", CFG, "k", post=post)
    assert len(calls) == 2 and calls[1]["messages"][1]["role"] == "assistant"
    assert calls[0]["tools"][0]["type"] == "web_search_20250305"
    assert usage == {"input_tokens": 3000, "output_tokens": 350, "searches": 1}
    assert fg.estimate_cost(usage, "claude-sonnet-5-5") == pytest.approx(0.006 + 0.0035 + 0.01)
    kept, _ = fg.validate(fg.extract_json(blocks), fg.seen_urls(blocks), set(), True)
    assert len(kept) == 1


def test_prompt_mentions_feed_feedback_and_history():
    feed = {"feed": ["文学：莫言"], "avoid": ["软文"]}
    fbk = [{"verdict": "别再带", "species": "鸡枞", "title": "某新闻", "feed": "音乐"}]
    rains = [{"at": at(9).isoformat(), "mushrooms": [{"title": "旧的", "url": "https://x.com/1"}]}]
    p = fg.build_prompt(feed, fbk, rains, at(14), need_lost=True, matsutake_ok=False)
    for s in ("文学：莫言", "软文", "别再带", "https://x.com/1", "野蘑菇", "不要用松茸"):
        assert s in p


# ---------- 送达 ----------

RAIN = {"id": "20261126-1431", "at": at(14, m=31).isoformat(), "mushrooms": [
    {"id": "20261126-1431-1", "species": "猴头菇", "title": "长文 A", "what": "一篇长文", "why": "对应文学",
     "url": "https://example.com/a", "source": "例子", "edible": "能吃", "feed": "文学", "lost": False,
     "links": fb.mushroom_links("me/mogu-yuhou", "20261126-1431-1")},
    {"id": "20261126-1431-2", "species": "野蘑菇", "title": "野的 B", "what": "外面的", "why": "值得看",
     "url": "https://news.example.org/b", "source": "例子", "edible": "先闻闻", "feed": "野", "lost": True},
]}


def test_preview_does_not_spoil():
    assert notify.preview(RAIN) == "雨停了，冒了两朵：一朵猴头菇，一朵野蘑菇"


def test_send_all_channels():
    sent = []

    def post(url, json=None, data=None, headers=None, timeout=None):
        sent.append((url, json, data))
        return Resp({}, 200)

    class SMTP:
        def __init__(self, host, port, timeout):
            sent.append(("smtp", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            pass

        def send_message(self, msg):
            sent.append(("mail", msg["Subject"], msg.get_body(("html",)).get_content()))

    env = {"NTFY_TOPIC": "secret-topic", "SERVERCHAN_KEY": "SCT123", "SMTP_HOST": "smtp.qq.com",
           "SMTP_USER": "a@qq.com", "SMTP_PASS": "p", "MAIL_TO": "a@qq.com"}
    cfg = dict(CFG, page_url="https://me.github.io/mogu-yuhou/")
    res = notify.send_all(RAIN, cfg, env, post=post, smtp_cls=SMTP)
    assert res == {"ntfy": "ntfy 200", "微信": "Server酱 200", "邮件": "邮件 已发"}
    ntfy = sent[0][1]
    assert ntfy["topic"] == "secret-topic" and ntfy["message"] == notify.preview(RAIN)
    assert ntfy["click"] == cfg["page_url"] and len(ntfy["actions"]) <= 3
    sc = sent[1]
    assert sc[0] == "https://sctapi.ftqq.com/SCT123.send" and len(sc[2]["title"]) <= 32
    assert "[好吃](https://github.com/me/mogu-yuhou/issues/new?title=" in sc[2]["desp"]
    assert sent[3][0] == "mail" and "&lt;" not in sent[3][1] and "长文 A" in sent[3][2]


def test_missing_channels_are_skipped():
    res = notify.send_all(RAIN, CFG, {}, post=None)
    assert all(v.startswith("跳过") for v in res.values())


def test_ntfy_without_page_lists_titles():
    sent = []
    notify.send_ntfy(RAIN, CFG, {"NTFY_TOPIC": "t"}, post=lambda u, **k: sent.append(k["json"]) or Resp({}, 200))
    assert "长文 A" in sent[0]["message"] and sent[0]["click"] == "https://example.com/a"


# ---------- 喂养和反馈 ----------

@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    feed = tmp_path / "feed.yaml"
    feed.write_text("# 注释\nfeed:\n  - 文学\n\navoid:\n  - 软文\n", encoding="utf-8")
    monkeypatch.setattr(store, "FEED", feed)
    monkeypatch.setattr(store, "RAINS", tmp_path / "rains.json")
    monkeypatch.setattr(store, "FEEDBACK", tmp_path / "feedback.json")
    return tmp_path


def test_append_feed_keeps_comments_and_quotes(tmp_store):
    assert store.append_feed("想看: 蘑菇的科普")
    text = store.FEED.read_text(encoding="utf-8")
    assert text.startswith("# 注释")
    assert store.load_feed() == {"feed": ["文学", "想看: 蘑菇的科普"], "avoid": ["软文"]}


def test_process_issues(tmp_store):
    calls = []
    issues = [
        {"number": 1, "title": "喂 想看庄子的新研究", "body": "", "user": {"login": "Me"}},
        {"number": 2, "title": "别再带 20261126-1431-1", "body": "", "user": {"login": "me"}},
        {"number": 3, "title": "好吃 20261126-1431-1", "body": "", "user": {"login": "stranger"}},
        {"number": 4, "title": "随便聊聊", "body": "", "user": {"login": "me"}},
        {"number": 5, "title": "好吃 nope", "body": "", "user": {"login": "me"}},
    ]

    def request(method, url, token, **kw):
        calls.append((method, url, kw.get("json")))
        return Resp(issues if method == "GET" else {}, 200)

    feedback = []
    fb_changed, feed_changed, log = fb.process("me/mogu-yuhou", "tok", [RAIN], feedback, at(15), request=request)
    assert fb_changed and feed_changed and len(log) == 3
    assert "想看庄子的新研究" in store.load_feed()["feed"]
    assert feedback == [dict(feedback[0], verdict="别再带", id="20261126-1431-1", title="长文 A")]
    closed = [u for m, u, j in calls if m == "PATCH"]
    assert closed == [f"https://api.github.com/repos/me/mogu-yuhou/issues/{n}" for n in (1, 2, 5)]


# ---------- 整条链 ----------

def test_main_end_to_end(tmp_store, monkeypatch, capsys):
    monkeypatch.setattr(store, "load_config", lambda: dict(CFG))
    monkeypatch.setattr(mainmod.weather, "should_rain", lambda *a: (True, "下雨"))
    monkeypatch.setattr(fg, "call_claude", lambda prompt, cfg, key, post=None: (api_blocks(ITEMS), {"input_tokens": 30000, "output_tokens": 800, "searches": 4}))
    monkeypatch.setattr(mainmod, "forage", fg.forage)
    sent = []
    monkeypatch.setattr(notify, "send_all", lambda rain, cfg, env: sent.append(rain) or {"ntfy": "ok"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/mogu-yuhou")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    mainmod.main([])
    rains = store.load_rains()
    assert len(rains) == 1 and len(rains[0]["mushrooms"]) == 3
    m = rains[0]["mushrooms"][0]
    assert m["id"].endswith("-1") and "issues/new" in m["links"]["好吃"]
    assert rains[0]["cost_usd"] == pytest.approx(0.06 + 0.008 + 0.04)
    assert sent and "雨停了，冒了三朵" in capsys.readouterr().out


def test_main_test_notify_spends_nothing(tmp_store, monkeypatch):
    monkeypatch.setattr(store, "load_config", lambda: dict(CFG))
    sent = []
    monkeypatch.setattr(notify, "send_all", lambda rain, cfg, env: sent.append(rain) or {})
    monkeypatch.setattr(fg, "call_claude", lambda *a, **k: pytest.fail("不该调用 API"))
    mainmod.main(["--test-notify"])
    assert sent[0]["mushrooms"][0]["title"] == "这是一朵测试蘑菇"
    assert store.load_rains() == []
