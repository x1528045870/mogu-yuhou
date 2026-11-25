"""每小时跑一次：先处理喂养和反馈，再看要不要下雨；下了就去采，采到了就送。

用法：
  python -m rain.main              正常检查天气
  python -m rain.main --force      求雨：不管概率，现在就下一场（测试用）
  python -m rain.main --test-notify  只发一朵测试蘑菇，检查三个渠道通不通（不花 API 的钱）
"""
import argparse
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from . import feedback as fb
from . import notify, store, weather
from .forage import forage


def sample_rain(now, repo):
    url = f"https://github.com/{repo}" if repo else "https://docs.ntfy.sh/"
    m = {
        "id": f"{now:%Y%m%d-%H%M}-test", "species": "鸡枞", "title": "这是一朵测试蘑菇",
        "what": "看到这条，说明这个渠道通了。", "why": "测试推送，不算一场雨，也不会进图鉴。",
        "url": url, "source": "蘑菇·雨后", "edible": "能吃", "feed": "测试", "lost": False,
    }
    if repo:
        m["links"] = fb.mushroom_links(repo, m["id"])
    return {"id": m["id"], "at": now.isoformat(), "mushrooms": [m]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--test-notify", action="store_true")
    args = ap.parse_args(argv)
    env = os.environ
    force = args.force or env.get("FORCE", "").lower() == "true"
    test_notify = args.test_notify or env.get("TEST_NOTIFY", "").lower() == "true"

    cfg = store.load_config()
    now = datetime.now(ZoneInfo(cfg["timezone"])).replace(second=0, microsecond=0)
    repo, token = env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_TOKEN", "")
    rains, feedback = store.load_rains(), store.load_feedback()

    if repo and token:
        try:
            fb_changed, _, log = fb.process(repo, token, rains, feedback, now)
            for line in log:
                print(line)
            if fb_changed:
                store.save_feedback(feedback)
        except Exception as exc:
            print(f"处理 Issue 时出错（不影响下雨）：{exc}")

    if test_notify:
        print(notify.send_all(sample_rain(now, repo), cfg, env))
        return

    rng = random.SystemRandom()
    ok, reason = (True, "求雨") if force else weather.should_rain(now, rains, cfg, rng)
    print(f"{now:%m-%d %H:%M} {reason}")
    if not ok:
        return
    api_key = env.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("没配 ANTHROPIC_API_KEY，这场雨下不了。")
        return

    need_lost = weather.needs_lost(rains, cfg, rng)
    matsutake_ok = weather.matsutake_allowed(rains, now)
    mushrooms, meta = forage(store.load_feed(), feedback, rains, now, cfg, api_key, need_lost, matsutake_ok)
    for d in meta["dropped"]:
        print(f"扔掉：{d}")
    if not mushrooms:
        print("这场雨没冒出能带的蘑菇，下次再说。")
        return

    rain_id = f"{now:%Y%m%d-%H%M}"
    for i, m in enumerate(mushrooms, 1):
        m["id"] = f"{rain_id}-{i}"
        if repo:
            m["links"] = fb.mushroom_links(repo, m["id"])
    rain = {"id": rain_id, "at": now.isoformat(), "mushrooms": mushrooms,
            "searches": meta["searches"], "cost_usd": meta["cost_usd"], "forced": force}
    rains.append(rain)
    store.save_rains(rains)
    print(notify.preview(rain), f"（搜了 {meta['searches']} 次，约 ${meta['cost_usd']}）")
    print(notify.send_all(rain, cfg, env))


if __name__ == "__main__":
    main()
