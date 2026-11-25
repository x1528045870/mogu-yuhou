"""要不要下雨。每小时检查一次，按概率决定，守住醒着的时段、间隔和每日上限。"""
from datetime import datetime, timedelta


def last_rain_time(rains):
    if not rains:
        return None
    return datetime.fromisoformat(rains[-1]["at"])


def rains_today(rains, now):
    return sum(1 for r in rains if datetime.fromisoformat(r["at"]).astimezone(now.tzinfo).date() == now.date())


def rain_probability(cfg, last, now):
    start, end = cfg["awake"]
    awake_hours_per_week = 7 * max(end - start, 1)
    p = cfg["rains_per_week"] / awake_hours_per_week
    if last is None or now - last > timedelta(hours=cfg["drought_hours"]):
        p *= 3
    return min(p, 0.9)


def should_rain(now, rains, cfg, rng):
    """返回 (是否下雨, 原因)。now 必须带时区，且已换算到用户时区。"""
    start, end = cfg["awake"]
    if not (start <= now.hour < end):
        return False, "睡觉时间"
    last = last_rain_time(rains)
    if last and now - last < timedelta(hours=cfg["min_gap_hours"]):
        return False, "刚下过"
    if rains_today(rains, now) >= cfg["max_per_day"]:
        return False, "今天下够了"
    p = rain_probability(cfg, last, now)
    roll = rng.random()
    if roll < p:
        return True, f"下雨（{roll:.3f} < {p:.3f}）"
    return False, f"没下（{roll:.3f} ≥ {p:.3f}）"


def needs_lost(rains, cfg, rng):
    """这场雨要不要带一朵野蘑菇：大约每 lost_every 场一朵，太久没带就一定带。"""
    since = 0
    for r in reversed(rains):
        if any(m.get("lost") for m in r.get("mushrooms", [])):
            break
        since += 1
    if since >= cfg["lost_every"]:
        return True
    return rng.random() < 1 / max(cfg["lost_every"], 1)


def matsutake_allowed(rains, now):
    """一周最多一朵松茸。"""
    week_ago = now - timedelta(days=7)
    for r in rains:
        if datetime.fromisoformat(r["at"]) >= week_ago and any(m["species"] == "松茸" for m in r.get("mushrooms", [])):
            return False
    return True
