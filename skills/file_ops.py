"""
DODO — skills/file_ops.py
File and folder operations on Windows.
"""

import os
import glob
import shutil


_ALLOWED_ROOTS = [
    os.path.expanduser('~'),  # User's home directory is fine
]
_BLOCKED_PATHS = [
    'config.json', '.env', '.ssh', '.gnupg', 'AppData/Local/Google/Chrome',
    'AppData/Local/Microsoft/Edge', '.git/config', 'memory/',
    'credentials', 'tokens', 'secrets',
]

def _is_safe_path(path: str) -> bool:
    try:
        real_path = os.path.realpath(path)
        real_lower = real_path.lower().replace('\\', '/')
        
        # Check root
        is_allowed = False
        for root in _ALLOWED_ROOTS:
            if real_path.startswith(os.path.realpath(root)):
                is_allowed = True
                break
        if not is_allowed:
            return False
            
        # Check blocked patterns
        for blocked in _BLOCKED_PATHS:
            if blocked.lower() in real_lower:
                return False
                
        return True
    except Exception:
        return False


_SEARCH_ROOTS = [
    os.path.expanduser("~\\Desktop"),
    os.path.expanduser("~\\Documents"),
    os.path.expanduser("~\\Downloads"),
    os.path.expanduser("~\\Pictures"),
    os.path.expanduser("~\\Music"),
    os.path.expanduser("~\\Videos"),
    os.path.expanduser("~"),
]


def find_file(name: str) -> list[str]:
    """Search common user directories for files matching name."""
    results = []
    pattern = f"*{name}*"
    for root in _SEARCH_ROOTS:
        try:
            for match in glob.glob(os.path.join(root, "**", pattern), recursive=True):
                results.append(match)
                if len(results) >= 5:
                    return results
        except PermissionError:
            continue
    return results


def open_file(path: str) -> bool:
    """Open a file with its default application."""
    if not _is_safe_path(path):
        return False
    ext = os.path.splitext(path)[1].lower()
    allowed_exts = {'.txt', '.md', '.pdf', '.py', '.js', '.ts', '.c', '.cpp', '.h', '.java', '.html', '.css', '.json', '.csv', '.xml', '.yaml', '.yml', '.png', '.jpg', '.jpeg', '.gif', '.mp3', '.mp4'}
    blocked_exts = {'.bat', '.exe', '.vbs', '.ps1', '.msi', '.cmd', '.com', '.scr', '.reg'}
    if ext in blocked_exts or ext not in allowed_exts:
        return False
    try:
        os.startfile(path)
        return True
    except Exception:
        return False


def create_folder(name: str, location: str = None) -> str:
    """Create a folder on the desktop by default."""
    if location is None:
        location = os.path.expanduser("~\\Desktop")
    path = os.path.join(location, name)
    if not _is_safe_path(path):
        return ""
    os.makedirs(path, exist_ok=True)
    return path


def delete_path(path: str) -> bool:
    """Delete a file or folder — REQUIRES prior confirmation."""
    try:
        if os.path.isfile(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        return True
    except Exception:
        return False


def create_file(name: str, content: str = "", location: str = None) -> str:
    """Create a text file with optional content. Defaults to Desktop."""
    if location is None:
        location = os.path.expanduser("~\\Desktop")
    # Add .txt extension if none provided
    if "." not in os.path.basename(name):
        name = name + ".txt"
    path = os.path.join(location, name)
    if not _is_safe_path(path):
        return ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path
    except Exception:
        return ""


def read_file(path: str) -> str:
    """Read and return the contents of a text file (first 3000 chars)."""
    if not _is_safe_path(path):
        return "Could not read file: unsafe path"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(3000)
    except Exception as e:
        return f"Could not read file: {e}"
