"""读写仓库里的数据文件。所有状态都放在 docs/data/ 下，网页直接读。"""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
RAINS = DATA / "rains.json"
FEEDBACK = DATA / "feedback.json"
STATUS = DATA / "status.json"
CONFIG = ROOT / "config.yaml"
FEED = ROOT / "feed.yaml"


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_rains():
    return _read_json(RAINS, [])


def save_rains(rains):
    _write_json(RAINS, rains)


def load_feedback():
    return _read_json(FEEDBACK, [])


def save_feedback(items):
    _write_json(FEEDBACK, items)


def save_status(status):
    _write_json(STATUS, status)


def load_config():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    cfg.setdefault("timezone", "Australia/Sydney")
    cfg.setdefault("awake", [9, 23])
    cfg.setdefault("rains_per_week", 6)
    cfg.setdefault("min_gap_hours", 4)
    cfg.setdefault("max_per_day", 2)
    cfg.setdefault("drought_hours", 60)
    cfg.setdefault("lost_every", 3)
    cfg.setdefault("source", "pool")
    cfg.setdefault("model", "claude-sonnet-5-5")
    cfg.setdefault("max_searches", 6)
    cfg.setdefault("page_url", "")
    return cfg


def load_feed():
    feed = yaml.safe_load(FEED.read_text(encoding="utf-8")) or {}
    return {"feed": list(feed.get("feed") or []), "avoid": list(feed.get("avoid") or [])}


def append_feed(line):
    """把「喂 …」的内容追加到 feed.yaml 的 feed 列表末尾，保留文件里的注释。"""
    text = " ".join(line.split())
    if not text:
        return False
    entry = "  - " + json.dumps(text, ensure_ascii=False)  # 双引号字符串也是合法 YAML
    lines = FEED.read_text(encoding="utf-8").splitlines()
    try:
        i = next(k for k, l in enumerate(lines) if l.rstrip() == "feed:")
    except StopIteration:
        lines += ["", "feed:", entry]
    else:
        j = i + 1
        while j < len(lines) and lines[j].startswith("  - "):
            j += 1
        lines.insert(j, entry)
    FEED.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True
