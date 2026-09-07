"""
DODO — skills/utils.py
Shared helpers used across skill modules.
"""

import subprocess
import os
import sys


def run_command(cmd: list | str, shell: bool = False) -> tuple[int, str, str]:
    """Run a shell command. Returns (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True, timeout=10
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


def run_powershell(script: str) -> tuple[int, str, str]:
    """Execute a PowerShell script string."""
    return run_command(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    )


def is_process_running(name: str) -> bool:
    """Check if a process with given name is running."""
    code, out, _ = run_command(
        f'tasklist /FI "IMAGENAME eq {name}.exe" /FO CSV /NH', shell=True
    )
    return name.lower() in out.lower()


def get_base_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
