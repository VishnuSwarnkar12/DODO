"""
DODO — skills/self_updater.py
Adaptive Learning & Self-Update Engine.

Allows DODO to:
  1. Propose targeted, safe code improvements to its own source files.
  2. Apply changes via exact text replacement (dry-run diff shown first).
  3. Log every update attempt to memory/update_log.json.
  4. Push to GitHub ONLY after explicit user approval (UI dialog or voice).

Security model:
  - ALLOW_LIST  : only files inside skills/, core/, ui/, README.md
  - BLOCK_LIST  : config.json, memory/, .git/, any file outside project root
  - No arbitrary code execution — changes are text-replacement patches only.
  - All patches logged regardless of whether push is approved.
  - Git push is gated behind a modal dialog that blocks until user responds.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Project root (skills/ is a sibling of this file) ─────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Paths ─────────────────────────────────────────────────────────────────────
_UPDATE_LOG  = _PROJECT_ROOT / "memory" / "update_log.json"
_ALLOW_DIRS  = {"skills", "core", "ui"}          # relative to project root
_ALLOW_FILES = {"README.md"}                      # specific files allowed too
_BLOCK_PATHS = {
    "config.json", "config.example.json",
    "memory", ".git", ".env",
}

# ── Human-readable cap on patch size ─────────────────────────────────────────
_MAX_PATCH_BYTES = 32_768   # 32 KB per single patch


# ──────────────────────────────────────────────────────────────────────────────
# Security helpers
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_safe(filepath: str) -> Path:
    """
    Resolve filepath to an absolute Path inside _PROJECT_ROOT.
    Raises ValueError if the path escapes the root or touches blocked paths.
    """
    p = Path(filepath)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    p = p.resolve()

    # Must be inside project root
    try:
        rel = p.relative_to(_PROJECT_ROOT)
    except ValueError:
        raise ValueError(f"Path '{p}' is outside the project root — rejected.")

    parts = rel.parts
    if not parts:
        raise ValueError("Cannot modify the project root itself.")

    # Check block list
    for blocked in _BLOCK_PATHS:
        if parts[0] == blocked or str(rel) == blocked:
            raise ValueError(f"'{rel}' is in the protected block list — rejected.")

    # Check allow list
    top = parts[0]
    filename = rel.name
    if top not in _ALLOW_DIRS and filename not in _ALLOW_FILES:
        raise ValueError(
            f"'{rel}' is not in an allowed directory ({', '.join(_ALLOW_DIRS)}) "
            f"or allowed file ({', '.join(_ALLOW_FILES)}) — rejected."
        )

    if not p.exists():
        raise ValueError(f"File '{p}' does not exist.")
    if not p.is_file():
        raise ValueError(f"'{p}' is not a file.")

    return p


# ──────────────────────────────────────────────────────────────────────────────
# Update log
# ──────────────────────────────────────────────────────────────────────────────

def _load_log() -> list[dict]:
    """Load the update log from disk."""
    _UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not _UPDATE_LOG.exists():
        return []
    try:
        with open(_UPDATE_LOG, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_log(records: list[dict]) -> None:
    """Persist the update log."""
    _UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_UPDATE_LOG, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def log_update(record: dict) -> None:
    """Append a single update record to memory/update_log.json."""
    records = _load_log()
    records.append(record)
    # Keep only the 500 most recent entries
    if len(records) > 500:
        records = records[-500:]
    _save_log(records)


def get_update_history(limit: int = 20) -> list[dict]:
    """Return the most recent update records."""
    records = _load_log()
    return records[-limit:]


# ──────────────────────────────────────────────────────────────────────────────
# Diff / Patch engine
# ──────────────────────────────────────────────────────────────────────────────

def compute_diff(old_text: str, new_text: str, filename: str = "file") -> str:
    """Return a unified diff string (safe to display to the user)."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm=""
    )
    return "\n".join(diff)


def apply_patch(filepath: str, old_text: str, new_text: str) -> dict:
    """
    Safely apply a text-replacement patch to a file.

    Returns a dict:
      { "success": bool, "diff": str, "error": str | None,
        "filepath": str, "timestamp": str }
    """
    ts = datetime.now().isoformat()
    result: dict = {
        "success": False,
        "diff": "",
        "error": None,
        "filepath": filepath,
        "timestamp": ts,
    }

    # ── Validation ────────────────────────────────────────────────────────────
    if not old_text or not old_text.strip():
        result["error"] = "old_text must not be empty."
        return result

    if len(new_text.encode()) > _MAX_PATCH_BYTES:
        result["error"] = f"Patch exceeds max size ({_MAX_PATCH_BYTES} bytes)."
        return result

    try:
        safe_path = _resolve_safe(filepath)
    except ValueError as e:
        result["error"] = str(e)
        return result

    # ── Read file ─────────────────────────────────────────────────────────────
    try:
        original = safe_path.read_text(encoding="utf-8")
    except Exception as e:
        result["error"] = f"Cannot read file: {e}"
        return result

    # ── Check old_text actually exists ────────────────────────────────────────
    occurrences = original.count(old_text)
    if occurrences == 0:
        result["error"] = (
            "old_text not found in file — the file may have changed, "
            "or the snippet is not exact (check whitespace/indentation)."
        )
        return result
    if occurrences > 1:
        result["error"] = (
            f"old_text appears {occurrences} times in the file — "
            "be more specific to make the replacement unambiguous."
        )
        return result

    # ── Build patched content ─────────────────────────────────────────────────
    patched = original.replace(old_text, new_text, 1)

    # ── Generate diff for user review ─────────────────────────────────────────
    diff = compute_diff(original, patched, safe_path.name)
    result["diff"] = diff

    # ── Write to disk ─────────────────────────────────────────────────────────
    try:
        safe_path.write_text(patched, encoding="utf-8")
        result["success"] = True
    except Exception as e:
        result["error"] = f"Write failed: {e}"

    return result


