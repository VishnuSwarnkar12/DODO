"""
DODO — skills/screen_monitor.py
Real-time screen monitoring and coding assistant.

Two modes:
  1. On-demand:  analyze_screen("what's the error?") → screenshot → vision AI → answer
  2. Auto-watch: watch_file("main.py") → monitors saves → flags errors proactively

Security:
  - Screenshots are temporary — deleted after analysis
  - File edits only on user-owned files
  - Vision calls go through Groq (same API as all DODO AI)
"""

from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional, Callable

import pyautogui

from core.memory import load_config

# ── State ─────────────────────────────────────────────────────────────────────

_watcher_thread: Optional[threading.Thread] = None
_watcher_stop = threading.Event()
_watcher_filepath: Optional[str] = None
_watcher_callback: Optional[Callable[[str], None]] = None   # called with message
_last_analysis_path: Optional[str] = None  # temp screenshot path for cleanup


# ── Screenshot Capture ────────────────────────────────────────────────────────

def capture_screen() -> str:
    """
    Take a screenshot and return it as a base64-encoded PNG string.
    The screenshot is kept in memory — never saved to disk permanently.
    """
    img = pyautogui.screenshot()

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def capture_screen_to_file() -> str:
    """Take a screenshot and save to a temp file. Returns the file path."""
    global _last_analysis_path

    # Clean up previous temp screenshot
    _cleanup_temp()

    tmp_dir = os.path.join(os.path.expanduser("~"), ".dodo_temp")
    os.makedirs(tmp_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(tmp_dir, f"screen_{ts}.png")

    img = pyautogui.screenshot()
    img.save(path)
    _last_analysis_path = path
    return path


def _cleanup_temp():
    """Delete the last temporary screenshot."""
    global _last_analysis_path
    if _last_analysis_path and os.path.exists(_last_analysis_path):
        try:
            os.remove(_last_analysis_path)
        except OSError:
            pass
    _last_analysis_path = None


# ── Vision Analysis ───────────────────────────────────────────────────────────

def _get_vision_client():
    """Create an OpenAI client pointed at Groq for vision calls."""
    from openai import OpenAI
    cfg = load_config()
    key = cfg.get("groq_api_key", "")
    base_url = cfg.get("openai_base_url", "https://api.groq.com/openai/v1")
    if not key:
        raise RuntimeError("Groq API key missing from config.json.")
    return OpenAI(api_key=key, base_url=base_url)


def analyze_screen(question: str = "What do you see on the screen?") -> str:
    """
    Analyze what the user is working on.
    
    Strategy (smart fallback):
      1. Try reading the active VS Code file directly — fast, free, no vision model.
         Send the code to the text LLM with the user's question.
      2. If VS Code isn't open, try screenshot + vision model.
      3. If vision model isn't available, explain what happened.
    
    Args:
        question: What to ask about the screen content.
        
    Returns:
        The AI's analysis as a string.
    """
    cfg = load_config()

    # ── Strategy 1: Read the VS Code file directly ────────────────────────
    editor_info = read_active_editor()
    if "error" not in editor_info:
        # Got the file — send code to text LLM (no vision model needed)
        filepath = editor_info["filepath"]
        content = editor_info["content"]
        language = editor_info["language"]
        filename = editor_info["filename"]
        lines = editor_info["lines"]

        # Truncate very long files to avoid token limits
        if len(content) > 8000:
            content = content[:8000] + "\n\n... (file truncated at 8000 chars)"

        # Also run a quick syntax check
        syntax_result = check_syntax(filepath)

        try:
            client = _get_vision_client()
            model = cfg.get("groq_model", "openai/gpt-oss-120b")

            response = client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "system",
                    "content": (
                        "You are DODO, an expert AI coding assistant. "
                        "The user is currently editing a file in VS Code. "
                        "You can see the full file content below. "
                        "Help them with their question — be concise and actionable. "
                        "If there are errors, point to exact line numbers."
                    )
                }, {
                    "role": "user",
                    "content": (
                        f"**File:** {filename} ({language}, {lines} lines)\n"
                        f"**Path:** {filepath}\n"
                        f"**Syntax check:** {syntax_result}\n\n"
                        f"```{language}\n{content}\n```\n\n"
                        f"**My question:** {question}"
                    )
                }],
                temperature=0.3,
                max_tokens=2048,
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            return f"I can see you're editing {filename} but hit an error analyzing it: {str(e)[:100]}"

    # ── Strategy 2: Screenshot + vision model ─────────────────────────────
    try:
        img_b64 = capture_screen()

        vision_model = cfg.get(
            "openai_vision_model",
            "meta-llama/llama-4-scout-17b-16e-instruct"
        )

        client = _get_vision_client()

        response = client.chat.completions.create(
            model=vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "You are DODO, an AI coding assistant looking at the user's screen. "
                        "Focus on code, errors, file names, and anything relevant. "
                        "Be concise and actionable.\n\n"
                        f"User's question: {question}"
                    )},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{img_b64}"
                    }}
                ]
            }],
            max_tokens=1024,
        )

        _cleanup_temp()
        return response.choices[0].message.content.strip()

    except Exception as e:
        _cleanup_temp()
        err = str(e).lower()

        # If vision failed but we know VS Code wasn't open, give helpful message
        if "modality" in err or "image" in err or "vision" in err or "not found" in err:
            return (
                "⚠️ Vision model isn't available on Groq right now. "
                "But I can still help! Open a file in VS Code and ask me again — "
                "I'll read the file directly without needing a screenshot."
            )
        if "rate" in err or "limit" in err:
            return "⚠️ Rate limited — try again in a moment."
        if "screen grab" in err:
            return (
                "⚠️ Screenshot failed (display not accessible). "
                "Open a file in VS Code and ask me again — I'll read the code directly."
            )
        return f"⚠️ Screen analysis failed: {str(e)[:120]}"


