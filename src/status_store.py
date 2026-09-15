"""Per-item triage status (unread/read/starred/archived), persisted locally.

Keyed by a stable identifier (ticker, or ticker+fund) rather than row position,
so marks survive across re-runs even as the underlying data is refreshed.
"""
from __future__ import annotations

RESTART_TEST_MARKER = "restart-test-1"

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

STATUS_UNREAD = "unread"
STATUS_READ = "read"
STATUS_STARRED = "starred"
STATUS_ARCHIVED = "archived"

STATUS_LABELS = {
    STATUS_UNREAD: "New",
    STATUS_READ: "✓ Read",
    STATUS_STARRED: "⭐ Follow up later",
    STATUS_ARCHIVED: "🗑 Not relevant",
}
STATUS_OPTIONS = [STATUS_UNREAD, STATUS_READ, STATUS_STARRED, STATUS_ARCHIVED]


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


def get_status(status: dict, key: str) -> str:
    return status.get(key, {}).get("status", STATUS_UNREAD)


def set_status(tab_key: str, status: dict, key: str, new_status: str) -> None:
    status[key] = {"status": new_status}
    save_status(tab_key, status)


def row_key(company: str, fund: str | None = None) -> str:
    ticker = str(company).split(" - ", 1)[0].strip()
    return f"{ticker}|{fund}" if fund else ticker
