"""
attendance.py — Everything related to reading and writing attendance records.

Responsibilities:
  - Append a new attendance record (once per person per day)
  - Check if already marked today
  - Query records by date or date range
  - Return today's present names

Cross-file rule: Only this module touches attendance_log.csv.
"""

import csv
import threading
from datetime import datetime, date
from typing import Optional

import pandas as pd

from config import LOG_PATH

# File-level lock so concurrent API requests don't corrupt the CSV.
_csv_lock = threading.Lock()


# ── Setup ─────────────────────────────────────────────────────────────────────

def ensure_log_exists() -> None:
    """Create the CSV file with headers if it does not exist yet."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        with open(LOG_PATH, "w", newline="") as fh:
            csv.writer(fh).writerow(["name", "date", "time", "confidence", "status"])


# ── Queries ───────────────────────────────────────────────────────────────────

def is_marked_today(name: str) -> bool:
    """Return True if `name` already has an entry for today's date."""
    today = date.today().isoformat()
    return _name_exists_on_date(name, today)


def _name_exists_on_date(name: str, iso_date: str) -> bool:
    if not LOG_PATH.exists():
        return False
    df = _read_csv()
    return not df[(df["name"] == name) & (df["date"] == iso_date)].empty


def get_attendance(filter_date: Optional[str] = None) -> list[dict]:
    """
    Return attendance records as a list of dicts.
    filter_date: ISO date string "YYYY-MM-DD". If None, return all records.
    """
    if not LOG_PATH.exists():
        return []
    df = _read_csv()
    if filter_date:
        df = df[df["date"] == filter_date]
    return df.sort_values("time").to_dict(orient="records")


def get_today_names() -> list[str]:
    """Return a list of names marked present today."""
    today = date.today().isoformat()
    if not LOG_PATH.exists():
        return []
    df = _read_csv()
    return df[df["date"] == today]["name"].tolist()


# ── Write ─────────────────────────────────────────────────────────────────────

def mark_attendance(name: str, confidence: float) -> dict:
    """
    Mark a person as present for today — idempotent (no-op if already marked).

    Returns:
        {"success": bool, "already_marked": bool, "message": str}
    """
    ensure_log_exists()

    if is_marked_today(name):
        return {"success": True, "already_marked": True,  "message": f"{name} already marked today."}

    now = datetime.now()
    with _csv_lock:
        with open(LOG_PATH, "a", newline="") as fh:
            csv.writer(fh).writerow([
                name,
                now.date().isoformat(),
                now.strftime("%H:%M:%S"),
                round(confidence, 4),
                "present",
            ])

    return {"success": True, "already_marked": False, "message": f"Attendance marked for {name}."}


# ── Internal ──────────────────────────────────────────────────────────────────

def _read_csv() -> pd.DataFrame:
    """Read CSV into DataFrame; return empty frame on any error."""
    try:
        df = pd.read_csv(LOG_PATH)
        # Coerce column types to avoid type-mismatch on filter.
        df["name"] = df["name"].astype(str)
        df["date"] = df["date"].astype(str)
        return df
    except Exception:
        return pd.DataFrame(columns=["name", "date", "time", "confidence", "status"])
