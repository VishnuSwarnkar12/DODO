"""
DODO — skills/reminders.py
Reminder and timer management with local JSON persistence.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMINDERS_PATH = os.path.join(BASE_DIR, "memory", "reminders.json")


def _load_reminders() -> dict[str, Any]:
    """Load reminders from the JSON file, creating it if it does not exist."""
    if not os.path.exists(REMINDERS_PATH):
        return {"reminders": []}

    try:
        with open(REMINDERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict) or "reminders" not in data:
                return {"reminders": []}
            return data
    except Exception:
        return {"reminders": []}


def _save_reminders(data: dict[str, Any]) -> None:
    """Save reminders data dictionary to the JSON file."""
    try:
        os.makedirs(os.path.dirname(REMINDERS_PATH), exist_ok=True)
        with open(REMINDERS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[Reminders] Error saving reminders: {e}")


def set_reminder(text: str, minutes: int) -> str:
    """
    Create a reminder due in N minutes from now.

    Args:
        text: Description of the reminder task.
        minutes: Number of minutes from now when the reminder is due.

    Returns:
        Confirmation message string.
    """
    try:
        if not text or not text.strip():
            return "Cannot set a reminder with empty text."

        clean_text = text.strip()
        minutes_val = max(0, int(minutes))
        now = datetime.now()
        due_time = now + timedelta(minutes=minutes_val)
        due_iso = due_time.strftime("%Y-%m-%dT%H:%M:%S")

        data = _load_reminders()
        reminders_list = data.get("reminders", [])

        # Generate the next available numeric ID
        next_id = max((r.get("id", 0) for r in reminders_list if isinstance(r.get("id"), int)), default=0) + 1

        new_reminder = {
            "id": next_id,
            "text": clean_text,
            "due": due_iso,
            "done": False,
        }

        reminders_list.append(new_reminder)
        data["reminders"] = reminders_list
        _save_reminders(data)

        min_unit = "minute" if minutes_val == 1 else "minutes"
        return f"Reminder set: '{clean_text}' in {minutes_val} {min_unit}."
    except Exception as e:
        return f"Failed to set reminder: {str(e)}"


def list_reminders() -> str:
    """
    Return all pending (not done) reminders as a formatted string.

    Returns:
        Formatted string listing pending reminders, or a status message if none exist.
    """
    try:
        data = _load_reminders()
        reminders_list = data.get("reminders", [])
        pending = [r for r in reminders_list if not r.get("done", False)]

        if not pending:
            return "No pending reminders."

        lines = ["Pending reminders:"]
        for r in pending:
            r_id = r.get("id")
            r_text = r.get("text", "")
            r_due = r.get("due", "")
            try:
                dt = datetime.fromisoformat(r_due)
                due_display = dt.strftime("%Y-%m-%d %I:%M %p")
            except Exception:
                due_display = r_due

            lines.append(f"[{r_id}] {r_text} (Due: {due_display})")

        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list reminders: {str(e)}"


def check_due_reminders() -> list[str]:
    """
    Check for reminders that are now due, mark them as done, and return their texts.
    Designed to be called periodically (e.g. by a background thread every 30 seconds).

    Returns:
        List of reminder texts that were due and have been marked done.
    """
    try:
        data = _load_reminders()
        reminders_list = data.get("reminders", [])
        now = datetime.now()

        due_texts: list[str] = []
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
                    r["done"] = True
                    due_texts.append(r.get("text", ""))
                    updated = True
            except Exception:
                continue

        if updated:
            data["reminders"] = reminders_list
            _save_reminders(data)

        return due_texts
    except Exception as e:
        print(f"[Reminders] Error checking due reminders: {e}")
        return []


def cancel_reminder(reminder_id: int) -> str:
    """
    Mark a reminder as cancelled/done by its ID.

    Args:
        reminder_id: Unique integer identifier of the reminder.

    Returns:
        Confirmation message indicating whether the reminder was cancelled.
    """
    try:
        data = _load_reminders()
        reminders_list = data.get("reminders", [])

        target = None
        for r in reminders_list:
            if r.get("id") == reminder_id:
                target = r
                break

        if target is None:
            return f"Reminder with ID {reminder_id} not found."

        if target.get("done", False):
            return f"Reminder {reminder_id} ('{target.get('text')}') is already completed or cancelled."

        target["done"] = True
        data["reminders"] = reminders_list
        _save_reminders(data)

        return f"Reminder {reminder_id} ('{target.get('text')}') has been cancelled."
    except Exception as e:
        return f"Failed to cancel reminder: {str(e)}"
