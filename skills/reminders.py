"""
DODO — skills/reminders.py
Advanced, reliable, efficient reminder and timer management.
Features:
- Natural language time parsing (relative, clock time, tomorrow, weekdays, etc.)
- Recurring reminders (daily, weekdays, weekly, hourly, custom intervals)
- Priority classification (Urgent, High, Normal, Low)
- Snooze and reschedule support
- Thread-safe storage with atomic writes
- Missed reminder catch-up on boot
- Humanized time formatting
"""

import json
import os
import re
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMINDERS_PATH = os.path.join(BASE_DIR, "memory", "reminders.json")

# Thread lock to prevent concurrent read/write race conditions
_lock = threading.RLock()


# ─────────────────────────────────────────────────────────────────────────────
# Storage & Persistence
# ─────────────────────────────────────────────────────────────────────────────

def _load_reminders() -> dict[str, Any]:
    """Thread-safe load of reminders from JSON file."""
    with _lock:
        if not os.path.exists(REMINDERS_PATH):
            return {"reminders": []}

        try:
            with open(REMINDERS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict) or "reminders" not in data:
                    return {"reminders": []}
                # Sanitize records to ensure all required fields exist
                for r in data["reminders"]:
                    if "priority" not in r:
                        r["priority"] = "normal"
                    if "repeat" not in r:
                        r["repeat"] = "none"
                    if "done" not in r:
                        r["done"] = False
                    if "snooze_count" not in r:
                        r["snooze_count"] = 0
                return data
        except Exception as e:
            print(f"[Reminders] Error reading reminders: {e}")
            return {"reminders": []}


