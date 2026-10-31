"""
DODO — skills/browser_control.py
Smart Browser Control Skill — Futuristic Edition

Features:
  - Smart URL resolution (site aliases, fuzzy matching, auto-scheme)
  - Multi-engine search (Google, DuckDuckGo, Bing, YouTube, Reddit, GitHub, Wikipedia)
  - Targeted browser launch (Chrome, Edge, Firefox, or default)
  - Incognito / private-window mode
  - Bookmark manager (persistent JSON store)
  - Recent sites history (persistent, last 50)
  - Download directory opener
  - Deep-link shortcuts (Gmail Compose, GitHub repo search, Maps, Translate, etc.)
  - Fully type-safe, zero external dependencies beyond stdlib
"""

import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
import winreg
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dodo.browser")

# ── Paths ──────────────────────────────────────────────────────────────────────
_MEMORY_DIR   = Path(__file__).parent.parent / "memory"
_BOOKMARKS_FILE = _MEMORY_DIR / "bookmarks.json"
_HISTORY_FILE   = _MEMORY_DIR / "browser_history.json"
_MAX_HISTORY    = 50

# ── Known site aliases ─────────────────────────────────────────────────────────
SITE_MAP: dict[str, str] = {
    # Search & productivity
    "google":        "https://google.com",
    "gmail":         "https://mail.google.com",
    "google drive":  "https://drive.google.com",
    "google docs":   "https://docs.google.com",
    "google sheets": "https://sheets.google.com",
    "google slides": "https://slides.google.com",
    "google meet":   "https://meet.google.com",
    "google photos": "https://photos.google.com",
    "google maps":   "https://maps.google.com",
    "calendar":      "https://calendar.google.com",
    "keep":          "https://keep.google.com",
    # Social
    "youtube":       "https://youtube.com",
    "twitter":       "https://twitter.com",
    "x":             "https://x.com",
    "instagram":     "https://instagram.com",
    "facebook":      "https://facebook.com",
    "linkedin":      "https://linkedin.com",
    "reddit":        "https://reddit.com",
    "discord":       "https://discord.com/app",
    "whatsapp":      "https://web.whatsapp.com",
    "telegram":      "https://web.telegram.org",
    "snapchat":      "https://web.snapchat.com",
    # Dev
    "github":        "https://github.com",
    "gitlab":        "https://gitlab.com",
    "stackoverflow": "https://stackoverflow.com",
    "stackoverflow overflow": "https://stackoverflow.com",
    "npm":           "https://npmjs.com",
    "pypi":          "https://pypi.org",
    "docker hub":    "https://hub.docker.com",
    "vercel":        "https://vercel.com",
    "netlify":       "https://netlify.com",
    "replit":        "https://replit.com",
    "codesandbox":   "https://codesandbox.io",
    "codepen":       "https://codepen.io",
    "leetcode":      "https://leetcode.com",
    "hackerrank":    "https://hackerrank.com",
    "codeforces":    "https://codeforces.com",
    # Shopping
    "amazon":        "https://amazon.in",
    "flipkart":      "https://flipkart.com",
    "meesho":        "https://meesho.com",
    # Entertainment
    "netflix":       "https://netflix.com",
    "hotstar":       "https://hotstar.com",
    "prime":         "https://primevideo.com",
    "prime video":   "https://primevideo.com",
    "spotify":       "https://open.spotify.com",
    "soundcloud":    "https://soundcloud.com",
    "twitch":        "https://twitch.tv",
    # AI tools
    "chatgpt":       "https://chat.openai.com",
    "claude":        "https://claude.ai",
    "gemini":        "https://gemini.google.com",
    "perplexity":    "https://perplexity.ai",
    "copilot":       "https://copilot.microsoft.com",
    # News
    "bbc":           "https://bbc.com/news",
    "cnn":           "https://cnn.com",
    "ndtv":          "https://ndtv.com",
    "times of india":"https://timesofindia.com",
    "the hindu":     "https://thehindu.com",
    "techcrunch":    "https://techcrunch.com",
    "hackernews":    "https://news.ycombinator.com",
    "hacker news":   "https://news.ycombinator.com",
    # Reference
    "wikipedia":     "https://wikipedia.org",
    "wiki":          "https://wikipedia.org",
    "wolfram":       "https://wolframalpha.com",
    "translate":     "https://translate.google.com",
    # Finance
    "moneycontrol":  "https://moneycontrol.com",
    "zerodha":       "https://zerodha.com",
    "groww":         "https://groww.in",
}