# ──────────────────────────────────────────────────────────────────────────────
# User approval gate
# ──────────────────────────────────────────────────────────────────────────────

_approval_event  = threading.Event()
_approval_result: Optional[bool] = None


def _show_approval_dialog(title: str, message: str) -> bool:
    """
    Show a native Windows dialog (tkinter) asking the user to Approve or Deny.
    Blocks the calling thread until the user responds.
    Returns True if approved, False if denied/closed.
    """
    approved = [False]

    def _run():
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title(title)
        root.resizable(False, False)
        root.attributes("-topmost", True)

        # Center on screen
        root.update_idletasks()
        w, h = 560, 360
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{(sw - w)//2}+{(sh - h)//2}")

        # Styling
        bg = "#1a1a2e"
        fg = "#e0e0e0"
        accent = "#e94560"
        green  = "#00b894"
        root.configure(bg=bg)

        tk.Label(root, text="🔒  DODO Self-Update Request",
                 font=("Segoe UI", 13, "bold"), bg=bg, fg=accent).pack(pady=(18, 6))

        frame = tk.Frame(root, bg="#16213e", bd=0, relief="flat")
        frame.pack(padx=16, pady=6, fill="both", expand=True)

        txt = tk.Text(frame, wrap="word", font=("Consolas", 9),
                      bg="#16213e", fg=fg, relief="flat",
                      bd=0, padx=8, pady=8, state="normal", height=12)
        txt.insert("1.0", message)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)

        btn_frame = tk.Frame(root, bg=bg)
        btn_frame.pack(pady=10)

        def approve():
            approved[0] = True
            root.destroy()

        def deny():
            approved[0] = False
            root.destroy()

        tk.Button(btn_frame, text="✅  Approve & Push",
                  command=approve, bg=green, fg="white",
                  font=("Segoe UI", 10, "bold"),
                  relief="flat", padx=14, pady=6,
                  cursor="hand2").pack(side="left", padx=12)
        tk.Button(btn_frame, text="❌  Deny",
                  command=deny, bg=accent, fg="white",
                  font=("Segoe UI", 10, "bold"),
                  relief="flat", padx=14, pady=6,
                  cursor="hand2").pack(side="left", padx=12)

        root.protocol("WM_DELETE_WINDOW", deny)
        root.mainloop()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=300)   # 5-minute timeout before auto-deny
    return approved[0]


def request_push_approval(commit_message: str, diff_summary: str) -> bool:
    """
    Ask the user (via native dialog) whether DODO may push to GitHub.
    Returns True if approved, False if denied.
    """
    message = (
        f"DODO wants to push the following update to GitHub:\n\n"
        f"Commit message:\n  {commit_message}\n\n"
        f"Changes summary:\n{diff_summary}\n\n"
        "⚠  Review carefully. Clicking Approve will run:\n"
        "   git add -A  →  git commit  →  git push origin main\n\n"
        "This cannot be undone without manual git revert."
    )
    return _show_approval_dialog("DODO — GitHub Push Approval Required", message)


# ──────────────────────────────────────────────────────────────────────────────
# Git integration
# ──────────────────────────────────────────────────────────────────────────────

