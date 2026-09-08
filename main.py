"""
DODO — main.py
Entry point: boots voice engine + control panel.
Uses a shared queue for voice→UI communication
and a UI event queue for thread-safe UI updates.
"""

import os
import sys
import queue
import threading
import time
from datetime import datetime

from core.voice    import VoiceEngine
from core.agent   import process as agent_process
from core import speech
from core.memory   import load_config, update_session, get_user_name

# ── Queues ─────────────────────────────────────────────────────────────────────
command_queue = queue.Queue()   # voice/UI → brain/executor
ui_queue      = queue.Queue()   # any thread → UI

# Global reference to the panel (set after construction)
panel = None


# ── Thread-safe UI helpers ─────────────────────────────────────────────────────

def ui_status(state: str):
    ui_queue.put({"type": "status", "state": state})

def ui_chat(text: str, sender: str = "dodo"):
    ui_queue.put({"type": "chat", "text": text, "sender": sender})

def ui_log(action_type: str, text: str):
    ui_queue.put({"type": "log", "action_type": action_type, "text": text})


# ── Voice status callback ──────────────────────────────────────────────────────

def on_voice_status(state: str):
    ui_status(state)
    if state == "LISTENING":
        ui_log("system_command", "Wake word detected — listening…")


# ── Agent loop (runs in background thread) ────────────────────────────────────

def processing_loop():
    """Picks commands from queue, sends to agent, speaks response."""
    while True:
        try:
            cmd = command_queue.get(timeout=1)
        except queue.Empty:
            continue

        ui_status("PROCESSING")
        ui_chat(cmd, sender="user")
        ui_log("system_command", f"Command: {cmd}")

        # Let the agent handle everything
        try:
            response = agent_process(cmd)
        except Exception as e:
            response = f"Something went wrong: {str(e)[:100]}"

        # Update UI
        ui_chat(response, sender="dodo")
        ui_log("agent", f"→ {response[:60]}")

        # Speak
        ui_status("SPEAKING")
        speech.speak(response, on_done=lambda: ui_status("IDLE"))


# ── Reminder background checker ───────────────────────────────────────────────

def reminder_loop():
    """Check for due reminders every 30 seconds."""
    from skills.reminders import check_due_reminders
    while True:
        try:
            due = check_due_reminders()
            for text in due:
                msg = f"Reminder: {text}"
                ui_chat(msg, sender="dodo")
                ui_log("reminder", f"⏰ {msg}")
                speech.speak(msg)
        except Exception:
            pass
        time.sleep(30)


# ── Startup greeting ───────────────────────────────────────────────────────────

def greet():
    cfg  = load_config()
    if not cfg.get("startup_greeting", True):
        return
    hour = datetime.now().hour
    if hour < 12:
        part = "Good morning"
    elif hour < 17:
        part = "Good afternoon"
    else:
        part = "Good evening"

    name = get_user_name()
    msg  = f"{part}, {name}. DODO is online and ready. Say DODO to wake me up."
    ui_chat(msg, sender="dodo")
    ui_log("system_command", "DODO started")
    speech.speak(msg)


# ── Pre-flight checks ─────────────────────────────────────────────────────────

def preflight_check() -> bool:
    """
    Verify the environment is ready before launching the UI.
    Returns True if OK, False if the user should run install.py first.
    """
    issues = []

    # 1. config.json present?
    from core.memory import CONFIG_PATH, MEMORY_DIR
    if not os.path.exists(CONFIG_PATH):
        issues.append("config.json not found.")

    # 2. API key set?
    cfg = load_config()
    key = cfg.get("groq_api_key", "")
    if not key or key.startswith("YOUR_") or len(key) < 20:
        issues.append("Groq API key is missing or not set in config.json.")

    # 3. memory/ folder writable?
    try:
        mem_test = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "memory"
        )
        os.makedirs(mem_test, exist_ok=True)
    except OSError:
        issues.append("Cannot write to memory/ folder.")

    if issues:
        print("\n" + "─" * 52)
        print("  ⚠  DODO cannot start — setup incomplete:\n")
        for issue in issues:
            print(f"     • {issue}")
        print("\n  Fix: run  python install.py  first.")
        print("  Free API key: https://console.groq.com")
        print("─" * 52 + "\n")
        return False

    return True


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global panel

    # Run checks before anything else
    if not preflight_check():
        sys.exit(1)

    update_session()
    cfg = load_config()

    # Import here to avoid circular issues at module level
    from ui.control_panel import ControlPanel

    # Create voice engine
    voice = VoiceEngine(
        command_queue   = command_queue,
        status_callback = on_voice_status,
    )

    # Create panel (must run on main thread)
    panel = ControlPanel(
        command_queue = command_queue,
        voice_engine  = voice,
        on_exit       = lambda: voice.stop()
    )
    panel.set_ui_queue(ui_queue)

    # Start background threads
    voice.start()
    threading.Thread(target=processing_loop, daemon=True).start()
    threading.Thread(target=reminder_loop,   daemon=True).start()

    # Greeting (slight delay so UI renders first)
    panel.after(1200, greet)

    # Start the UI main loop (blocks until window is closed)
    panel.mainloop()


if __name__ == "__main__":
    main()
