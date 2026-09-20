"""
DODO — skills/system_control.py
Controls apps, volume, screen, power on Windows.
"""

import os
import glob
import ctypes
import subprocess
import pyautogui
import psutil
import time
import re
from datetime import datetime
from skills.utils import run_command, run_powershell, is_process_running, get_base_dir

# Optional: pycaw for precise volume control
try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    _PYCAW_AVAILABLE = True
except Exception:
    _PYCAW_AVAILABLE = False


# ── Volume ─────────────────────────────────────────────────────────────────────

def _get_volume_interface():
    devices  = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def _get_current_volume() -> int:
    if _PYCAW_AVAILABLE:
        try:
            vol = _get_volume_interface()
            return int(vol.GetMasterVolumeLevelScalar() * 100)
        except Exception:
            pass
    return -1


def volume_up(step: int = 10) -> int:
    if _PYCAW_AVAILABLE:
        try:
            vol = _get_volume_interface()
            current = vol.GetMasterVolumeLevelScalar()
            new_val = min(1.0, current + step / 100.0)
            vol.SetMasterVolumeLevelScalar(new_val, None)
            return int(new_val * 100)
        except Exception:
            pass
    # Fallback: send media keys
    for _ in range(step // 2):
        pyautogui.press("volumeup")
    return -1


def volume_down(step: int = 10) -> int:
    if _PYCAW_AVAILABLE:
        try:
            vol = _get_volume_interface()
            current = vol.GetMasterVolumeLevelScalar()
            new_val = max(0.0, current - step / 100.0)
            vol.SetMasterVolumeLevelScalar(new_val, None)
            return int(new_val * 100)
        except Exception:
            pass
    for _ in range(step // 2):
        pyautogui.press("volumedown")
    return -1


def toggle_mute():
    if _PYCAW_AVAILABLE:
        try:
            vol = _get_volume_interface()
            muted = vol.GetMute()
            vol.SetMute(not muted, None)
            return
        except Exception:
            pass
    pyautogui.press("volumemute")


def set_volume_exact(level: int) -> int:
    """Set volume to exact percentage (0-100). Uses pycaw if available."""
    level = max(0, min(100, level))
    if _PYCAW_AVAILABLE:
        try:
            vol = _get_volume_interface()
            vol.SetMasterVolumeLevelScalar(level / 100.0, None)
            return level
        except Exception:
            pass
    # Fallback: can't set exact via media keys, return -1
    return -1


# ── App Control ────────────────────────────────────────────────────────────────

_APP_COMMANDS = {
    "chrome":      ["start", "chrome"],
    "msedge":      ["start", "msedge"],
    "firefox":     ["start", "firefox"],
    "notepad":     ["notepad"],
    "calc":        ["calc"],
    "explorer":    ["explorer"],
    "taskmgr":     ["taskmgr"],
    "cmd":         ["start", "cmd"],
    "powershell":  ["start", "powershell"],
    "code":        ["code"],
    "winword":     ["start", "winword"],
    "excel":       ["start", "excel"],
    "powerpnt":    ["start", "powerpnt"],
    "mspaint":     ["mspaint"],
    "snippingtool": ["snippingtool"],
    "control":     ["control"],
    "spotify":     ["start", "spotify"],
    "discord":     ["start", "discord"],
}


def open_app(name: str) -> bool:
    name = name.lower().strip()
    if not re.match(r'^[a-zA-Z0-9_.\-\s]+$', name):
        if name != "ms-settings:":
            return False
    cmd  = _APP_COMMANDS.get(name)
    if cmd:
        try:
            subprocess.Popen(cmd, shell=True)
            return True
        except Exception:
            pass
    # Try ms-settings: protocol
    if name == "ms-settings:":
        os.startfile("ms-settings:")
        return True
    # Generic: try os.startfile / start command
    try:
        os.startfile(name)
        return True
    except Exception:
        return False


def close_app(name: str) -> bool:
    name = name.lower().strip()
    # Map friendly names to exe names
    exe_map = {
        "chrome": "chrome", "google chrome": "chrome",
        "firefox": "firefox", "edge": "msedge",
        "notepad": "notepad", "calculator": "calc",
        "spotify": "spotify", "discord": "discord",
        "code": "code", "vscode": "code",
    }
    exe = exe_map.get(name, name)
    if not re.match(r'^[a-zA-Z0-9_.\-\s]+$', exe): return False
    code, _, _ = run_command(['taskkill', '/F', '/IM', f'{exe}.exe'])
    return code == 0


# ── Screen & System ────────────────────────────────────────────────────────────

def lock_screen():
    ctypes.windll.user32.LockWorkStation()


def _cleanup_old_screenshots():
    """Delete DODO screenshots older than 24 hours."""
    pics = os.path.join(os.path.expanduser("~"), "Pictures")
    cutoff = time.time() - 86400  # 24 hours
    for f in glob.glob(os.path.join(pics, "DODO_screenshot_*.png")):
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        except OSError:
            pass


def take_screenshot() -> str:
    _cleanup_old_screenshots()
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    pics  = os.path.join(os.path.expanduser("~"), "Pictures")
    path  = os.path.join(pics, f"DODO_screenshot_{ts}.png")
    img   = pyautogui.screenshot()
    img.save(path)
    return path


def get_battery() -> str:
    bat = psutil.sensors_battery()
    if bat is None:
        return "No battery detected — you're on desktop power."
    pct     = int(bat.percent)
    plugged = "plugged in" if bat.power_plugged else "on battery"
    return f"Battery is at {pct}%, {plugged}."


def power_action(action: str):
    """action: 'restart', 'sleep'"""
    if action == "restart":
        run_command("shutdown /r /t 5", shell=True)
    elif action == "sleep":
        run_command("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
