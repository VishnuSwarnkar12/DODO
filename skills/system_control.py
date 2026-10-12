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
_PYCAW_AVAILABLE = False
try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    _PYCAW_AVAILABLE = True
except Exception:
    _PYCAW_AVAILABLE = False


# ── Volume & Audio Subsystem ──────────────────────────────────────────────────

def _get_volume_interface():
    """Obtain the Windows audio endpoint volume interface across all pycaw versions."""
    if not _PYCAW_AVAILABLE:
        return None
    try:
        device = AudioUtilities.GetSpeakers()
        # Modern pycaw (AudioDevice wrapper with EndpointVolume attribute)
        if hasattr(device, "EndpointVolume") and device.EndpointVolume is not None:
            return device.EndpointVolume
        # Pycaw with underlying _dev IMMDevice
        if hasattr(device, "_dev") and hasattr(device._dev, "Activate"):
            interface = device._dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(interface, POINTER(IAudioEndpointVolume))
        # Legacy pycaw (where GetSpeakers directly returned IMMDevice)
        if hasattr(device, "Activate"):
            interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        pass
    return None


def get_current_volume() -> int:
    """Return the current system master volume as an integer percentage (0-100), or -1 if unavailable."""
    vol = _get_volume_interface()
    if vol:
        try:
            return int(round(vol.GetMasterVolumeLevelScalar() * 100))
        except Exception:
            pass
    return -1