# ── Active Editor Detection ──────────────────────────────────────────────────

def read_active_editor() -> dict:
    """
    Detect the active VS Code file by parsing the window title.
    
    Returns:
        dict with keys: 'filepath', 'filename', 'content', 'language'
        or dict with 'error' key if detection fails.
    """
    try:
        # Get VS Code window title via PowerShell
        ps_cmd = (
            "Get-Process code -ErrorAction SilentlyContinue | "
            "Where-Object {$_.MainWindowTitle -ne ''} | "
            "Select-Object -First 1 -ExpandProperty MainWindowTitle"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=5
        )

        title = result.stdout.strip()
        if not title:
            return {"error": "VS Code is not open or has no active file."}

        # VS Code title format: "filename — folder — Visual Studio Code"
        # or: "filename - folder - Visual Studio Code"
        parts = re.split(r"\s*[—–-]\s*", title)
        if not parts:
            return {"error": f"Could not parse VS Code title: {title}"}

        filename = parts[0].strip()

        # Try to find the full path by searching common locations
        filepath = _find_file_path(filename, parts)

        if not filepath or not os.path.exists(filepath):
            return {
                "error": f"Found '{filename}' in VS Code title but can't locate the file on disk.",
                "filename": filename,
                "vs_code_title": title
            }

        # Read file content
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Detect language from extension
        ext = os.path.splitext(filename)[1].lower()
        lang_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".c": "c", ".cpp": "cpp", ".h": "c", ".java": "java",
            ".html": "html", ".css": "css", ".json": "json",
            ".md": "markdown", ".txt": "text", ".sh": "bash",
            ".ps1": "powershell", ".bat": "batch", ".rs": "rust",
            ".go": "go", ".rb": "ruby", ".php": "php",
        }

        return {
            "filepath": filepath,
            "filename": filename,
            "content": content,
            "language": lang_map.get(ext, "unknown"),
            "lines": len(content.splitlines()),
        }

    except subprocess.TimeoutExpired:
        return {"error": "Timed out detecting VS Code window."}
    except Exception as e:
        return {"error": f"Failed to detect active editor: {str(e)[:100]}"}


def _find_file_path(filename: str, title_parts: list) -> Optional[str]:
    """
    Try to resolve a filename from VS Code's title to an absolute path.
    Searches the workspace folder (from title) and common project paths.
    """
    search_dirs = []

    # If title has a folder part, try it
    if len(title_parts) >= 2:
        folder_name = title_parts[1].strip()
        # Common project locations
        for base in [
            os.path.expanduser("~"),
            os.path.join(os.path.expanduser("~"), "OneDrive", "AppData", "Desktop", "projects"),
            os.path.join(os.path.expanduser("~"), "Desktop"),
            os.path.join(os.path.expanduser("~"), "Documents"),
        ]:
            candidate = os.path.join(base, folder_name)
            if os.path.isdir(candidate):
                search_dirs.append(candidate)

    # Also search DODO's own directory
    dodo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    search_dirs.append(dodo_dir)

    # Walk each directory looking for the file
    for search_dir in search_dirs:
        for root, dirs, files in os.walk(search_dir):
            # Skip hidden dirs and common junk
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
                "__pycache__", "node_modules", ".git", "venv", "env"
            )]
            if filename in files:
                return os.path.join(root, filename)

    return None


# ── File Editing ──────────────────────────────────────────────────────────────

