"""Persist run results + timestamps under data/ so the app has run history (P1)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def save_run(tab_key: str, df: pd.DataFrame) -> str:
    DATA_DIR.mkdir(exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    csv_path = DATA_DIR / f"{tab_key}_{timestamp}.csv"
    df.to_csv(csv_path, index=False)

    meta_path = DATA_DIR / f"{tab_key}_latest.json"
    meta_path.write_text(json.dumps({"timestamp": timestamp, "csv": csv_path.name}, ensure_ascii=False))
    return timestamp


def load_latest_timestamp(tab_key: str) -> str | None:
    meta_path = DATA_DIR / f"{tab_key}_latest.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text()).get("timestamp")


def list_runs(tab_key: str) -> list[str]:
    if not DATA_DIR.exists():
        return []
    return sorted(p.stem for p in DATA_DIR.glob(f"{tab_key}_*.csv"))


def load_run(tab_key: str, run_name: str) -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / f"{run_name}.csv")
