"""Persistent token usage tracker with session, daily, and monthly counters.

Stores usage in a JSON file so it survives restarts.  The monthly counter
resets on the user's Anthropic billing cycle day (configurable, default 1st).
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class TokenTracker:
    def __init__(self, storage_path="token_usage.json", billing_cycle_day=1):
        self._path = Path(storage_path)
        self._billing_cycle_day = billing_cycle_day
        self._lock = threading.Lock()
        self._data = self._load()
        # Session counters reset each time the process starts
        self._data["session_input"] = 0
        self._data["session_output"] = 0
        self._data["session_requests"] = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_usage(self, input_tokens: int, output_tokens: int):
        """Record token usage from one API call."""
        now = datetime.now(timezone.utc)
        with self._lock:
            self._maybe_reset(now)
            self._data["session_input"] += input_tokens
            self._data["session_output"] += output_tokens
            self._data["session_requests"] += 1
            self._data["daily_input"] += input_tokens
            self._data["daily_output"] += output_tokens
            self._data["daily_requests"] += 1
            self._data["monthly_input"] += input_tokens
            self._data["monthly_output"] += output_tokens
            self._data["monthly_requests"] += 1
            self._data["all_time_input"] += input_tokens
            self._data["all_time_output"] += output_tokens
            self._data["all_time_requests"] += 1
            self._data["last_updated"] = now.isoformat()
            self._save()

    def get_usage(self) -> dict:
        """Return all counters for the frontend."""
        now = datetime.now(timezone.utc)
        with self._lock:
            self._maybe_reset(now)
            d = dict(self._data)
        return {
            "session": {
                "input_tokens": d["session_input"],
                "output_tokens": d["session_output"],
                "total_tokens": d["session_input"] + d["session_output"],
                "requests": d["session_requests"],
            },
            "daily": {
                "input_tokens": d["daily_input"],
                "output_tokens": d["daily_output"],
                "total_tokens": d["daily_input"] + d["daily_output"],
                "requests": d["daily_requests"],
                "date": d.get("daily_date", ""),
            },
            "monthly": {
                "input_tokens": d["monthly_input"],
                "output_tokens": d["monthly_output"],
                "total_tokens": d["monthly_input"] + d["monthly_output"],
                "requests": d["monthly_requests"],
                "period_start": d.get("monthly_start", ""),
            },
            "all_time": {
                "input_tokens": d["all_time_input"],
                "output_tokens": d["all_time_output"],
                "total_tokens": d["all_time_input"] + d["all_time_output"],
                "requests": d["all_time_requests"],
            },
            "billing_cycle_day": self._billing_cycle_day,
        }

    def reset_session(self):
        """Reset just the session counters (called on new browser session)."""
        with self._lock:
            self._data["session_input"] = 0
            self._data["session_output"] = 0
            self._data["session_requests"] = 0

    def set_billing_cycle_day(self, day: int):
        """Update the billing cycle reset day (1-28)."""
        self._billing_cycle_day = max(1, min(28, day))
        with self._lock:
            self._data["billing_cycle_day"] = self._billing_cycle_day
            self._save()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _maybe_reset(self, now: datetime):
        """Auto-reset daily and monthly counters if the period has changed."""
        today = now.strftime("%Y-%m-%d")

        # Daily reset
        if self._data.get("daily_date") != today:
            self._data["daily_input"] = 0
            self._data["daily_output"] = 0
            self._data["daily_requests"] = 0
            self._data["daily_date"] = today

        # Monthly reset on billing cycle day
        current_period = self._billing_period_start(now)
        if self._data.get("monthly_start") != current_period:
            self._data["monthly_input"] = 0
            self._data["monthly_output"] = 0
            self._data["monthly_requests"] = 0
            self._data["monthly_start"] = current_period

    def _billing_period_start(self, now: datetime) -> str:
        """Compute the start date of the current billing period."""
        day = self._billing_cycle_day
        if now.day >= day:
            # Current month
            return now.strftime(f"%Y-%m-{day:02d}")
        else:
            # Previous month
            if now.month == 1:
                return f"{now.year - 1}-12-{day:02d}"
            else:
                return now.strftime(f"%Y-{now.month - 1:02d}-{day:02d}")

    def _load(self) -> dict:
        defaults = self._defaults()
        if not self._path.exists():
            return defaults
        try:
            data = json.loads(self._path.read_text())
            # Restore billing cycle day from persisted data
            if "billing_cycle_day" in data:
                self._billing_cycle_day = data["billing_cycle_day"]
            # Merge with defaults to pick up any new fields
            for k, v in defaults.items():
                data.setdefault(k, v)
            return data
        except (json.JSONDecodeError, OSError):
            return defaults

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2) + "\n")
        except OSError:
            pass  # Non-fatal — we still have in-memory data

    def _defaults(self) -> dict:
        return {
            "session_input": 0,
            "session_output": 0,
            "session_requests": 0,
            "daily_input": 0,
            "daily_output": 0,
            "daily_requests": 0,
            "daily_date": "",
            "monthly_input": 0,
            "monthly_output": 0,
            "monthly_requests": 0,
            "monthly_start": "",
            "billing_cycle_day": self._billing_cycle_day,
            "all_time_input": 0,
            "all_time_output": 0,
            "all_time_requests": 0,
            "last_updated": "",
        }