def edit_file(filepath: str, old_text: str, new_text: str) -> str:
    """
    Replace text in a file. VS Code auto-reloads when the file changes on disk.
    
    Args:
        filepath: Absolute path to the file.
        old_text: Exact text to find and replace.
        new_text: Replacement text.
        
    Returns:
        Success or error message.
    """
    if not os.path.exists(filepath):
        return f"❌ File not found: {filepath}"

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        if old_text not in content:
            # Try fuzzy match — strip whitespace differences
            old_stripped = " ".join(old_text.split())
            lines = content.splitlines()
            found = False
            for i, line in enumerate(lines):
                if old_stripped in " ".join(line.split()):
                    # Found a fuzzy match — use the exact line
                    content = content.replace(line, new_text.splitlines()[0] if new_text.splitlines() else "", 1)
                    found = True
                    break
            if not found:
                return f"❌ Could not find the text to replace in {os.path.basename(filepath)}."
        else:
            count = content.count(old_text)
            content = content.replace(old_text, new_text, 1)  # Replace first occurrence only

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return f"✅ Edited {os.path.basename(filepath)} — VS Code will auto-reload."

    except PermissionError:
        return f"❌ Permission denied: {filepath}"
    except Exception as e:
        return f"❌ Edit failed: {str(e)[:100]}"


def check_syntax(filepath: str) -> str:
    """
    Run a syntax check on a file based on its language.
    Returns error output or 'OK' if clean.
    """
    ext = os.path.splitext(filepath)[1].lower()

    commands = {
        ".py":  ["python", "-m", "py_compile", filepath],
        ".js":  ["node", "--check", filepath],
        ".ts":  ["npx", "tsc", "--noEmit", filepath],
        ".c":   ["gcc", "-fsyntax-only", filepath],
        ".cpp": ["g++", "-fsyntax-only", filepath],
    }

    cmd = commands.get(ext)
    if not cmd:
        return f"No syntax checker available for {ext} files."

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return "✅ Syntax OK — no errors found."
        else:
            error = (result.stderr or result.stdout or "Unknown error").strip()
            # Trim to reasonable length
            return f"⚠️ Syntax error:\n{error[:500]}"

    except FileNotFoundError:
        return f"Syntax checker not found. Is {cmd[0]} installed?"
    except subprocess.TimeoutExpired:
        return "Syntax check timed out."
    except Exception as e:
        return f"Syntax check failed: {str(e)[:100]}"


# ── File Watcher ──────────────────────────────────────────────────────────────

def watch_file(filepath: str, callback: Optional[Callable[[str], None]] = None,
               interval: float = 5.0) -> str:
    """
    Start watching a file for changes. When the file is saved (modified),
    run a syntax check and report via callback.
    
    Args:
        filepath: Absolute path to the file to watch.
        callback: Function to call with status messages (e.g., speech.speak).
        interval: Seconds between checks (default: 5).
        
    Returns:
        Confirmation message.
    """
    global _watcher_thread, _watcher_filepath, _watcher_callback

    if not os.path.exists(filepath):
        return f"❌ File not found: {filepath}"

    # Stop existing watcher
    stop_watching()

    _watcher_filepath = filepath
    _watcher_callback = callback
    _watcher_stop.clear()

    def _watch_loop():
        last_mtime = os.path.getmtime(filepath)
        last_size = os.path.getsize(filepath)
        fname = os.path.basename(filepath)

        while not _watcher_stop.is_set():
            _watcher_stop.wait(interval)
            if _watcher_stop.is_set():
                break

            try:
                cur_mtime = os.path.getmtime(filepath)
                cur_size = os.path.getsize(filepath)

                if cur_mtime != last_mtime or cur_size != last_size:
                    last_mtime = cur_mtime
                    last_size = cur_size

                    # File changed — run syntax check
                    result = check_syntax(filepath)
                    msg = f"[{fname}] {result}"

                    if _watcher_callback:
                        _watcher_callback(msg)
                    else:
                        print(f"[DODO Watch] {msg}")

            except FileNotFoundError:
                msg = f"❌ {fname} was deleted. Stopping watcher."
                if _watcher_callback:
                    _watcher_callback(msg)
                break
            except Exception:
                pass  # Ignore transient errors

    _watcher_thread = threading.Thread(target=_watch_loop, daemon=True)
    _watcher_thread.start()

    return f"👁️ Watching {os.path.basename(filepath)} — I'll flag errors when you save."


def stop_watching() -> str:
    """Stop the file watcher if running."""
    global _watcher_thread, _watcher_filepath

    if _watcher_thread and _watcher_thread.is_alive():
        _watcher_stop.set()
        _watcher_thread.join(timeout=3)
        name = os.path.basename(_watcher_filepath) if _watcher_filepath else "file"
        _watcher_thread = None
        _watcher_filepath = None
        return f"Stopped watching {name}."

    return "No file watcher is running."


def get_watch_status() -> str:
    """Check if a file watcher is active."""
    if _watcher_thread and _watcher_thread.is_alive() and _watcher_filepath:
        return f"👁️ Currently watching: {_watcher_filepath}"
    return "No file watcher active."
