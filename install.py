"""
DODO — install.py
First-run setup wizard. Run once before starting DODO.

  python install.py

Does:
  1. Installs all Python dependencies
  2. Creates config.json from template (asks for name + API key)
  3. Validates the API key with a real Groq ping
  4. Creates memory/ directory with empty default files
  5. Optionally registers DODO in Windows startup
"""

import subprocess
import sys
import os
import json
import shutil

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(BASE_DIR, "config.json")
EXAMPLE_PATH = os.path.join(BASE_DIR, "config.example.json")
MEMORY_DIR   = os.path.join(BASE_DIR, "memory")

BANNER = r"""
  ____   ___  ____   ___
 |  _ \ / _ \|  _ \ / _ \
 | | | | | | | | | | | | |
 | |_| | |_| | |_| | |_| |
 |____/ \___/|____/ \___/
   AI Desktop Assistant
   Setup & Installer
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def step(msg): print(f"\n  ▸ {msg}")
def ok(msg):   print(f"    ✓ {msg}")
def warn(msg): print(f"    ⚠  {msg}")
def err(msg):  print(f"    ✗ {msg}")
def ask(prompt, default=""):
    val = input(f"    → {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return val if val else default


# ── Step 1: Dependencies ──────────────────────────────────────────────────────

def install_dependencies():
    step("Installing Python dependencies…")
    req = os.path.join(BASE_DIR, "requirements.txt")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", req, "--quiet"],
        capture_output=False
    )
    if result.returncode == 0:
        ok("All packages installed.")
    else:
        warn("Some packages may have failed. Check output above.")
        warn("If PyAudio fails: pip install pipwin && pipwin install pyaudio")


# ── Step 2: Config setup ──────────────────────────────────────────────────────

def setup_config():
    step("Setting up config.json…")

    if os.path.exists(CONFIG_PATH):
        cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
        existing_key = cfg.get("groq_api_key", "")
        existing_name = cfg.get("user_name", "")
        if existing_key and not existing_key.startswith("YOUR_"):
            ok(f"config.json already set up (user: {existing_name})")
            print("    Run with --reset to reconfigure.")
            return cfg

    # Copy template
    if not os.path.exists(EXAMPLE_PATH):
        err("config.example.json not found. Re-clone the repo.")
        sys.exit(1)
    shutil.copy(EXAMPLE_PATH, CONFIG_PATH)

    cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))

    print()
    print("  Let's personalise DODO for you.\n")

    # Name
    name = ask("What's your name?", "User")
    cfg["user_name"] = name

    # API Key
    print()
    print("  You need a FREE Groq API key to power DODO's AI brain.")
    print("  Get one in 30 seconds at: https://console.groq.com")
    print("  (Sign up → API Keys → Create API Key)\n")
    while True:
        key = ask("Paste your Groq API key (starts with gsk_)")
        if not key:
            warn("API key is required. DODO won't work without it.")
            continue
        if not key.startswith("gsk_") or len(key) < 20:
            warn("That doesn't look like a valid Groq key (should start with gsk_).")
            retry = ask("Try again? (y/n)", "y")
            if retry.lower() != "y":
                warn("Skipping key validation. Edit config.json manually.")
                break
            continue
        cfg["groq_api_key"] = key
        break

    # Wake word
    wake = ask("Wake word (what you say to activate DODO)", "dodo")
    cfg["wake_word"] = wake.lower()

    # Save
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)
    ok(f"config.json saved. Welcome, {name}!")
    return cfg


# ── Step 3: Validate API key ──────────────────────────────────────────────────

def validate_api_key(cfg: dict):
    step("Validating Groq API key…")
    key = cfg.get("groq_api_key", "")
    if not key or key.startswith("YOUR_"):
        warn("No API key set — skipping validation.")
        return

    try:
        from openai import OpenAI, AuthenticationError
        client = OpenAI(
            api_key=key,
            base_url=cfg.get("openai_base_url", "https://api.groq.com/openai/v1")
        )
        # Minimal test call
        client.chat.completions.create(
            model=cfg.get("groq_model", "openai/gpt-oss-120b"),
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5
        )
        ok("API key is valid! Groq connection successful.")
    except Exception as e:
        msg = str(e).lower()
        if "auth" in msg or "invalid" in msg or "api_key" in msg:
            err("API key is INVALID. Please check it and update config.json.")
            err(f"Details: {str(e)[:100]}")
        elif "connect" in msg or "network" in msg or "timeout" in msg:
            warn("Could not reach Groq (no internet?). Key not validated.")
        else:
            warn(f"Unexpected error during validation: {str(e)[:80]}")


# ── Step 4: Memory directory ──────────────────────────────────────────────────

def ensure_memory():
    step("Setting up memory directory…")
    os.makedirs(MEMORY_DIR, exist_ok=True)

    defaults = {
        "commands.json":    {"history": [], "frequency": {}},
        "preferences.json": {
            "user_name": "User",
            "preferred_browser": "chrome",
            "preferred_search_engine": "google",
            "language": "en",
            "custom_apps": {},
            "last_seen": None,
            "session_count": 0
        },
        "user_profile.json": {"facts": [], "updated": None},
        "chat_history.json": {"history": [], "saved": None},
        "reminders.json":    {"reminders": []},
    }

    for filename, default_data in defaults.items():
        path = os.path.join(MEMORY_DIR, filename)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default_data, f, indent=4)
            ok(f"Created memory/{filename}")
        else:
            ok(f"memory/{filename} already exists — preserved.")


# ── Step 5: Optional Windows startup ─────────────────────────────────────────

def register_startup():
    answer = ask("\n  Start DODO automatically when Windows boots? (y/n)", "n")
    if answer.lower() != "y":
        return
    try:
        import winreg
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        main_py = os.path.join(BASE_DIR, "main.py")
        value   = f'"{pythonw}" "{main_py}"'
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "DODO", 0, winreg.REG_SZ, value)
        winreg.CloseKey(key)
        ok("DODO added to Windows startup.")
    except Exception as e:
        warn(f"Could not register startup: {e}")
        warn("Run as Administrator if needed.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(BANNER)
    print("  Setting up DODO — your AI desktop assistant\n")

    if "--remove-startup" in sys.argv:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE
            )
            winreg.DeleteValue(key, "DODO")
            winreg.CloseKey(key)
            ok("DODO removed from Windows startup.")
        except FileNotFoundError:
            ok("DODO was not in startup.")
        except Exception as e:
            err(f"Could not remove startup entry: {e}")
        return

    install_dependencies()
    cfg = setup_config()
    validate_api_key(cfg)
    ensure_memory()
    register_startup()

    print("\n" + "─" * 50)
    print("  ✓  DODO setup complete!")
    print(f"  ✓  Run:  python main.py  to start")
    print(f"  ✓  Say '{cfg.get('wake_word', 'dodo')}' to wake me up")
    print(f"  ✓  Or type in the text box and press Enter")
    print("─" * 50 + "\n")


if __name__ == "__main__":
    main()