# ── Search engines ─────────────────────────────────────────────────────────────
SEARCH_ENGINES: dict[str, str] = {
    "google":     "https://www.google.com/search?q={q}",
    "ddg":        "https://duckduckgo.com/?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "bing":       "https://www.bing.com/search?q={q}",
    "youtube":    "https://www.youtube.com/results?search_query={q}",
    "reddit":     "https://www.reddit.com/search/?q={q}",
    "github":     "https://github.com/search?q={q}",
    "wikipedia":  "https://en.wikipedia.org/w/index.php?search={q}",
    "amazon":     "https://www.amazon.in/s?k={q}",
    "flipkart":   "https://www.flipkart.com/search?q={q}",
    "stackoverflow": "https://stackoverflow.com/search?q={q}",
    "maps":       "https://www.google.com/maps/search/{q}",
    "images":     "https://www.google.com/search?q={q}&tbm=isch",
    "news":       "https://news.google.com/search?q={q}",
    "scholar":    "https://scholar.google.com/scholar?q={q}",
    "pypi":       "https://pypi.org/search/?q={q}",
    "npm":        "https://www.npmjs.com/search?q={q}",
    "translate":  "https://translate.google.com/?text={q}",
}

# ── Deep-link templates ────────────────────────────────────────────────────────
DEEP_LINKS: dict[str, str] = {
    "gmail compose":      "https://mail.google.com/mail/?view=cm",
    "gmail inbox":        "https://mail.google.com/mail/#inbox",
    "gmail sent":         "https://mail.google.com/mail/#sent",
    "new google doc":     "https://docs.new",
    "new google sheet":   "https://sheets.new",
    "new google slide":   "https://slides.new",
    "new replit":         "https://replit.com/new",
    "github new repo":    "https://github.com/new",
    "github profile":     "https://github.com/VishnuSwarnkar12",
    "youtube studio":     "https://studio.youtube.com",
    "youtube subscriptions": "https://youtube.com/feed/subscriptions",
    "youtube history":    "https://youtube.com/feed/history",
}


# ── Browser detection ──────────────────────────────────────────────────────────

def _detect_browser(name: str) -> Optional[str]:
    """Find the executable path for a named browser on Windows."""
    candidates: dict[str, list[str]] = {
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
        "firefox": [
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        ],
        "edge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "brave": [
            os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
        "opera": [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Opera\launcher.exe"),
        ],
    }
    name_lower = name.lower()
    for key, paths in candidates.items():
        if key in name_lower:
            for p in paths:
                if os.path.exists(p):
                    return p
    return None


