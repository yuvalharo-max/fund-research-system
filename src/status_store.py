"""Per-item read/priority status (read, starred, archived), persisted locally.

Keyed by a stable identifier (ticker, or ticker+fund) rather than row position,
so marks survive across re-runs even as the underlying data is refreshed.
"""
from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

PRIORITY_STARRED = "starred"
PRIORITY_ARCHIVED = "archived"


def _path(tab_key: str) -> Path:
    return DATA_DIR / f"{tab_key}_status.json"


def load_status(tab_key: str) -> dict:
    path = _path(tab_key)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_status(tab_key: str, status: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    _path(tab_key).write_text(json.dumps(status, ensure_ascii=False, indent=2))


def get_item(status: dict, key: str) -> dict:
    return status.get(key, {"read": False, "priority": None})


def set_item(tab_key: str, status: dict, key: str, **updates) -> None:
    item = dict(get_item(status, key))
    item.update(updates)
    status[key] = item
    save_status(tab_key, status)


def row_key(company: str, fund: str | None = None) -> str:
    ticker = str(company).split(" - ", 1)[0].strip()
    return f"{ticker}|{fund}" if fund else ticker
