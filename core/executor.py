"""
DODO — core/executor.py
Dispatches task plans to skill modules and returns result text.
"""

import re
import random
from datetime import datetime

from core.memory import log_command, get_user_name, save_fact
import skills.system_control as sys_ctrl
import skills.browser_control as browser
import skills.file_ops as file_ops
import skills.device_control as device
import skills.weather as weather
import skills.reminders as reminders
import skills.web_search as web_search
from core.ollama_chat import chat as ollama_chat


# Keep only instant responses that don't need Ollama
_SMALL_TALK = {
    "time": lambda: f"It's {datetime.now().strftime('%I:%M %p')}.",
    "date": lambda: f"Today is {datetime.now().strftime('%A, %d %B %Y')}.",
}


def _small_talk_reply(action: str) -> str:
    resp = _SMALL_TALK.get(action, ["Sure!"])
    if callable(resp):
        return resp()
    return random.choice(resp)


# ── Main executor ──────────────────────────────────────────────────────────────

def execute(result: dict, confirm_callback=None) -> str:
    """
    Execute a classified command plan.
    result: output of brain.classify()
    confirm_callback: callable(question) -> bool  (for destructive operations)
    Returns a string response for TTS + UI display.
    """
    intent = result.get("intent", "unknown")
    plan   = result.get("plan", [])
    raw    = result.get("raw", "")

    log_command(raw, intent)
    responses = []

    for step in plan:
        step_type = step.get("type")
        action    = step.get("action")
        value     = step.get("value")

        try:
            # ── System commands ──────────────────────────────────────────────
            if step_type == "system_command":
                if action == "open_app":
                    ok = sys_ctrl.open_app(value)
                    responses.append(f"Opening {value}." if ok else f"Sorry, I couldn't find {value}.")

                elif action == "close_app":
                    ok = sys_ctrl.close_app(value)
                    responses.append(f"Closed {value}." if ok else f"Couldn't close {value}.")

                elif action == "volume_up":
                    lvl = sys_ctrl.volume_up(value or 10)
                    responses.append(f"Volume up. Now at {lvl}%.")

                elif action == "volume_down":
                    lvl = sys_ctrl.volume_down(value or 10)
                    responses.append(f"Volume down. Now at {lvl}%.")

                elif action == "mute":
                    sys_ctrl.toggle_mute()
                    responses.append("Toggled mute.")

                elif action == "lock_screen":
                    sys_ctrl.lock_screen()
                    responses.append("Locking screen.")

                elif action == "screenshot":
                    path = sys_ctrl.take_screenshot()
                    responses.append(f"Screenshot saved to {path}.")

                elif action == "battery":
                    info = sys_ctrl.get_battery()
                    responses.append(info)

                elif action in ("restart", "sleep"):
                    if confirm_callback:
                        ok = confirm_callback(f"Are you sure you want to {action}?")
                    else:
                        ok = False
                        responses.append(f"{action.capitalize()} cancelled. Please confirm in the panel.")
                    if ok:
                        sys_ctrl.power_action(action)
                        responses.append(f"{action.capitalize()} initiated.")

            # ── Internet actions ─────────────────────────────────────────────
            elif step_type == "internet_action":
                if action == "open_site":
                    browser.open_url(value)
                    responses.append(f"Opening {value}.")

                elif action == "google_search":
                    browser.google_search(value)
                    responses.append(f"Searching for '{value}'.")

                elif action == "youtube_play":
                    browser.youtube_search(value)
                    responses.append(f"Playing '{value}' on YouTube.")

            # ── File operations ──────────────────────────────────────────────
            elif step_type == "file_operation":
                requires_confirm = step.get("requires_confirm", False)

                if action == "find_file":
                    results = file_ops.find_file(value)
                    if results:
                        responses.append(f"Found: {results[0]}")
                    else:
                        responses.append(f"No file found matching '{value}'.")

                elif action == "create_folder":
                    path = file_ops.create_folder(value)
                    responses.append(f"Created folder: {path}")

                elif action == "create_file":
                    # Detect if user wants code or content generated
                    raw_lower = raw.lower()
                    content = ""
                    filename = value

                    # If the request mentions code/programming, ask LLM to generate it
                    code_keywords = ["code", "program", "script", "calculator", "function",
                                     "class", "html", "python", "c lang", "java", "javascript"]
                    if any(kw in raw_lower for kw in code_keywords):
                        # Determine file extension from context
                        ext_map = {"python": ".py", "c lang": ".c", "c++": ".cpp", "cpp": ".cpp",
                                   "java": ".java", "javascript": ".js", "js": ".js",
                                   "html": ".html", "css": ".css", "json": ".json"}
                        ext = ".txt"
                        for lang, e in ext_map.items():
                            if lang in raw_lower:
                                ext = e
                                break
                        if "calculator" in raw_lower and ext == ".txt":
                            ext = ".c"  # default for calculator

                        # Clean up filename
                        if "." not in filename:
                            filename = filename.split()[0] if filename else "code"
                            filename = filename + ext

                        # Generate code via LLM
                        content = ollama_chat(
                            f"Write ONLY the code (no explanation, no markdown) for: {raw}"
                        )
                        # Strip markdown code fences if present
                        content = re.sub(r"^```\w*\n?", "", content)
                        content = re.sub(r"\n?```$", "", content)

                    path = file_ops.create_file(filename, content)
                    if path:
                        responses.append(f"Created file: {path}")
                        if content:
                            responses.append("I wrote the code into the file for you.")
                    else:
                        responses.append(f"Couldn't create file '{filename}'.")

                elif action == "read_file":
                    # First find the file, then read it
                    results = file_ops.find_file(value)
                    if results:
                        content = file_ops.read_file(results[0])
                        responses.append(f"Contents of {results[0]}:\n{content[:500]}")
                    else:
                        responses.append(f"No file found matching '{value}'.")

                elif action == "open_file":
                    results = file_ops.find_file(value)
                    if results:
                        ok = file_ops.open_file(results[0])
                        responses.append(f"Opening {results[0]}." if ok else f"Couldn't open the file.")
                    else:
                        responses.append(f"No file found matching '{value}'.")

                elif action == "delete_file":
                    if requires_confirm and confirm_callback:
                        ok = confirm_callback(f"Delete '{value}'? This cannot be undone.")
                        if ok:
                            file_ops.delete_path(value)
                            responses.append(f"Deleted {value}.")
                        else:
                            responses.append("Deletion cancelled.")
                    else:
                        responses.append("Deletion requires confirmation. Please use the panel.")

            # ── Weather ──────────────────────────────────────────────────────
            elif step_type == "weather":
                info = weather.get_weather(value)
                responses.append(info)

            # ── Reminders ────────────────────────────────────────────────────
            elif step_type == "reminder":
                if action == "set":
                    minutes = step.get("minutes", 5)
                    result = reminders.set_reminder(value, minutes)
                    responses.append(result)
                elif action == "list":
                    result = reminders.list_reminders()
                    responses.append(result)

            # ── Web search ───────────────────────────────────────────────────
            elif step_type == "web_search":
                if action == "search":
                    # Try quick answer first, then full search
                    answer = web_search.quick_answer(value)
                    if answer:
                        responses.append(answer)
                    else:
                        results = web_search.search(value)
                        responses.append(results)
                elif action == "summarize":
                    # If value looks like a URL, fetch and summarize it
                    if value and ("http" in value or "." in value.split()[-1] if value.split() else False):
                        raw_text = web_search.summarize_url(value)
                        if raw_text:
                            summary = ollama_chat(f"Summarize this in 3-4 sentences:\n{raw_text}")
                            responses.append(summary)
                        else:
                            responses.append("Couldn't fetch that page.")
                    else:
                        # Not a URL — search the web for it and summarize
                        results = web_search.search(f"{value} summary", max_results=3)
                        if results:
                            summary = ollama_chat(
                                f"Based on these search results, give a brief summary of '{value}':\n{results}"
                            )
                            responses.append(summary)
                        else:
                            responses.append(f"Couldn't find information about '{value}'.")

            # ── Vision / screen analysis ─────────────────────────────────────
            elif step_type == "vision":
                if action == "analyze_screen":
                    try:
                        path = sys_ctrl.take_screenshot()
                        responses.append(f"Screenshot saved to {path}. "
                                        f"Vision analysis isn't available right now, "
                                        f"but I captured your screen.")
                    except Exception as e:
                        responses.append(f"Couldn't take screenshot: {e}")

            # ── Memory ───────────────────────────────────────────────────────
            elif step_type == "memory":
                if action == "remember":
                    save_fact(value)
                    responses.append(f"Got it, I'll remember that: {value}")

            # ── Device control ───────────────────────────────────────────────
            elif step_type == "device_control":
                if action == "bluetooth":
                    ok = device.set_bluetooth(value == "on")
                    responses.append(f"Bluetooth turned {value}." if ok
                                     else "Couldn't toggle Bluetooth. Try manually.")

                elif action == "wifi":
                    ok = device.set_wifi(value == "on")
                    responses.append(f"WiFi turned {value}." if ok
                                     else "Couldn't toggle WiFi. Try manually.")

            # ── Groq conversational AI ───────────────────────────────────────
            elif step_type == "ollama_chat":
                user_text = value or raw
                reply = ollama_chat(user_text)
                responses.append(reply)

            # ── Small talk (instant: time, date) ────────────────────────────
            elif step_type == "small_talk":
                responses.append(_small_talk_reply(action))

        except Exception as e:
            responses.append(f"Something went wrong: {str(e)}")

    return " ".join(responses) if responses else "Done."
