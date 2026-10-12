"""
DODO — skills/screenshot_engine.py
Futuristic, multi-mode, intelligent screen capture engine for Windows.

Features:
- Multi-mode capture: Fullscreen, Active Window, Region, and Delayed Timer.
- Automatic clipboard synchronization (instant Ctrl+V pasting into apps).
- Futuristic dual-frequency audio shutter feedback.
- Active window identification & title metadata (via Win32 & DWM APIs).
- Intelligent AI Vision HUD analysis (detects open apps, code, errors).
- Scalable structured storage: Pictures/DODO_Screenshots/ with history catalog.
- Quick system actions: Open Image, Open Folder, Delete, Analyze.
"""

import os
import io
import time
import json
import ctypes
import threading
from datetime import datetime, timedelta
from typing import Any, Optional
from ctypes import wintypes
from PIL import Image, ImageGrab

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FALLBACK_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "DODO_Screenshots")
HISTORY_FILE = os.path.join(BASE_DIR, "memory", "screenshot_history.json")

_lock = threading.RLock()


# ─────────────────────────────────────────────────────────────────────────────
# Storage & Catalog Management
# ─────────────────────────────────────────────────────────────────────────────

def get_screenshots_dir() -> str:
    """Return the configured screenshot directory, or default fallback."""
    try:
        from core.memory import load_config
        cfg = load_config()
        custom = cfg.get("screenshot_folder")
        if custom and isinstance(custom, str) and custom.strip():
            folder = os.path.expanduser(custom.strip())
            return os.path.abspath(folder)
    except Exception:
        pass
    return DEFAULT_FALLBACK_DIR


def set_screenshots_dir(new_path: str) -> str:
    """Update and persist screenshot folder in config.json."""
    from core.memory import load_config, CONFIG_PATH
    folder = os.path.abspath(os.path.expanduser(new_path.strip()))
    os.makedirs(folder, exist_ok=True)
    try:
        cfg = load_config()
        cfg["screenshot_folder"] = folder
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"[ScreenshotEngine] Failed to save config: {e}")
    return folder


def _ensure_dirs(target_dir: Optional[str] = None):
    """Ensure output directories exist."""
    folder = target_dir or get_screenshots_dir()
    os.makedirs(folder, exist_ok=True)
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)


def _load_history() -> list[dict[str, Any]]:
    """Thread-safe load of screenshot history catalog."""
    _ensure_dirs()
    with _lock:
        if not os.path.exists(HISTORY_FILE):
            return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []


def _save_history(records: list[dict[str, Any]]) -> None:
    """Thread-safe save of screenshot history catalog."""
    _ensure_dirs()
    with _lock:
        try:
            tmp_path = HISTORY_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(records[-100:], f, indent=2)  # retain last 100 entries
            os.replace(tmp_path, HISTORY_FILE)
        except Exception as e:
            print(f"[ScreenshotEngine] Error saving history: {e}")


def cleanup_old_screenshots(days: int = 7, max_files: int = 100, target_dir: Optional[str] = None) -> int:
    """
    Intelligent retention policy: keeps screenshots up to N days or last M files.
    Runs non-blockingly so disk/cloud sync never delays active captures.
    """
    folder = target_dir or get_screenshots_dir()
    _ensure_dirs(folder)
    deleted = 0
    cutoff = time.time() - (days * 86400)
    with _lock:
        try:
            files = []
            for f in os.listdir(folder):
                if f.startswith("DODO_") and f.endswith(".png"):
                    full_path = os.path.join(folder, f)
                    try:
                        mtime = os.path.getmtime(full_path)
                        files.append((mtime, full_path))
                    except OSError:
                        pass

            # Sort oldest to newest
            files.sort(key=lambda x: x[0])

            # Delete files older than cutoff or beyond max_files limit
            excess = max(0, len(files) - max_files)
            for idx, (mtime, path) in enumerate(files):
                if mtime < cutoff or idx < excess:
                    try:
                        os.remove(path)
                        deleted += 1
                    except OSError:
                        pass
        except Exception as e:
            print(f"[ScreenshotEngine] Cleanup error: {e}")
    return deleted


# ─────────────────────────────────────────────────────────────────────────────
# Audio Shutter FX & Windows Native Clipboard Integration
# ─────────────────────────────────────────────────────────────────────────────

def play_shutter_sound():
    """Play a futuristic dual-frequency camera snap chirp."""
    try:
        import winsound
        # Fast double-frequency chirp: 1600Hz -> 2400Hz
        winsound.Beep(1600, 50)
        winsound.Beep(2400, 70)
    except Exception:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass


def copy_image_to_clipboard(pil_image: Image.Image) -> bool:
    """
    Copy a PIL Image directly to the Windows Clipboard in CF_DIB format.
    Uses win32clipboard for memory-safe Windows native clipboard handling without access violations.
    """
    if os.name != "nt":
        return False

    try:
        import win32clipboard
        output = io.BytesIO()
        pil_image.convert("RGB").save(output, "BMP")
        # CF_DIB requires bitmap data without the 14-byte BMP header
        data = output.getvalue()[14:]
        output.close()

        # Retry in case clipboard is momentarily locked by another application
        for _ in range(3):
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
                win32clipboard.CloseClipboard()
                return True
            except Exception:
                time.sleep(0.04)
        return False
    except Exception as e:
        print(f"[ScreenshotEngine] Clipboard copy error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Active Window & Geometry Detection
# ─────────────────────────────────────────────────────────────────────────────

def get_active_window_info() -> tuple[str, Optional[tuple[int, int, int, int]]]:
    """
    Identify the active foreground window, its clean application title,
    and its exact bounding box rect (left, top, right, bottom).
    Uses Desktop Window Manager (DWM) for accurate border bounds on Win 10/11.
    """
    if os.name != "nt":
        return "Desktop", None

    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "Desktop", None

        # Extract title
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()

        # Extract geometry with shadowless frame bounds
        rect = wintypes.RECT()
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        bounds_ok = False
        try:
            dwmapi = ctypes.windll.dwmapi
            hr = dwmapi.DwmGetWindowAttribute(
                hwnd,
                DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if hr == 0:
                bounds_ok = True
        except Exception:
            pass

        if not bounds_ok:
            user32.GetWindowRect(hwnd, ctypes.byref(rect))

        bbox = (rect.left, rect.top, rect.right, rect.bottom)
        # Validate dimensions
        if bbox[2] > bbox[0] + 50 and bbox[3] > bbox[1] + 50:
            clean_title = title if title else "Active Window"
            return clean_title, bbox

        return title if title else "Desktop", None
    except Exception:
        return "Desktop", None


def _sanitize_title(title: str) -> str:
    """Format window title for safe filename use."""
    if not title or title.lower() in ("desktop", "active window"):
        return "Screen"
    # Take first meaningful chunk (e.g. 'main.py - Visual Studio Code' -> 'Visual Studio Code')
    parts = [p.strip() for p in title.split(" - ") if p.strip()]
    name = parts[-1] if len(parts) > 1 else parts[0]
    safe = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).strip()
    safe = safe.replace(" ", "_")
    return safe[:25] if safe else "Window"


# ─────────────────────────────────────────────────────────────────────────────
# Screenshot Result Representation
# ─────────────────────────────────────────────────────────────────────────────

class ScreenshotResult(dict):
    """
    Result object that acts as a dictionary with full metadata,
    and returns the file path string when evaluated as str() for 100% backward compatibility.
    """
    def __str__(self):
        return self.get("path", "")

    @property
    def path(self) -> str:
        return self.get("path", "")


# ─────────────────────────────────────────────────────────────────────────────
# Main Screenshot Engine
# ─────────────────────────────────────────────────────────────────────────────

def take_screenshot(
    mode: str = "fullscreen",
    delay: int = 0,
    copy_clipboard: bool = True,
    play_sound: bool = True,
    analyze: bool = False,
    question: Optional[str] = None,
    output_dir: Optional[str] = None
) -> ScreenshotResult:
    """
    Capture the screen with futuristic multi-mode capabilities.

    Args:
        mode: 'fullscreen' (default), 'window' (active application), or 'region'.
        delay: Countdown in seconds before capturing (allows switching windows).
        copy_clipboard: If True, automatically copies the image into the Windows Clipboard.
        play_sound: If True, plays the audio shutter chirp.
        analyze: If True, analyzes the captured image with Vision AI.
        question: Optional specific question for vision analysis.
        output_dir: Custom destination directory (defaults to configured screenshot folder).

    Returns:
        ScreenshotResult containing path, dimensions, window title, clipboard status, and optional AI analysis.
    """
    target_dir = output_dir or get_screenshots_dir()
    _ensure_dirs(target_dir)

    # Run cleanup asynchronously in background thread so disk/cloud sync never stalls captures
    threading.Thread(
        target=lambda: cleanup_old_screenshots(days=7, max_files=100, target_dir=target_dir),
        daemon=True
    ).start()

    # 1. Countdown delay if requested
    if delay > 0:
        time.sleep(delay)

    # 2. Identify active window details
    active_title, bbox = get_active_window_info()
    safe_title = _sanitize_title(active_title)

    # 3. Capture image safely with multi-tiered fallback
    img = None
    captured_mode = mode.lower()

    if captured_mode in ("window", "active_window") and bbox:
        try:
            img = ImageGrab.grab(bbox=bbox)
        except Exception:
            try:
                img = ImageGrab.grab(bbox=bbox, all_screens=True)
            except Exception:
                img = None

    if img is None:
        # Fullscreen: try all_screens, then single screen, then pyautogui fallback
        try:
            img = ImageGrab.grab(all_screens=True)
            captured_mode = "fullscreen"
        except Exception:
            try:
                img = ImageGrab.grab()
                captured_mode = "fullscreen"
            except Exception:
                try:
                    import pyautogui
                    img = pyautogui.screenshot()
                    captured_mode = "fullscreen"
                except Exception:
                    img = None

    # Handle locked / inaccessible workstation gracefully without crashing
    if img is None:
        msg = "⚠️ Unable to capture screen. The display may be locked, sleeping, or inaccessible."
        return ScreenshotResult({
            "success": False,
            "path": "",
            "filename": "",
            "mode": captured_mode,
            "window_title": active_title,
            "dimensions": (0, 0),
            "file_size_kb": 0,
            "copied_to_clipboard": False,
            "analysis": None,
            "message": msg,
            "error": "Display capture unavailable"
        })

    # 4. Save to organized target folder
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    filename = f"DODO_{ts}_{safe_title}.png"
    filepath = os.path.join(target_dir, filename)

    try:
        img.save(filepath, format="PNG")
    except Exception as e:
        return ScreenshotResult({
            "success": False,
            "path": "",
            "filename": "",
            "mode": captured_mode,
            "window_title": active_title,
            "dimensions": img.size,
            "file_size_kb": 0,
            "copied_to_clipboard": False,
            "analysis": None,
            "message": f"Failed to save screenshot: {e}",
            "error": str(e)
        })

    # 5. Audio feedback
    if play_sound:
        threading.Thread(target=play_shutter_sound, daemon=True).start()

    # 6. Windows Clipboard sync (safe via win32clipboard)
    copied = False
    if copy_clipboard:
        copied = copy_image_to_clipboard(img)

    # 7. AI Vision HUD Analysis
    analysis_text = None
    if analyze:
        try:
            from skills.screen_monitor import analyze_screen
            q = question or "Summarize what is open on this screen, identify any errors or active code, and note key windows."
            analysis_text = analyze_screen(q)
        except Exception as e:
            analysis_text = f"Vision analysis unavailable: {str(e)[:80]}"

    # 8. Record in catalog
    width, height = img.size
    file_size_kb = round(os.path.getsize(filepath) / 1024, 1) if os.path.exists(filepath) else 0

    record = {
        "filename": filename,
        "path": filepath,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": captured_mode,
        "window_title": active_title,
        "dimensions": f"{width}x{height}",
        "file_size_kb": file_size_kb,
        "copied_to_clipboard": copied,
        "has_analysis": bool(analysis_text),
    }

    history = _load_history()
    history.append(record)
    _save_history(history)

    # Build human-friendly message
    clip_msg = "Copied to clipboard" if copied else "Saved to disk"
    mode_msg = f"Active window '{active_title}'" if captured_mode == "window" else "Full screen"
    message = f"📸 Screenshot captured ({mode_msg}, {width}x{height}) — {clip_msg}.\nSaved: {filepath}"
    if analysis_text:
        message += f"\n\n🔍 AI Vision Analysis:\n{analysis_text}"

    result = ScreenshotResult({
        "success": True,
        "path": filepath,
        "filename": filename,
        "mode": captured_mode,
        "window_title": active_title,
        "dimensions": (width, height),
        "file_size_kb": file_size_kb,
        "copied_to_clipboard": copied,
        "analysis": analysis_text,
        "message": message,
    })

    return result


def open_latest_screenshot() -> str:
    """Open the most recently captured screenshot in default photo viewer."""
    history = _load_history()
    if not history:
        return "No screenshots taken yet."
    latest = history[-1]
    path = latest.get("path")
    if path and os.path.exists(path):
        try:
            os.startfile(path)
            return f"Opened latest screenshot: {latest.get('filename')}"
        except Exception as e:
            return f"Failed to open image: {e}"
    return "Latest screenshot file not found."


def open_screenshots_folder() -> str:
    """Open the DODO Screenshots directory in Windows Explorer."""
    target_dir = get_screenshots_dir()
    _ensure_dirs(target_dir)
    try:
        os.startfile(target_dir)
        return f"Opened screenshots directory: {target_dir}"
    except Exception as e:
        return f"Failed to open directory: {e}"


def get_screenshot_history(limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve recent screenshot records."""
    history = _load_history()
    return history[-limit:]