def _save_reminders(data: dict[str, Any]) -> None:
    """Thread-safe and atomic save of reminders data dictionary."""
    with _lock:
        try:
            os.makedirs(os.path.dirname(REMINDERS_PATH), exist_ok=True)
            tmp_path = REMINDERS_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            os.replace(tmp_path, REMINDERS_PATH)
        except Exception as e:
            print(f"[Reminders] Error saving reminders: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Time & Natural Language Parsing
# ─────────────────────────────────────────────────────────────────────────────

_DAY_NAME_TO_INT = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def parse_time_expression(expr: str | int | float, base_dt: Optional[datetime] = None) -> tuple[datetime, str]:
    """
    Parse a flexible time expression into an absolute datetime and detected repeat mode.

    Supports:
      - Integers / Floats: treated as minutes from now
      - Relative: "in 10 minutes", "in 2 hours", "in 45 sec", "half an hour", "1.5 hours"
      - Clock times: "at 5pm", "at 5:30 pm", "at 17:00", "at 9am", "at noon", "at midnight"
      - Relative days: "tomorrow at 9am", "tonight at 8pm", "this evening at 7:30"
      - Weekdays: "on Friday at 4pm", "next Monday at 10am"
      - Hindi / Hinglish: "5 minute baad", "1 ghante baad", "kal subah 9 baje", "shaam ko 7 baje"
      - Recurring patterns: "every day at 8am", "daily at 9am", "every hour"

    Returns:
        tuple[datetime, repeat_str]
    """
    now = base_dt or datetime.now()
    repeat = "none"

    if isinstance(expr, (int, float)):
        minutes = max(0.1, float(expr))
        return now + timedelta(minutes=minutes), repeat

    text = str(expr).strip().lower()

    # Detect recurrence in string
    if re.search(r"\b(every day|daily|har roz|roz)\b", text):
        repeat = "daily"
    elif re.search(r"\b(every weekday|weekdays|har working day)\b", text):
        repeat = "weekdays"
    elif re.search(r"\b(every week|weekly|har hafte)\b", text):
        repeat = "weekly"
    elif re.search(r"\b(every hour|hourly|har ghante)\b", text):
        repeat = "hourly"
    elif m_rep := re.search(r"every\s+(\d+)\s*(hour|hr|minute|min)s?", text):
        val = int(m_rep.group(1))
        unit = "h" if "h" in m_rep.group(2) else "m"
        repeat = f"every_{val}{unit}"

    # 1. Hindi time offsets: "X minute baad", "X ghante baad", "aadhe ghante baad"
    if "aadhe ghante baad" in text or "half an hour" in text:
        return now + timedelta(minutes=30), repeat

    if m := re.search(r"(\d+(?:\.\d+)?)\s*(?:ghante|ghanta)\s*baad", text):
        hrs = float(m.group(1))
        return now + timedelta(hours=hrs), repeat

    if m := re.search(r"(\d+)\s*minute\s*baad", text):
        mins = int(m.group(1))
        return now + timedelta(minutes=mins), repeat

    # 2. English relative: "in X hours Y minutes", "in X mins", "in X sec"
    rel_m = re.search(r"(?:in|after)\s+(?:(\d+)\s*(?:hours|hour|hrs|hr))?\s*(?:(\d+)\s*(?:minutes|minute|mins|min))?\s*(?:(\d+)\s*(?:seconds|second|secs|sec|s))?", text)
    if rel_m and any(rel_m.groups()):
        h = int(rel_m.group(1) or 0)
        m_val = int(rel_m.group(2) or 0)
        s = int(rel_m.group(3) or 0)
        delta = timedelta(hours=h, minutes=m_val, seconds=s)
        if delta.total_seconds() > 0:
            return now + delta, repeat

    # Simple single unit relative: "in 2.5 hours", "in 45 mins", "in 30s"
    m_simple = re.search(r"(?:in|after)\s+(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)\b", text)
    if m_simple:
        num = float(m_simple.group(1))
        unit = m_simple.group(2)
        if unit.startswith("h"):
            return now + timedelta(hours=num), repeat
        elif unit.startswith("s"):
            return now + timedelta(seconds=num), repeat
        else:
            return now + timedelta(minutes=num), repeat

    # 3. Target Date determination: today vs tomorrow vs specific weekday
    target_date = now.date()
    is_tomorrow = bool(re.search(r"\b(tomorrow|kal|next day)\b", text))
    if is_tomorrow:
        target_date = now.date() + timedelta(days=1)

    # Check for specific weekday: "on Friday", "this monday"
    weekday_match = re.search(r"\b(?:on|this|next)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)\b", text)
    if weekday_match and not is_tomorrow:
        w_name = weekday_match.group(1)
        target_weekday = _DAY_NAME_TO_INT[w_name]
        days_ahead = (target_weekday - now.weekday()) % 7
        if days_ahead == 0:
            # If today is Friday and user says "on Friday", check time later. If no time or past, 7 days ahead
            pass
        else:
            target_date = now.date() + timedelta(days=days_ahead)

    # 4. Target Time extraction: "at 5:30 pm", "at 9am", "at 14:00", "at noon", "at midnight"
    target_hour = None
    target_minute = 0

    if "at noon" in text or "dophar 12 baje" in text:
        target_hour, target_minute = 12, 0
    elif "at midnight" in text or "raat 12 baje" in text:
        target_hour, target_minute = 0, 0
    else:
        # Check standard 12h/24h regex: "at 5:30 pm", "at 9 am", "at 18:00", "5pm", "5:30pm"
        time_match = re.search(r"(?:at\s+|ko\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje)?\b", text)
        if time_match:
            raw_h = int(time_match.group(1))
            raw_m = int(time_match.group(2) or 0)
            ampm = (time_match.group(3) or "").lower()

            # Context cues: "shaam ko", "subah", "raat ko", "tonight", "morning", "evening"
            is_evening = bool(re.search(r"\b(evening|shaam|sham|tonight|raat|night)\b", text))
            is_morning = bool(re.search(r"\b(morning|subah)\b", text))

            if ampm == "pm" and raw_h < 12:
                raw_h += 12
            elif ampm == "am" and raw_h == 12:
                raw_h = 0
            elif not ampm:
                if is_evening and raw_h < 12:
                    raw_h += 12
                elif is_morning and raw_h == 12:
                    raw_h = 0
                elif raw_h <= 12 and not is_morning:
                    # Ambiguous (e.g. "at 5"): if 5 in 24h is in the past for today, treat as 5 PM (17:00)
                    candidate = datetime(target_date.year, target_date.month, target_date.day, raw_h, raw_m)
                    if candidate < now and raw_h + 12 < 24:
                        raw_h += 12

            if 0 <= raw_h < 24 and 0 <= raw_m < 60:
                target_hour = raw_h
                target_minute = raw_m

    if target_hour is not None:
        target_dt = datetime(target_date.year, target_date.month, target_date.day, target_hour, target_minute)
        # If target datetime is already in the past (e.g., currently 11 PM and user asks for 5 PM without saying tomorrow)
        if target_dt <= now and not is_tomorrow:
            # If recurrence is daily, target is tomorrow
            target_dt += timedelta(days=1)
        return target_dt, repeat

    # Default fallback: 5 minutes from now
    return now + timedelta(minutes=5), repeat


def parse_natural_reminder_input(text: str) -> dict[str, Any]:
    """
    Extract reminder task text, due datetime, priority, and repeat pattern
    from a free-form natural language query.

    Example inputs:
      - "remind me urgently to submit project tomorrow at 9am"
      - "remind me to drink water every 2 hours"
      - "set a timer for 10 minutes to take cookies out of oven"
      - "remind me on Friday at 5pm about team presentation"
    """
    clean = text.strip()

    # 1. Priority Detection
    priority = "normal"
    if re.search(r"\b(urgent|urgently|emergency|asap|important|zaroori|highest priority)\b", clean, re.IGNORECASE):
        priority = "urgent"
    elif re.search(r"\b(high priority|crucial|critical)\b", clean, re.IGNORECASE):
        priority = "high"
    elif re.search(r"\b(low priority|minor|whenever)\b", clean, re.IGNORECASE):
        priority = "low"

    # Remove priority words from the text body
    clean = re.sub(r"\b(urgently|urgent|emergency|asap|important|zaroori|highest priority|high priority|low priority)\b", "", clean, flags=re.IGNORECASE).strip()

    # 2. Extract and parse time & repeat expression
    # Find matching time parts
    due_dt, repeat = parse_time_expression(clean)

    # 3. Clean up the task description
    # Remove trigger phrases
    task = re.sub(r"^(?:dodo\s*,?\s*)?(?:please\s+)?(?:remind me|set\s*(?:a|the)?\s*reminder|set\s*(?:a|the)?\s*timer|yaad dilana|schedule\s*(?:a|the)?\s*reminder|alert me)\s*(?:to|that|about|for)?\s*", "", clean, flags=re.IGNORECASE).strip()

    # Remove trailing time/recurrence phrases from task
    task = re.sub(r"\b(?:in|after)\s+\d+(?:\.\d+)?\s*(?:hours|hour|hrs|hr|minutes|minute|mins|min|seconds|second|secs|sec|s)\b", "", task, flags=re.IGNORECASE)
    task = re.sub(r"\b(?:tomorrow|today|tonight|this evening|kal|subah|shaam)\b", "", task, flags=re.IGNORECASE)
    task = re.sub(r"\b(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm|baje)?\b", "", task, flags=re.IGNORECASE)
    task = re.sub(r"\b(?:every day|daily|every week|weekly|every hour|hourly|every weekday)\b", "", task, flags=re.IGNORECASE)
    task = re.sub(r"\b(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "", task, flags=re.IGNORECASE)
    task = re.sub(r"\s+", " ", task).strip(" ,.-")

    if not task:
        task = "Reminder"

    return {
        "text": task,
        "due": due_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "priority": priority,
        "repeat": repeat,
    }


def format_time_relative(due_dt: datetime, base_dt: Optional[datetime] = None) -> str:
    """Format due datetime into human-friendly relative string."""
    now = base_dt or datetime.now()
    diff = due_dt - now
    seconds = int(diff.total_seconds())

    if seconds < -60:
        past_mins = abs(seconds) // 60
        if past_mins < 60:
            return f"Overdue by {past_mins} min{'s' if past_mins != 1 else ''}"
        past_hrs = past_mins // 60
        return f"Overdue by {past_hrs} hr{'s' if past_hrs != 1 else ''}"
    elif -60 <= seconds <= 60:
        return "Due now"
    elif seconds < 3600:
        mins = max(1, seconds // 60)
        return f"In {mins} min{'s' if mins != 1 else ''}"
    elif seconds < 86400:
        hrs = seconds // 3600
        mins = (seconds % 3600) // 60
        if due_dt.date() == now.date():
            time_part = due_dt.strftime("%I:%M %p").lstrip("0")
            return f"Today at {time_part} (in {hrs}h {mins}m)" if mins else f"Today at {time_part} (in {hrs}h)"
        else:
            time_part = due_dt.strftime("%I:%M %p").lstrip("0")
            return f"Tomorrow at {time_part} (in {hrs}h)"
    elif seconds < 172800 and due_dt.date() == (now.date() + timedelta(days=1)):
        time_part = due_dt.strftime("%I:%M %p").lstrip("0")
        return f"Tomorrow at {time_part}"
    else:
        return due_dt.strftime("%b %d, %I:%M %p")


def _calculate_next_occurrence(due_dt: datetime, repeat: str) -> datetime:
    """Calculate the next scheduled occurrence for a recurring reminder."""
    if repeat == "daily":
        return due_dt + timedelta(days=1)
    elif repeat == "weekdays":
        next_dt = due_dt + timedelta(days=1)
        while next_dt.weekday() >= 5:  # 5=Sat, 6=Sun
            next_dt += timedelta(days=1)
        return next_dt
    elif repeat == "weekly":
        return due_dt + timedelta(weeks=1)
    elif repeat == "hourly":
        return due_dt + timedelta(hours=1)
    elif repeat.startswith("every_"):
        m = re.match(r"every_(\d+)([hm])", repeat)
        if m:
            val = int(m.group(1))
            unit = m.group(2)
            if unit == "h":
                return due_dt + timedelta(hours=val)
            else:
                return due_dt + timedelta(minutes=val)
    return due_dt + timedelta(days=1)


# ─────────────────────────────────────────────────────────────────────────────
# Primary Reminder API
# ─────────────────────────────────────────────────────────────────────────────

def set_reminder(
    text: str,
    minutes: int | float | str = 5,
    priority: str = "normal",
    repeat: str = "none",
    due_iso: Optional[str] = None
) -> str:
    """
    Create a new reminder with advanced parameters and persistence.

    Args:
        text: Description of reminder.
        minutes: Number of minutes, or a natural time string like 'at 5pm', 'tomorrow 9am'.
        priority: 'low', 'normal', 'high', or 'urgent'.
        repeat: 'none', 'daily', 'weekdays', 'weekly', 'hourly', or interval.
        due_iso: Optional direct ISO 8601 string.
    """
    try:
        clean_text = (text or "").strip()
        if not clean_text:
            return "Cannot set a reminder with empty text."

        now = datetime.now()

        if due_iso:
            due_dt = datetime.fromisoformat(due_iso)
            detected_repeat = repeat
        elif isinstance(minutes, str):
            due_dt, detected_repeat = parse_time_expression(minutes, base_dt=now)
            if repeat == "none" and detected_repeat != "none":
                repeat = detected_repeat
        else:
            mins_val = max(0.1, float(minutes))
            due_dt = now + timedelta(minutes=mins_val)

        # Normalize priority
        norm_priority = priority.lower()
        if norm_priority not in ("low", "normal", "high", "urgent"):
            norm_priority = "normal"

        with _lock:
            data = _load_reminders()
            reminders_list = data.get("reminders", [])

            next_id = max((r.get("id", 0) for r in reminders_list if isinstance(r.get("id"), int)), default=0) + 1

            new_reminder = {
                "id": next_id,
                "text": clean_text,
                "due": due_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "created_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
                "done": False,
                "priority": norm_priority,
                "repeat": repeat,
                "snooze_count": 0,
                "missed_alerted": False,
            }

            reminders_list.append(new_reminder)
            data["reminders"] = reminders_list
            _save_reminders(data)

        # Build friendly response
        rel_str = format_time_relative(due_dt, now)
        rep_str = f" [Repeat: {repeat.capitalize()}]" if repeat != "none" else ""
        prio_tag = f" [{norm_priority.upper()}]" if norm_priority != "normal" else ""
        return f"Reminder set [{next_id}]: '{clean_text}' - {rel_str}.{prio_tag}{rep_str}".strip()

    except Exception as e:
        return f"Failed to set reminder: {str(e)}"


def create_reminder_from_natural_text(query: str) -> str:
    """Parse and create a reminder directly from conversational speech or chat."""
    parsed = parse_natural_reminder_input(query)
    return set_reminder(
        text=parsed["text"],
        due_iso=parsed["due"],
        priority=parsed["priority"],
        repeat=parsed["repeat"]
    )


def list_reminders(filter_type: str = "pending") -> str:
    """
    List reminders matching the filter criteria.

    Args:
        filter_type: 'pending', 'today', 'completed', or 'all'.
    """
    try:
        data = _load_reminders()
        reminders_list = data.get("reminders", [])
        now = datetime.now()

        if filter_type == "completed":
            items = [r for r in reminders_list if r.get("done", False)]
            title = "Completed reminders:"
        elif filter_type == "today":
            items = [
                r for r in reminders_list
                if not r.get("done", False) and r.get("due") and
                datetime.fromisoformat(r["due"]).date() == now.date()
            ]
            title = "Today's reminders:"
        elif filter_type == "all":
            items = reminders_list
            title = "All reminders:"
        else:  # default 'pending'
            items = [r for r in reminders_list if not r.get("done", False)]
            title = "Pending reminders:"

        if not items:
            return f"No {filter_type} reminders found."

        # Sort: pending first by due date, completed by due date descending
        items.sort(key=lambda x: x.get("due", ""))

        lines = [f"{title} ({len(items)})"]
        for r in items:
            r_id = r.get("id")
            r_text = r.get("text", "")
            r_due = r.get("due", "")
            prio = r.get("priority", "normal")
            rep = r.get("repeat", "none")
            is_done = r.get("done", False)

            prio_badge = f"[{prio.upper()}] " if prio != "normal" else ""
            rep_badge = f" [Repeat: {rep}]" if rep != "none" else ""
            status_badge = " [Completed]" if is_done else ""

            try:
                dt = datetime.fromisoformat(r_due)
                rel = format_time_relative(dt, now)
                due_display = f"{rel} ({dt.strftime('%I:%M %p')})"
            except Exception:
                due_display = r_due

            lines.append(f"  [{r_id}] {prio_badge}{r_text} - {due_display}{rep_badge}{status_badge}")

        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list reminders: {str(e)}"


def get_reminders(filter_type: str = "pending") -> list[dict[str, Any]]:
    """Retrieve raw reminder dictionaries for UI or integration."""
    data = _load_reminders()
    reminders_list = data.get("reminders", [])
    now = datetime.now()

    if filter_type == "pending":
        items = [r for r in reminders_list if not r.get("done", False)]
    elif filter_type == "completed":
        items = [r for r in reminders_list if r.get("done", False)]
    elif filter_type == "today":
        items = [
            r for r in reminders_list
            if not r.get("done", False) and r.get("due") and
            datetime.fromisoformat(r["due"]).date() == now.date()
        ]
    else:
        items = reminders_list

    items.sort(key=lambda x: x.get("due", ""))
    return items


def check_due_reminders(detailed: bool = False) -> list[Any]:
    """
    Check for due reminders, advance recurring ones or mark one-offs completed.
    Designed for periodic polling (e.g. every 5 seconds).

    Args:
        detailed: If True, returns list of dicts. If False, returns list of formatted string alerts.
    """
    with _lock:
        try:
            data = _load_reminders()
            reminders_list = data.get("reminders", [])
            now = datetime.now()

            due_items: list[Any] = []
            updated = False

            for r in reminders_list:
                if r.get("done", False):
                    continue

                due_str = r.get("due")
                if not due_str:
                    continue

                try:
                    due_dt = datetime.fromisoformat(due_str)
                    if due_dt <= now:
                        repeat = r.get("repeat", "none")
                        r_text = r.get("text", "")
                        r_priority = r.get("priority", "normal")

                        if detailed:
                            due_items.append(dict(r))
                        else:
                            prefix = "URGENT: " if r_priority in ("urgent", "high") else ""
                            due_items.append(f"{prefix}{r_text}")

                        # Handle recurrence
                        if repeat != "none":
                            next_due = _calculate_next_occurrence(due_dt, repeat)
                            r["due"] = next_due.strftime("%Y-%m-%dT%H:%M:%S")
                            r["last_triggered"] = now.strftime("%Y-%m-%dT%H:%M:%S")
                        else:
                            r["done"] = True
                            r["completed_at"] = now.strftime("%Y-%m-%dT%H:%M:%S")

                        updated = True
                except Exception as e:
                    print(f"[Reminders] Error checking item {r.get('id')}: {e}")
                    continue

            if updated:
                data["reminders"] = reminders_list
                _save_reminders(data)

            return due_items
        except Exception as e:
            print(f"[Reminders] Error checking due reminders: {e}")
            return []


def check_missed_reminders(max_hours: int = 24) -> list[dict[str, Any]]:
    """
    Detect reminders that became due while DODO was closed / offline.
    Marks them as alerted so they don't repeatedly spam on subsequent reboots.
    """
    with _lock:
        try:
            data = _load_reminders()
            reminders_list = data.get("reminders", [])
            now = datetime.now()
            cutoff = now - timedelta(hours=max_hours)

            missed: list[dict[str, Any]] = []
            updated = False

            for r in reminders_list:
                if r.get("done", False) or r.get("missed_alerted", False):
                    continue
                due_str = r.get("due")
                if not due_str:
                    continue
                try:
                    due_dt = datetime.fromisoformat(due_str)
                    if cutoff <= due_dt < now:
                        r["missed_alerted"] = True
                        missed.append(r)
                        updated = True
                except Exception:
                    continue

            if updated:
                data["reminders"] = reminders_list
                _save_reminders(data)

            return missed
        except Exception as e:
            print(f"[Reminders] Error checking missed reminders: {e}")
            return []


def snooze_reminder(reminder_id: int, minutes: int = 10) -> str:
    """Snooze an existing reminder by N minutes from right now."""
    with _lock:
        try:
            data = _load_reminders()
            reminders_list = data.get("reminders", [])

            target = next((r for r in reminders_list if r.get("id") == reminder_id), None)
            if target is None:
                return f"Reminder #{reminder_id} not found."

            now = datetime.now()
            snooze_mins = max(1, int(minutes))
            new_due = now + timedelta(minutes=snooze_mins)

            target["due"] = new_due.strftime("%Y-%m-%dT%H:%M:%S")
            target["done"] = False
            target["snooze_count"] = target.get("snooze_count", 0) + 1

            data["reminders"] = reminders_list
            _save_reminders(data)

            return f"Snoozed reminder #{reminder_id} ('{target.get('text')}') for {snooze_mins} minutes."
        except Exception as e:
            return f"Failed to snooze reminder: {str(e)}"


def complete_reminder(reminder_id: int) -> str:
    """Mark a reminder as completed."""
    with _lock:
        try:
            data = _load_reminders()
            reminders_list = data.get("reminders", [])

            target = next((r for r in reminders_list if r.get("id") == reminder_id), None)
            if target is None:
                return f"Reminder #{reminder_id} not found."

            if target.get("done", False):
                return f"Reminder #{reminder_id} is already marked as completed."

            target["done"] = True
            target["completed_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            data["reminders"] = reminders_list
            _save_reminders(data)

            return f"Reminder #{reminder_id} ('{target.get('text')}') marked as completed."
        except Exception as e:
            return f"Failed to complete reminder: {str(e)}"


def cancel_reminder(reminder_id: int) -> str:
    """Delete or cancel a reminder by its ID."""
    with _lock:
        try:
            data = _load_reminders()
            reminders_list = data.get("reminders", [])

            target_idx = None
            for idx, r in enumerate(reminders_list):
                if r.get("id") == reminder_id:
                    target_idx = idx
                    break

            if target_idx is None:
                return f"Reminder with ID {reminder_id} not found."

            removed = reminders_list.pop(target_idx)
            data["reminders"] = reminders_list
            _save_reminders(data)

            return f"Reminder #{reminder_id} ('{removed.get('text')}') has been removed."
        except Exception as e:
            return f"Failed to cancel reminder: {str(e)}"


def clear_reminders(target: str = "completed") -> str:
    """
    Clear reminders in bulk.
    Args:
        target: 'completed' (default) or 'all'.
    """
    with _lock:
        try:
            data = _load_reminders()
            reminders_list = data.get("reminders", [])

            if target == "all":
                count = len(reminders_list)
                data["reminders"] = []
                _save_reminders(data)
                return f"Cleared all {count} reminders."
            else:
                remaining = [r for r in reminders_list if not r.get("done", False)]
                cleared_count = len(reminders_list) - len(remaining)
                data["reminders"] = remaining
                _save_reminders(data)
                return f"Cleared {cleared_count} completed reminder{'s' if cleared_count != 1 else ''}."
        except Exception as e:
            return f"Failed to clear reminders: {str(e)}"
