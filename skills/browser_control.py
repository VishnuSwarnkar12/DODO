"""
DODO — skills/browser_control.py
Opens URLs, performs Google and YouTube searches.
Uses subprocess 'start' command so the browser actually appears on screen.
"""

import subprocess
import urllib.parse
import time


def open_url(url: str) -> bool:
    """Open any URL in the default browser (foreground, visible on screen)."""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        # 'start' command opens in foreground on Windows
        subprocess.Popen(f'start "" "{url}"', shell=True)
        time.sleep(0.5)  # give Windows a moment to bring the window up
        return True
    except Exception:
        return False


def google_search(query: str) -> bool:
    """Search Google for a query."""
    encoded = urllib.parse.quote_plus(query)
    return open_url(f"https://www.google.com/search?q={encoded}")


def youtube_search(query: str) -> bool:
    """Search YouTube for a query."""
    encoded = urllib.parse.quote_plus(query)
    return open_url(f"https://www.youtube.com/results?search_query={encoded}")
