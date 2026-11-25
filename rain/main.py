"""每小时跑一次：先处理喂养和反馈，再看要不要下雨；下了就去采，采到了就送。

用法：
  python -m rain.main                检查天气
  python -m rain.main --force        求雨：不管概率，现在就下一场（测试用）
  python -m rain.main --preview      预演：照常挑蘑菇，但不送、不存
  python -m rain.main --test-notify  只发一朵测试蘑菇，检查渠道通不通

蘑菇从哪来由 config.yaml 的 source 决定：
  pool   = 从 pool.yaml 蘑菇池里带（原型，不连 API，不花钱）
  claude = 让 Claude 联网现采（要 ANTHROPIC_API_KEY）
"""
import argparse
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from . import feedback as fb
from . import notify, pool, relay, store, weather
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


def flag(args, name, env):
    return getattr(args, name) or env.get(name.upper(), "").lower() == "true"


def gather(cfg, env, rains, feedback, now, rng):
    """去采蘑菇。返回 (蘑菇列表, 附加信息)。"""
    need_lost = weather.needs_lost(rains, cfg, rng)
    matsutake_ok = weather.matsutake_allowed(rains, now)
    if cfg["source"] == "claude":
        api_key = env.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("source 是 claude，但没配 ANTHROPIC_API_KEY，这场雨下不了。")
            return [], {}
        mushrooms, meta = forage(store.load_feed(), feedback, rains, now, cfg, api_key, need_lost, matsutake_ok)
        for d in meta["dropped"]:
            print(f"扔掉：{d}")
        return mushrooms, {"searches": meta["searches"], "cost_usd": meta["cost_usd"]}
    items = pool.load_pool()
    mushrooms = pool.pick(items, rains, feedback, need_lost, matsutake_ok, rng)
    left = len(pool.remaining(items, rains)) - len(mushrooms)
    return mushrooms, {"pool_left": left, "pool_total": len(items)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    for name in ("force", "preview", "test_notify"):
        ap.add_argument("--" + name.replace("_", "-"), dest=name, action="store_true")
    args = ap.parse_args(argv)
    env = os.environ
    force, preview, test_notify = (flag(args, n, env) for n in ("force", "preview", "test_notify"))

    cfg = store.load_config()
    now = datetime.now(ZoneInfo(cfg["timezone"])).replace(second=0, microsecond=0)
    repo, token = env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_TOKEN", "")
    rains, feedback = store.load_rains(), store.load_feedback()

    if repo and token and not preview:
        try:
            fb_changed, _, log = fb.process(repo, token, rains, feedback, now)
            for line in log:
                print(line)
            if fb_changed:
                store.save_feedback(feedback)
        except Exception as exc:
            print(f"处理 Issue 时出错（不影响下雨）：{exc}")

    if relay.topic(env) and not preview:
        try:
            state = store.load_relay()
            messages = relay.fetch(env, state.get("since"))
            rains_changed, fb_changed, _, log = relay.apply(messages, rains, feedback, state, now.tzinfo)
            for line in log:
                print(line)
            if rains_changed:
                store.save_rains(rains)
            if fb_changed:
                store.save_feedback(feedback)
            if messages:
                store.save_relay(state)
        except Exception as exc:
            print(f"读回信箱时出错（不影响下雨）：{exc}")

    if test_notify:
        print(notify.send_all(sample_rain(now, repo), cfg, env))
        return

    rng = random.SystemRandom()
    if force or preview:
        ok, reason = True, "预演" if preview else "求雨"
    else:
        ok, reason = weather.should_rain(now, rains, cfg, rng)
    print(f"{now:%m-%d %H:%M} {reason}")
    if not ok:
        return

    mushrooms, meta = gather(cfg, env, rains, feedback, now, rng)
    if not mushrooms:
        print("这场雨没冒出能带的蘑菇" + ("，池子空了，该补蘑菇了。" if cfg["source"] == "pool" else "，下次再说。"))
        return

    rain_id = f"{now:%Y%m%d-%H%M}"
    for i, m in enumerate(mushrooms, 1):
        m["id"] = f"{rain_id}-{i}"
        if repo:
            m["links"] = fb.mushroom_links(repo, m["id"])
    rain = {"id": rain_id, "at": now.isoformat(), "mushrooms": mushrooms, "source": cfg["source"], "forced": force}
    rain.update({k: v for k, v in meta.items() if k in ("searches", "cost_usd")})
    print(notify.preview(rain))
    for m in mushrooms:
        print(f"  {m['species']}｜{m['title']}｜{m['edible']}｜{m['url']}")
    if "pool_left" in meta:
        print(f"池子里还剩 {meta['pool_left']} / {meta['pool_total']} 朵")
    if preview:
        print("预演结束：没有送，也没有存。")
        return

    rains.append(rain)
    store.save_rains(rains)
    store.save_status({"source": cfg["source"], "last_rain": rain["at"],
                       **{k: v for k, v in meta.items() if k.startswith("pool_")}})
    print(notify.send_all(rain, cfg, env))


if __name__ == "__main__":
    main()