def _get_default_browser() -> Optional[str]:
    """Read the default browser from Windows registry."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
        ) as key:
            prog_id = winreg.QueryValueEx(key, "ProgId")[0]

        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT,
            rf"{prog_id}\shell\open\command"
        ) as key:
            cmd = winreg.QueryValueEx(key, "")[0]
            # Extract the executable path from the command string
            match = re.match(r'"?([^"]+\.exe)"?', cmd)
            if match:
                path = match.group(1)
                if os.path.exists(path):
                    return path
    except Exception:
        pass
    return None


# ── Persistence helpers ────────────────────────────────────────────────────────

def _load_json(path: Path, default) -> any:
    try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning("Could not load %s: %s", path, e)
    return default


def _save_json(path: Path, data) -> None:
    _MEMORY_DIR.mkdir(exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Could not save %s: %s", path, e)


def _record_history(url: str, title: str = "") -> None:
    history = _load_json(_HISTORY_FILE, [])
    entry = {
        "url": url,
        "title": title or url,
        "visited_at": datetime.now().isoformat()
    }
    # Deduplicate: remove older entry for same URL
    history = [h for h in history if h.get("url") != url]
    history.insert(0, entry)
    _save_json(_HISTORY_FILE, history[:_MAX_HISTORY])


# ── URL resolution ─────────────────────────────────────────────────────────────

def resolve_url(raw: str) -> str:
    """
    Intelligently resolve a raw string to a full HTTPS URL.
    Priority: deep-links → site aliases → URL pattern → domain guess → Google fallback
    """
    text = raw.strip()
    if not text:
        return "https://google.com"

    text_lower = text.lower()

    # 1. Deep-link shortcut?
    for alias, url in DEEP_LINKS.items():
        if alias in text_lower:
            return url

    # 2. Site alias?
    if text_lower in SITE_MAP:
        return SITE_MAP[text_lower]

    # 3. Already a valid URL?
    if re.match(r"https?://", text):
        return text

    # 4. Looks like a domain (e.g. "github.com/user/repo")?
    if re.match(r"[\w\-]+\.(com|org|net|io|in|co|ai|dev|app|edu|gov|uk|de|fr)\b", text):
        return "https://" + text

    # 5. Fuzzy alias match (partial word match)
    for alias, url in SITE_MAP.items():
        if text_lower in alias or alias in text_lower:
            return url

    # 6. Fallback: Google search for it
    encoded = urllib.parse.quote_plus(text)
    return f"https://www.google.com/search?q={encoded}"


# ── Core launcher ──────────────────────────────────────────────────────────────

def _launch(url: str, browser_exe: Optional[str] = None,
            incognito: bool = False, new_window: bool = False) -> bool:
    """
    Launch a URL in the specified browser (or system default).
    Handles incognito and new-window flags per browser.
    """
    try:
        if browser_exe:
            exe_lower = browser_exe.lower()
            args = [browser_exe]

            if "chrome" in exe_lower or "brave" in exe_lower:
                if incognito:
                    args.append("--incognito")
                if new_window:
                    args.append("--new-window")
            elif "firefox" in exe_lower:
                if incognito:
                    args.extend(["-private-window"])
                elif new_window:
                    args.append("--new-window")
            elif "msedge" in exe_lower or "edge" in exe_lower:
                if incognito:
                    args.append("--inprivate")
                if new_window:
                    args.append("--new-window")

            args.append(url)
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # System default via 'start' command
            if incognito:
                # Try Chrome incognito as best-effort fallback
                chrome = _detect_browser("chrome") or _detect_browser("edge")
                if chrome:
                    return _launch(url, browser_exe=chrome, incognito=True)
            subprocess.Popen(f'start "" "{url}"', shell=True)

        time.sleep(0.4)
        return True
    except Exception as e:
        logger.error("Failed to launch URL '%s': %s", url, e)
        return False


# ── Public API ─────────────────────────────────────────────────────────────────

def open_url(url: str, browser: Optional[str] = None,
             incognito: bool = False, new_window: bool = False) -> bool:
    """
    Open a URL in the browser.
    `url` can be a full URL, domain, or known site name.
    `browser`: 'chrome', 'firefox', 'edge', 'brave', or None for default.
    """
    resolved = resolve_url(url)
    exe = _detect_browser(browser) if browser else None
    ok = _launch(resolved, browser_exe=exe, incognito=incognito, new_window=new_window)
    if ok:
        _record_history(resolved, url)
    return ok


def open_incognito(url: str = "https://google.com", browser: Optional[str] = None) -> str:
    """Open a URL in an incognito / private window."""
    resolved = resolve_url(url)
    exe = _detect_browser(browser) if browser else None
    if not exe:
        # Try to find any supported browser for incognito
        for b in ("chrome", "edge", "firefox", "brave"):
            exe = _detect_browser(b)
            if exe:
                break
    ok = _launch(resolved, browser_exe=exe, incognito=True)
    if ok:
        return f"🕵️ Opened incognito: {resolved}"
    return "❌ Couldn't open incognito window. Is Chrome/Edge/Firefox installed?"


def search(query: str, engine: str = "google") -> str:
    """
    Search the web using a specific engine.
    Supported: google, ddg, bing, youtube, reddit, github, wikipedia,
               amazon, flipkart, stackoverflow, maps, images, news, scholar,
               pypi, npm, translate
    """
    engine_key = engine.lower().strip()
    template = SEARCH_ENGINES.get(engine_key, SEARCH_ENGINES["google"])
    encoded = urllib.parse.quote_plus(query)
    url = template.format(q=encoded)
    ok = open_url(url)
    engine_name = engine_key.title()
    return f"🔍 Searching {engine_name} for: {query}" if ok else f"❌ Failed to open {engine_name}."


def google_search(query: str) -> bool:
    """Search Google. Kept for backward compatibility."""
    return open_url(f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}")


def youtube_search(query: str) -> bool:
    """Search YouTube. Kept for backward compatibility."""
    return open_url(f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}")


def open_incognito_search(query: str, engine: str = "google") -> str:
    """Search privately in an incognito window."""
    template = SEARCH_ENGINES.get(engine.lower(), SEARCH_ENGINES["google"])
    url = template.format(q=urllib.parse.quote_plus(query))
    return open_incognito(url)


# ── Smart open (the main voice-command entry point) ────────────────────────────

def smart_open(text: str, incognito: bool = False,
               browser: Optional[str] = None) -> str:
    """
    The intelligent entry point for all 'open X' commands.
    Handles: site names, URLs, deep-link shortcuts, and free-text (falls back to search).
    """
    text = text.strip()
    if not text:
        url = "https://google.com"
        label = "Google"
    else:
        text_lower = text.lower()
        # Deep-link?
        for alias, dl_url in DEEP_LINKS.items():
            if alias in text_lower:
                ok = open_url(dl_url, browser=browser, incognito=incognito)
                icon = "🕵️" if incognito else "🌐"
                return f"{icon} Opened {alias.title()}" if ok else f"❌ Failed to open {alias}"
        url = resolve_url(text)
        label = text

    ok = open_url(url, browser=browser, incognito=incognito)
    icon = "🕵️" if incognito else "🌐"
    return f"{icon} Opened {label}" if ok else f"❌ Failed to open {url}"


# ── Bookmarks ──────────────────────────────────────────────────────────────────

def add_bookmark(name: str, url: str) -> str:
    """Save a named bookmark."""
    bookmarks = _load_json(_BOOKMARKS_FILE, {})
    resolved = resolve_url(url) if not url.startswith("http") else url
    bookmarks[name.lower().strip()] = {
        "url": resolved,
        "name": name,
        "added_at": datetime.now().isoformat()
    }
    _save_json(_BOOKMARKS_FILE, bookmarks)
    return f"🔖 Saved bookmark '{name}' → {resolved}"


def open_bookmark(name: str) -> str:
    """Open a saved bookmark by name."""
    bookmarks = _load_json(_BOOKMARKS_FILE, {})
    name_lower = name.lower().strip()

    # Exact match
    if name_lower in bookmarks:
        entry = bookmarks[name_lower]
        ok = open_url(entry["url"])
        return f"🔖 Opening bookmark '{entry['name']}'" if ok else "❌ Failed to open bookmark."

    # Fuzzy match
    for key, entry in bookmarks.items():
        if name_lower in key or key in name_lower:
            ok = open_url(entry["url"])
            return f"🔖 Opening '{entry['name']}' (closest match)" if ok else "❌ Failed."

    return (f"❌ No bookmark named '{name}'. "
            f"Available: {', '.join(bookmarks.keys()) or 'none yet'}")


def list_bookmarks() -> str:
    """List all saved bookmarks."""
    bookmarks = _load_json(_BOOKMARKS_FILE, {})
    if not bookmarks:
        return "📭 No bookmarks saved yet. Say 'bookmark this as [name]' to add one."
    lines = ["🔖 Your bookmarks:"]
    for i, (key, entry) in enumerate(bookmarks.items(), 1):
        lines.append(f"  {i}. {entry['name']} → {entry['url']}")
    return "\n".join(lines)


def delete_bookmark(name: str) -> str:
    """Delete a bookmark by name."""
    bookmarks = _load_json(_BOOKMARKS_FILE, {})
    name_lower = name.lower().strip()
    if name_lower in bookmarks:
        del bookmarks[name_lower]
        _save_json(_BOOKMARKS_FILE, bookmarks)
        return f"🗑️ Deleted bookmark '{name}'."
    return f"❌ No bookmark named '{name}'."


# ── History ────────────────────────────────────────────────────────────────────

def get_history(limit: int = 10) -> str:
    """Return recently visited sites."""
    history = _load_json(_HISTORY_FILE, [])
    if not history:
        return "📭 No browser history recorded yet."
    lines = [f"🕓 Recent sites (last {min(limit, len(history))}):" ]
    for i, entry in enumerate(history[:limit], 1):
        ts = entry.get("visited_at", "")[:16].replace("T", " ")
        lines.append(f"  {i}. {entry.get('title', entry['url'])}  [{ts}]")
    return "\n".join(lines)


def open_last_site() -> str:
    """Re-open the most recently visited site."""
    history = _load_json(_HISTORY_FILE, [])
    if not history:
        return "❌ No history to go back to."
    entry = history[0]
    ok = open_url(entry["url"])
    return f"🔄 Reopened: {entry.get('title', entry['url'])}" if ok else "❌ Failed to reopen."


def clear_history() -> str:
    """Clear the DODO browser history log."""
    _save_json(_HISTORY_FILE, [])
    return "🗑️ Browser history cleared."


# ── Downloads ──────────────────────────────────────────────────────────────────

def open_downloads() -> str:
    """Open the Windows Downloads folder."""
    downloads = Path.home() / "Downloads"
    try:
        subprocess.Popen(["explorer", str(downloads)])
        return f"📂 Opened Downloads folder: {downloads}"
    except Exception as e:
        return f"❌ Couldn't open Downloads: {e}"


# ── Browser info ───────────────────────────────────────────────────────────────

def browser_info() -> str:
    """Report which browsers are installed on this system."""
    browsers = {
        "Google Chrome": "chrome",
        "Mozilla Firefox": "firefox",
        "Microsoft Edge": "edge",
        "Brave": "brave",
        "Opera": "opera",
    }
    found = []
    for name, key in browsers.items():
        exe = _detect_browser(key)
        if exe:
            found.append(f"  ✅ {name}: {exe}")
        else:
            found.append(f"  ❌ {name}: not found")

    default = _get_default_browser()
    default_str = f"\n🌐 Default: {default}" if default else "\n🌐 Default: (unknown)"
    return "🖥️ Installed browsers:\n" + "\n".join(found) + default_str


# ── Translate ──────────────────────────────────────────────────────────────────

def translate(text: str, target_lang: str = "en") -> str:
    """Open Google Translate with a pre-filled text."""
    lang_codes = {
        "hindi": "hi", "spanish": "es", "french": "fr", "german": "de",
        "japanese": "ja", "korean": "ko", "chinese": "zh-CN", "arabic": "ar",
        "portuguese": "pt", "russian": "ru", "italian": "it", "english": "en",
        "telugu": "te", "tamil": "ta", "marathi": "mr", "bengali": "bn",
        "gujarati": "gu", "punjabi": "pa", "urdu": "ur",
    }
    lang_code = lang_codes.get(target_lang.lower(), target_lang)
    encoded_text = urllib.parse.quote_plus(text)
    url = f"https://translate.google.com/?sl=auto&tl={lang_code}&text={encoded_text}&op=translate"
    ok = open_url(url)
    return f"🌍 Translating to {target_lang.title()}" if ok else "❌ Failed to open translator."


# ── Maps ───────────────────────────────────────────────────────────────────────

def open_maps(location: str, directions_from: Optional[str] = None) -> str:
    """Open Google Maps for a location, or get directions."""
    if directions_from:
        url = (f"https://www.google.com/maps/dir/"
               f"{urllib.parse.quote_plus(directions_from)}/"
               f"{urllib.parse.quote_plus(location)}")
        ok = open_url(url)
        return f"🗺️ Getting directions: {directions_from} → {location}" if ok else "❌ Failed."
    else:
        url = f"https://www.google.com/maps/search/{urllib.parse.quote_plus(location)}"
        ok = open_url(url)
        return f"📍 Opening maps: {location}" if ok else "❌ Failed to open Maps."
