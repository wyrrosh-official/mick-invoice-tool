"""Tracks estimated Claude API spend per month and enforces a cap."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

USAGE_FILE = Path(__file__).parent / "data" / "usage.json"

# claude-sonnet-5-5 pricing, per 1M tokens
INPUT_PRICE_PER_M = 2.00
OUTPUT_PRICE_PER_M = 10.00


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load() -> dict:
    if not USAGE_FILE.exists():
        return {}
    with open(USAGE_FILE, "r") as f:
        return json.load(f)


def _save(data: dict) -> None:
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_monthly_cost() -> float:
    data = _load()
    month = data.get(_month_key(), {})
    return month.get("estimated_cost_usd", 0.0)


def get_monthly_stats() -> dict:
    data = _load()
    return data.get(_month_key(), {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0})


def get_monthly_cap() -> float:
    return float(os.environ.get("MONTHLY_COST_CAP_USD", "15.00"))


def is_over_cap() -> bool:
    return get_monthly_cost() >= get_monthly_cap()


def record_usage(input_tokens: int, output_tokens: int) -> float:
    """Records a Claude API call's token usage and returns its cost in USD."""
    cost = (input_tokens / 1_000_000) * INPUT_PRICE_PER_M + (
        output_tokens / 1_000_000
    ) * OUTPUT_PRICE_PER_M

    data = _load()
    key = _month_key()
    month = data.get(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0})
    month["calls"] += 1
    month["input_tokens"] += input_tokens
    month["output_tokens"] += output_tokens
    month["estimated_cost_usd"] += cost
    data[key] = month
    _save(data)

    return cost
