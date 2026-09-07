"""
DODO — install.py
One-time setup script:
  1. Installs all Python dependencies
  2. Creates memory directory and default files
  3. Registers DODO in Windows Registry for auto-startup

Run once with: python install.py
"""

import subprocess
import sys
import os
import json
import winreg

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BANNER = r"""
  ____   ___  ____   ___  
 |  _ \ / _ \|  _ \ / _ \ 
 | | | | | | | | | | | | |
 | |_| | |_| | |_| | |_| |
 |____/ \___/|____/ \___/ 
   Desktop AI Assistant
   Setup & Installer
"""

def step(msg): print(f"\n  ▸ {msg}")
def ok(msg):   print(f"    ✓ {msg}")
def err(msg):  print(f"    ✗ {msg}")


def install_dependencies():
    step("Installing Python dependencies…")
    req = os.path.join(BASE_DIR, "requirements.txt")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", req, "--quiet"],
        capture_output=False
    )
    if result.returncode == 0:
        ok("All packages installed successfully.")
    else:
        err("Some packages failed. Check output above.")
        err("You may need to install PyAudio manually:")
        print("      pip install pipwin && pipwin install pyaudio")


def ensure_memory():
    step("Setting up memory directory…")
    mem_dir = os.path.join(BASE_DIR, "memory")
    os.makedirs(mem_dir, exist_ok=True)

    commands_path = os.path.join(mem_dir, "commands.json")
    if not os.path.exists(commands_path):
        with open(commands_path, "w") as f:
            json.dump({"history": [], "frequency": {}}, f, indent=4)
        ok("Created memory/commands.json")

    prefs_path = os.path.join(mem_dir, "preferences.json")
    if not os.path.exists(prefs_path):
        with open(prefs_path, "w") as f:
            json.dump({
                "user_name": "Boss",
                "preferred_browser": "chrome",
                "preferred_search_engine": "google",
                "language": "en",
                "custom_apps": {},
                "last_seen": None,
                "session_count": 0
            }, f, indent=4)
        ok("Created memory/preferences.json")

    ok("Memory directory ready.")


def register_startup():
    step("Registering DODO in Windows startup…")
    try:
        # Use pythonw.exe to launch without a console window
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable  # fallback to python.exe

        main_py = os.path.join(BASE_DIR, "main.py")
        value   = f'"{pythonw}" "{main_py}"'

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "DODO", 0, winreg.REG_SZ, value)
        winreg.CloseKey(key)

        ok(f"Startup key set: {value}")
        ok("DODO will now launch automatically when Windows starts.")
    except Exception as e:
        err(f"Failed to register startup: {e}")
        err("Run this script as Administrator if the error persists.")


def remove_startup():
    """Utility to remove the startup entry."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE
        )
        winreg.DeleteValue(key, "DODO")
        winreg.CloseKey(key)
        ok("DODO removed from Windows startup.")
    except FileNotFoundError:
        ok("DODO was not in startup (nothing to remove).")
    except Exception as e:
        err(f"Could not remove startup entry: {e}")


def main():
    print(BANNER)
    print("  Installing DODO — your desktop AI assistant\n")

    if "--remove-startup" in sys.argv:
        remove_startup()
        return

    install_dependencies()
    ensure_memory()
    # register_startup()

    print("\n" + "─" * 48)
    print("  ✓  DODO install complete!")
    print("  ✓  Run:  python main.py  to start now")
    print("  ✓  Or restart your PC for auto-start")
    print("  ✓  Hotkey:  Ctrl + Alt + D  to summon")
    print("─" * 48 + "\n")


if __name__ == "__main__":
    main()