def volume_up(step: int = 10) -> int:
    """Increase master volume by step percent."""
    vol = _get_volume_interface()
    if vol:
        try:
            current = vol.GetMasterVolumeLevelScalar()
            new_val = min(1.0, current + step / 100.0)
            vol.SetMasterVolumeLevelScalar(new_val, None)
            return int(round(new_val * 100))
        except Exception:
            pass
    for _ in range(max(1, step // 2)):
        pyautogui.press("volumeup")
    return -1


def volume_down(step: int = 10) -> int:
    """Decrease master volume by step percent."""
    vol = _get_volume_interface()
    if vol:
        try:
            current = vol.GetMasterVolumeLevelScalar()
            new_val = max(0.0, current - step / 100.0)
            vol.SetMasterVolumeLevelScalar(new_val, None)
            return int(round(new_val * 100))
        except Exception:
            pass
    for _ in range(max(1, step // 2)):
        pyautogui.press("volumedown")
    return -1


def toggle_mute() -> bool:
    """Toggle master audio mute state. Returns new mute state (True=muted, False=unmuted)."""
    vol = _get_volume_interface()
    if vol:
        try:
            muted = vol.GetMute()
            vol.SetMute(not muted, None)
            return not muted
        except Exception:
            pass
    pyautogui.press("volumemute")
    return False


def set_volume_exact(level: int) -> int:
    """Set volume to exact percentage (0-100). Uses pycaw for pixel-perfect fidelity."""
    level = max(0, min(100, level))
    vol = _get_volume_interface()
    if vol:
        try:
            vol.SetMasterVolumeLevelScalar(level / 100.0, None)
            return level
        except Exception:
            pass
    return -1


# ── Dynamic Application Discovery & Control ──────────────────────────────────

_APP_COMMANDS = {
    "chrome":        ["start", "chrome"],
    "google chrome": ["start", "chrome"],
    "msedge":        ["start", "msedge"],
    "edge":          ["start", "msedge"],
    "firefox":       ["start", "firefox"],
    "notepad":       ["notepad"],
    "calc":          ["calc"],
    "calculator":    ["calc"],
    "explorer":      ["explorer"],
    "taskmgr":       ["taskmgr"],
    "task manager":  ["taskmgr"],
    "cmd":           ["start", "cmd"],
    "terminal":      ["wt"],
    "windows terminal": ["wt"],
    "powershell":    ["start", "powershell"],
    "code":          ["code"],
    "vscode":        ["code"],
    "visual studio code": ["code"],
    "winword":       ["start", "winword"],
    "word":          ["start", "winword"],
    "excel":         ["start", "excel"],
    "powerpnt":      ["start", "powerpnt"],
    "powerpoint":    ["start", "powerpnt"],
    "mspaint":       ["mspaint"],
    "paint":         ["mspaint"],
    "snippingtool":  ["snippingtool"],
    "control":       ["control"],
    "control panel": ["control"],
    "spotify":       ["start", "spotify"],
    "discord":       ["start", "discord"],
    "vlc":           ["start", "vlc"],
    "whatsapp":      ["start", "whatsapp:"],
}


def _find_installed_shortcut_or_exe(name: str) -> str | None:
    """Search Windows Start Menu and Registry for installed application shortcuts or executables."""
    import winreg
    target = name.lower().strip()

    # 1. Search Start Menu programs
    roots = [
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
    ]
    for r in roots:
        if os.path.isdir(r):
            for lnk in glob.glob(os.path.join(r, "**", "*.lnk"), recursive=True):
                base = os.path.basename(lnk).lower().replace(".lnk", "")
                if target in base or base in target:
                    return lnk

    # 2. Search Windows Registry Uninstall table
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hkey, subpath in reg_paths:
        try:
            with winreg.OpenKey(hkey, subpath) as key:
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub_name) as sub:
                            dname, _ = winreg.QueryValueEx(sub, "DisplayName")
                            if target in dname.lower():
                                try:
                                    icon, _ = winreg.QueryValueEx(sub, "DisplayIcon")
                                    if icon and os.path.isfile(icon.split(",")[0]):
                                        return icon.split(",")[0]
                                except Exception:
                                    pass
                                try:
                                    loc, _ = winreg.QueryValueEx(sub, "InstallLocation")
                                    if loc and os.path.isdir(loc):
                                        for f in os.listdir(loc):
                                            if f.lower().endswith(".exe"):
                                                return os.path.join(loc, f)
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

    return None


def open_app(name: str) -> bool:
    """Open an application by friendly name, protocol, or dynamic system discovery."""
    clean = name.lower().strip()
    if not re.match(r"^[a-zA-Z0-9_.\-\s:]+$", clean):
        return False

    # 1. Check known command table
    cmd = _APP_COMMANDS.get(clean)
    if cmd:
        try:
            subprocess.Popen(cmd, shell=True)
            return True
        except Exception:
            pass

    # 2. Settings protocol
    if clean in ("ms-settings:", "settings", "windows settings"):
        try:
            os.startfile("ms-settings:")
            return True
        except Exception:
            pass

    # 3. Dynamic Start Menu or Registry resolution
    shortcut_or_exe = _find_installed_shortcut_or_exe(clean)
    if shortcut_or_exe and os.path.exists(shortcut_or_exe):
        try:
            os.startfile(shortcut_or_exe)
            return True
        except Exception:
            pass

    # 4. Generic start command
    try:
        os.startfile(clean)
        return True
    except Exception:
        pass

    try:
        subprocess.Popen(["start", clean], shell=True)
        return True
    except Exception:
        return False


def close_app(name: str) -> bool:
    """Force terminate an application by friendly name or process name."""
    clean = name.lower().strip()
    exe_map = {
        "chrome": "chrome", "google chrome": "chrome",
        "firefox": "firefox", "edge": "msedge", "msedge": "msedge",
        "notepad": "notepad", "calculator": "calc", "calc": "calc",
        "spotify": "spotify", "discord": "discord",
        "code": "code", "vscode": "code", "vlc": "vlc",
        "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
    }
    exe = exe_map.get(clean, clean)
    if exe.endswith(".exe"):
        exe = exe[:-4]
    if not re.match(r"^[a-zA-Z0-9_.\-\s]+$", exe):
        return False
    code, _, _ = run_command(["taskkill", "/F", "/IM", f"{exe}.exe"])
    return code == 0


# ── System Diagnostics & Power ────────────────────────────────────────────────

def get_system_stats() -> dict:
    """Retrieve real-time hardware performance metrics (CPU, RAM, Disk, Battery)."""
    cpu_pct = psutil.cpu_percent(interval=0.1)
    vmem = psutil.virtual_memory()
    disk = psutil.disk_usage("C:")
    bat = psutil.sensors_battery()

    battery_info = "Desktop power"
    if bat is not None:
        plugged = "plugged in" if bat.power_plugged else "on battery"
        battery_info = f"{int(bat.percent)}% ({plugged})"

    vol = get_current_volume()
    vol_info = f"{vol}%" if vol >= 0 else "Unknown"

    return {
        "cpu_percent": cpu_pct,
        "ram_percent": vmem.percent,
        "ram_used_gb": round(vmem.used / (1024**3), 1),
        "ram_total_gb": round(vmem.total / (1024**3), 1),
        "disk_percent": disk.percent,
        "disk_free_gb": round(disk.free / (1024**3), 1),
        "battery": battery_info,
        "volume": vol_info,
    }


def get_system_health_summary() -> str:
    """Format hardware performance metrics into a concise human-readable summary."""
    stats = get_system_stats()
    return (
        f"⚡ System Status:\n"
        f"• CPU Load: {stats['cpu_percent']}%\n"
        f"• RAM: {stats['ram_used_gb']} / {stats['ram_total_gb']} GB ({stats['ram_percent']}%)\n"
        f"• Disk (C:): {stats['disk_free_gb']} GB free ({stats['disk_percent']}% used)\n"
        f"• Battery: {stats['battery']}\n"
        f"• Master Volume: {stats['volume']}"
    )


def lock_screen():
    """Lock the Windows workstation."""
    ctypes.windll.user32.LockWorkStation()


def take_screenshot(mode: str = "fullscreen", delay: int = 0, copy_clipboard: bool = True, analyze: bool = False) -> str:
    """Take a screenshot using DODO's advanced screenshot engine. Returns the file path."""
    try:
        from skills.screenshot_engine import take_screenshot as se_take
        res = se_take(mode=mode, delay=delay, copy_clipboard=copy_clipboard, analyze=analyze)
        return res.path
    except Exception:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pics = os.path.join(os.path.expanduser("~"), "Pictures")
        path = os.path.join(pics, f"DODO_screenshot_{ts}.png")
        img = pyautogui.screenshot()
        img.save(path)
        return path


def get_battery() -> str:
    """Check battery level and charging state."""
    bat = psutil.sensors_battery()
    if bat is None:
        return "No battery detected — you're on desktop power."
    pct = int(bat.percent)
    plugged = "plugged in" if bat.power_plugged else "on battery"
    return f"Battery is at {pct}%, {plugged}."


def power_action(action: str):
    """Execute power actions: 'restart', 'sleep'."""
    if action == "restart":
        run_command("shutdown /r /t 5", shell=True)
    elif action == "sleep":
        run_command("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)

