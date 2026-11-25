"""原型阶段不连 API：雨从蘑菇池（pool.yaml）里带蘑菇。

池子里每朵都是事先采好、链接核实过的。每场雨按规则挑 1–3 朵：
- 带过的不再带；
- 该带野蘑菇时带一朵；一周最多一朵松茸；
- 同一场尽量不重复品种；
- 按反馈加权：好吃 / 还想要 的那一类更容易冒，难吃 / 别再带 的那一类更难冒。
"""
from collections import defaultdict

import yaml

from . import EDIBLE, SPECIES
from .forage import norm_url
from .store import ROOT

POOL = ROOT / "pool.yaml"
DELTA = {"好吃": 0.5, "还想要": 1.0, "难吃": -0.5, "别再带": -2.0}


def load_pool(path=None):
    items = yaml.safe_load((path or POOL).read_text(encoding="utf-8")) or []
    out = []
    for p in items:
        if not isinstance(p, dict) or not str(p.get("url", "")).startswith("https://"):
            continue
        species = p.get("species") if p.get("species") in SPECIES else "鸡枞"
        lost = species == "野蘑菇" or p.get("feed") == "野"
        out.append({
            "species": "野蘑菇" if lost else species,
            "title": str(p.get("title", "")).strip(),
            "what": str(p.get("what", "")).strip(),
            "why": str(p.get("why", "")).strip(),
            "url": str(p["url"]).strip(),
            "source": str(p.get("source", "")).strip(),
            "edible": p.get("edible") if p.get("edible") in EDIBLE else "先闻闻",
            "feed": "野" if lost else str(p.get("feed", "")).strip(),
            "lost": lost,
        })
    return out


def remaining(pool, rains):
    taken = {norm_url(m["url"]) for r in rains for m in r.get("mushrooms", [])}
    return [p for p in pool if norm_url(p["url"]) not in taken]


def feed_weights(feedback):
    score = defaultdict(float)
    for f in feedback:
        score[f.get("feed", "")] += DELTA.get(f.get("verdict"), 0)
    return lambda item: max(0.1, 1.0 + score[item["feed"]])


def weighted_choice(items, weight, rng):
    total = sum(weight(i) for i in items)
    r = rng.random() * total
    for i in items:
        r -= weight(i)
        if r <= 0:
            return i
    return items[-1]


def pick(pool, rains, feedback, need_lost, matsutake_ok, rng):
    left = remaining(pool, rains)
    if not matsutake_ok:
        left = [p for p in left if p["species"] != "松茸"]
    weight = feed_weights(feedback)
    n = rng.choice([1, 2, 2, 3])
    wild = [p for p in left if p["lost"]]
    others = [p for p in left if not p["lost"]]
    picks = []
    if need_lost and wild:
        picks.append(weighted_choice(wild, weight, rng))
    while len(picks) < n and others:
        used = {p["species"] for p in picks}
        fresh = [o for o in others if o["species"] not in used] or others
        choice = weighted_choice(fresh, weight, rng)
        picks.append(choice)
        others.remove(choice)
    if not picks and wild:  # 只剩野蘑菇了
        picks.append(weighted_choice(wild, weight, rng))
    return [dict(p) for p in picks]