def _run_git(args: list[str], cwd: Path) -> tuple[bool, str]:
    """Run a git command. Returns (success, output_or_error)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
        out = (result.stdout + result.stderr).strip()
        return result.returncode == 0, out
    except FileNotFoundError:
        return False, "git executable not found. Is git installed and on PATH?"
    except subprocess.TimeoutExpired:
        return False, "git command timed out after 60 seconds."
    except Exception as e:
        return False, str(e)


def push_to_github(commit_message: str, diff_summary: str) -> dict:
    """
    Stage all changes, commit, and push — BUT only after user approves.

    Returns:
      { "pushed": bool, "approved": bool, "output": str, "error": str | None }
    """
    result: dict = {
        "pushed": False,
        "approved": False,
        "output": "",
        "error": None,
    }

    # ── Approval gate (blocks until user clicks) ──────────────────────────────
    approved = request_push_approval(commit_message, diff_summary)
    result["approved"] = approved
    if not approved:
        result["error"] = "User denied the push request."
        return result

    # ── git add ───────────────────────────────────────────────────────────────
    ok, out = _run_git(["add", "-A"], _PROJECT_ROOT)
    if not ok:
        result["error"] = f"git add failed: {out}"
        result["output"] = out
        return result

    # ── git commit ────────────────────────────────────────────────────────────
    safe_msg = commit_message.replace('"', "'")[:200]
    ok, out = _run_git(["commit", "-m", safe_msg], _PROJECT_ROOT)
    result["output"] += out + "\n"
    if not ok:
        if "nothing to commit" in out.lower():
            result["pushed"] = False
            result["error"] = "Nothing to commit — files may not have changed."
            return result
        result["error"] = f"git commit failed: {out}"
        return result

    # ── git push ──────────────────────────────────────────────────────────────
    ok, out = _run_git(["push", "origin", "main"], _PROJECT_ROOT)
    result["output"] += out
    if not ok:
        result["error"] = f"git push failed: {out}"
        return result

    result["pushed"] = True
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Convenience: apply + log in one call
# ──────────────────────────────────────────────────────────────────────────────

def self_update(filepath: str, old_text: str, new_text: str, reason: str) -> dict:
    """
    Apply a code patch, log it, and return a human-readable status dict.

    This is the main entry point called by core/agent.py.
    Does NOT push to GitHub — call push_to_github() separately after user approval.
    """
    patch_result = apply_patch(filepath, old_text, new_text)

    record: dict = {
        "timestamp":    datetime.now().isoformat(),
        "filepath":     filepath,
        "reason":       reason,
        "old_snippet":  old_text[:500],          # store first 500 chars only
        "new_snippet":  new_text[:500],
        "diff_head":    patch_result.get("diff", "")[:1000],
        "success":      patch_result["success"],
        "error":        patch_result.get("error"),
        "git_pushed":   False,
        "approved":     False,
    }
    log_update(record)

    return {
        "success":   patch_result["success"],
        "diff":      patch_result.get("diff", ""),
        "error":     patch_result.get("error"),
        "filepath":  filepath,
        "timestamp": record["timestamp"],
        "reason":    reason,
    }


def commit_and_push(commit_message: str) -> dict:
    """
    Request user approval then push to GitHub.
    Updates the latest log entry with push result.
    """
    # Build a short diff summary from recent log
    recent = get_update_history(5)
    changed_files = list({r["filepath"] for r in recent if r.get("success")})
    diff_summary = "Files modified:\n" + "\n".join(f"  • {f}" for f in changed_files)
    if not changed_files:
        diff_summary = "(no successfully patched files in recent history)"

    push_result = push_to_github(commit_message, diff_summary)

    # Update the most recent log entry with push info
    records = _load_log()
    if records:
        records[-1]["git_pushed"] = push_result.get("pushed", False)
        records[-1]["approved"]   = push_result.get("approved", False)
        _save_log(records)

    return push_result


# ──────────────────────────────────────────────────────────────────────────────
# Update history query
# ──────────────────────────────────────────────────────────────────────────────

def format_update_history(limit: int = 10) -> str:
    """Return a human-readable update history string."""
    records = get_update_history(limit)
    if not records:
        return "No self-updates logged yet."

    lines = [f"📋 DODO Self-Update History (last {len(records)} entries)\n"]
    for i, r in enumerate(reversed(records), 1):
        status = "✅ Applied" if r.get("success") else f"❌ Failed ({r.get('error', 'unknown')})"
        pushed = "🚀 Pushed to GitHub" if r.get("git_pushed") else ("🔒 Not pushed" if r.get("approved") is False else "⏳ Local only")
        ts = r.get("timestamp", "")[:16].replace("T", " ")
        lines.append(
            f"[{i}] {ts}  {status}  {pushed}\n"
            f"     File   : {r.get('filepath', '')}\n"
            f"     Reason : {r.get('reason', '')}\n"
        )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Read a file safely for inspection
# ──────────────────────────────────────────────────────────────────────────────

def read_own_file(filepath: str) -> str:
    """Read a DODO source file safely and return its contents (for LLM analysis)."""
    try:
        safe_path = _resolve_safe(filepath)
    except ValueError as e:
        return f"Error: {e}"
    try:
        content = safe_path.read_text(encoding="utf-8")
        # Cap at 8 KB to avoid overwhelming the LLM
        if len(content) > 8192:
            content = content[:8192] + "\n\n... [truncated at 8 KB] ..."
        return content
    except Exception as e:
        return f"Cannot read '{filepath}': {e}"


def list_own_files() -> str:
    """List all Python source files in skills/, core/, ui/ directories."""
    files = []
    for d in _ALLOW_DIRS:
        dir_path = _PROJECT_ROOT / d
        if dir_path.is_dir():
            for f in sorted(dir_path.glob("*.py")):
                rel = f.relative_to(_PROJECT_ROOT)
                files.append(str(rel).replace("\\", "/"))
    if not files:
        return "No source files found."
    return "DODO source files:\n" + "\n".join(f"  • {f}" for f in files)
