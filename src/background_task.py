"""Run a long analysis in a background thread so switching tabs doesn't cancel it.

Streamlit reruns the whole script synchronously on every interaction — normally,
switching tabs while a run is in progress aborts it. A plain module-level dict
here survives across reruns (the Python process itself doesn't restart), so a
background thread can keep working while the visible tab changes; whichever tab
is showing just reads the latest progress/result out of this dict.
"""
from __future__ import annotations

import threading

_TASKS: dict[str, dict] = {}


def start_task(key: str, job) -> bool:
    """Start `job(progress_callback) -> result` in a background thread.

    Returns False without starting anything if a task under this key is
    already running.
    """
    existing = _TASKS.get(key)
    if existing and existing["status"] == "running":
        return False

    state = {"status": "running", "progress": (0, 1, "Starting..."), "result": None, "error": None}
    _TASKS[key] = state

    def _progress(i, total, label):
        state["progress"] = (i, total, label)

    def _runner():
        try:
            state["result"] = job(_progress)
            state["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            state["error"] = str(exc)
            state["status"] = "error"

    threading.Thread(target=_runner, daemon=True).start()
    return True


def get_task(key: str) -> dict | None:
    return _TASKS.get(key)


def clear_task(key: str) -> None:
    _TASKS.pop(key, None)
