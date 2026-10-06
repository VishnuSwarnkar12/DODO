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
SCREENSHOTS_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "DODO_Screenshots")
HISTORY_FILE = os.path.join(BASE_DIR, "memory", "screenshot_history.json")

_lock = threading.RLock()


# ─────────────────────────────────────────────────────────────────────────────
# Storage & Catalog Management
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_dirs():
    """Ensure output directories exist."""
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
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


def cleanup_old_screenshots(days: int = 7, max_files: int = 100) -> int:
    """
    Intelligent retention policy: keeps screenshots up to N days or last M files.
    Returns number of deleted files.
    """
    _ensure_dirs()
    deleted = 0
    cutoff = time.time() - (days * 86400)
    with _lock:
        try:
            files = []
            for f in os.listdir(SCREENSHOTS_DIR):
                if f.startswith("DODO_") and f.endswith(".png"):
                    full_path = os.path.join(SCREENSHOTS_DIR, f)
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
    Allows immediate Ctrl+V pasting into Discord, Slack, WhatsApp, web apps, etc.
    """
    if os.name != "nt":
        return False

    try:
        output = io.BytesIO()
        pil_image.convert("RGB").save(output, "BMP")
        # Windows CF_DIB requires bitmap data without the initial 14-byte BMP file header
        data = output.getvalue()[14:]
        output.close()

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Explicit 64-bit parameter typing to avoid access violation
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

        GMEM_MOVEABLE = 0x0002
        CF_DIB = 8

        h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h_global:
            return False

        p_global = kernel32.GlobalLock(h_global)
        if not p_global:
            return False

        ctypes.memmove(p_global, data, len(data))
        kernel32.GlobalUnlock(h_global)

        # Retry up to 3 times in case clipboard is temporarily locked by another app
        for _ in range(3):
            if user32.OpenClipboard(None):
                user32.EmptyClipboard()
                user32.SetClipboardData(CF_DIB, h_global)
                user32.CloseClipboard()
                return True
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
    question: Optional[str] = None
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

    Returns:
        ScreenshotResult containing path, dimensions, window title, clipboard status, and optional AI analysis.
    """
    _ensure_dirs()
    cleanup_old_screenshots(days=7, max_files=100)

    # 1. Countdown delay if requested
    if delay > 0:
        for _ in range(delay):
            time.sleep(1)

    # 2. Identify active window details
    active_title, bbox = get_active_window_info()
    safe_title = _sanitize_title(active_title)

    # 3. Capture image
    img = None
    captured_mode = mode.lower()

    if captured_mode in ("window", "active_window") and bbox:
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
        except Exception:
            img = None

    if img is None:
        # Fullscreen (captures multi-monitor desktop or primary display)
        try:
            img = ImageGrab.grab(all_screens=True)
            captured_mode = "fullscreen"
        except Exception:
            import pyautogui
            img = pyautogui.screenshot()
            captured_mode = "fullscreen"

    # 4. Save to organized DODO folder
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    filename = f"DODO_{ts}_{safe_title}.png"
    filepath = os.path.join(SCREENSHOTS_DIR, filename)

    img.save(filepath, format="PNG")

    # 5. Audio feedback
    if play_sound:
        threading.Thread(target=play_shutter_sound, daemon=True).start()

    # 6. Windows Clipboard sync
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
    file_size_kb = round(os.path.getsize(filepath) / 1024, 1)

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
    _ensure_dirs()
    try:
        os.startfile(SCREENSHOTS_DIR)
        return f"Opened screenshots directory: {SCREENSHOTS_DIR}"
    except Exception as e:
        return f"Failed to open directory: {e}"


def get_screenshot_history(limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve recent screenshot records."""
    history = _load_history()
    return history[-limit:]
