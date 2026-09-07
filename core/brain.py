"""
DODO — core/brain.py
Intent classifier and task planner.
Handles English, Hindi, and Hinglish commands.
"""

import re

# ── Hinglish → English intent keyword map ─────────────────────────────────────
HINGLISH_MAP = {
    # Open
    "kholo": "open",      "chalu karo": "open",  "start karo": "open",
    "on karo": "open",    "launch karo": "open",

    # Close
    "band karo": "close", "close karo": "close", "bund karo": "close",
    "off karo": "close",  "band kar": "close",

    # Search
    "search karo": "search", "dhundho": "search", "dhundo": "search",
    "batao": "search",        "khojo": "search",

    # Play / Music
    "bajao": "play",      "chalao": "play",      "play karo": "play",

    # Volume
    "volume badhao": "volume up",   "aawaz badhao": "volume up",
    "volume badha": "volume up",    "loud karo": "volume up",
    "volume kam karo": "volume down", "aawaz kam karo": "volume down",
    "mute karo": "mute",             "chup karo": "mute",

    # System
    "lock karo": "lock",   "screen lock karo": "lock",
    "screenshot lo": "screenshot", "screenshot lelo": "screenshot",
    "battery batao": "battery",    "battery check karo": "battery",

    # Files
    "file kholo": "open file",    "folder banao": "create folder",
    "file dhundho": "find file",  "file dhundo": "find file",
}

# ── Site name map ──────────────────────────────────────────────────────────────
SITE_MAP = {
    "youtube": "https://youtube.com",
    "google":  "https://google.com",
    "github":  "https://github.com",
    "gmail":   "https://mail.google.com",
    "reddit":  "https://reddit.com",
    "twitter": "https://twitter.com",
    "x":       "https://x.com",
    "whatsapp":"https://web.whatsapp.com",
    "spotify": "https://open.spotify.com",
    "netflix": "https://netflix.com",
    "amazon":  "https://amazon.in",
    "flipkart":"https://flipkart.com",
    "linkedin":"https://linkedin.com",
    "instagram":"https://instagram.com",
}

# ── App name map ───────────────────────────────────────────────────────────────
APP_MAP = {
    "chrome":     "chrome",
    "google chrome": "chrome",
    "firefox":    "firefox",
    "edge":       "msedge",
    "notepad":    "notepad",
    "calculator": "calc",
    "file explorer": "explorer",
    "explorer":   "explorer",
    "task manager": "taskmgr",
    "cmd":        "cmd",
    "command prompt": "cmd",
    "powershell": "powershell",
    "vs code":    "code",
    "vscode":     "code",
    "word":       "winword",
    "excel":      "excel",
    "powerpoint": "powerpnt",
    "spotify":    "spotify",
    "discord":    "discord",
    "zoom":       "zoom",
    "teams":      "teams",
    "paint":      "mspaint",
    "vlc":        "vlc",
    "snipping tool": "snippingtool",
    "settings":   "ms-settings:",
    "control panel": "control",
}

# ── Intent patterns ───────────────────────────────────────────────────────────
# ORDER MATTERS: more specific patterns MUST come before generic ones.
# e.g. "open youtube" must match open_site, not open_app.
INTENT_PATTERNS = [
    # ── Exact system actions (no ambiguity) ──────────────────────────────────
    ("volume_up",      [r"volume up", r"increase volume", r"louder", r"turn up",
                        r"aawaz badhao", r"volume badhao"]),
    ("volume_down",    [r"volume down", r"decrease volume", r"quieter", r"turn down",
                        r"aawaz kam", r"volume kam"]),
    ("mute",           [r"\bmute\b", r"unmute"]),
    ("lock_screen",    [r"lock\s*(the)?\s*screen", r"\block\b"]),
    ("screenshot",     [r"screenshot", r"screen\s*shot", r"capture\s*(the)?\s*screen",
                        r"take\s+a?\s*ss", r"snap\s*screen"]),
    ("battery",        [r"battery", r"charge\s*(level|status|percent)?"]),
    ("restart",        [r"\brestart\b", r"reboot"]),
    ("sleep",          [r"\bsleep\s*(mode)?\b", r"hibernate"]),

    # ── Screen analysis ──────────────────────────────────────────────────────
    ("analyze_screen", [r"what('s| is) on\s*(my|the)?\s*screen", r"analyze.*screen",
                        r"describe.*screen", r"screen.*kya hai", r"read my screen",
                        r"look at\s*(my|the)?\s*screen"]),

    # ── Memory ───────────────────────────────────────────────────────────────
    ("remember",       [r"\bremember\b.*\bthat\b", r"yaad rakh"]),

    # ── Weather ──────────────────────────────────────────────────────────────
    ("weather",        [r"\bweather\b", r"\btemperature\b", r"\bmausam\b",
                        r"how (hot|cold|warm)", r"is it raining"]),

    # ── Reminders ────────────────────────────────────────────────────────────
    ("set_reminder",   [r"remind me", r"set\s*(a|the)?\s*reminder", r"set\s*(a|the)?\s*timer",
                        r"yaad dilana", r"reminder.*set", r"in\s+\d+\s*min"]),
    ("list_reminders", [r"(list|show|my|pending)\s*reminder"]),

    # ── Summarize (before web_search so "summarize X" doesn't become a search)
    ("summarize",      [r"\bsummarize\b", r"\bsummary\b", r"give me.*(summary|gist|overview)",
                        r"read this article", r"tldr", r"summarise"]),

    # ── Browser / Internet (BEFORE open_app!) ────────────────────────────────
    ("open_site",      [r"open\s+\w+\.(com|org|net|io|in|co)\b",
                        r"go\s+to\s+\w+", r"navigate\s+to",
                        r"open\s+(youtube|google|github|gmail|reddit|twitter|"
                        r"spotify|netflix|amazon|linkedin|instagram|whatsapp|"
                        r"flipkart|x|facebook|stackoverflow|chatgpt)\b"]),
    ("youtube_play",   [r"(play|search)\s+.*\bon\s+youtube\b",
                        r"\byoutube\b.*(play|search)",
                        r"(play|search)\s+.*\byoutube\b",
                        r"play\s+.+\s+on\s+youtube"]),
    ("web_search",     [r"\bsearch\b\s*(for|about)?\s+\w+",
                        r"\bgoogle\b\s*(for|about)?\s+\w+",
                        r"\blookup\b", r"\blook\s+up\b"]),

    # ── Files (BEFORE open_app — "create a file" != "open an app") ───────────
    ("create_file",    [r"create\s+(a\s+)?file", r"make\s+(a\s+)?file",
                        r"write\s+(a\s+)?(file|code|program|script)",
                        r"new\s+file", r"note\s+banao", r"file\s+banao",
                        r"create\s+.+\.(txt|py|c|cpp|java|js|html|css|json)\b"]),
    ("create_folder",  [r"create\s+(a\s+)?folder", r"make\s+(a\s+)?folder",
                        r"new\s+folder", r"folder\s+banao"]),
    ("read_file",      [r"read\s+(the\s+)?file", r"show\s+(the\s+)?file",
                        r"what('s| is) in\s+(the\s+)?file", r"open\s+and\s+read"]),
    ("open_file",      [r"open\s+(the\s+)?file\s+\w+", r"open\s+\w+\.\w{1,5}\b"]),
    ("find_file",      [r"find\s+(the\s+)?(file|document)", r"search\s+(for\s+)?(file|document)",
                        r"where\s+is\s+(the\s+)?file", r"locate\s+"]),
    ("delete_file",    [r"delete\s+(the\s+)?(file|folder)", r"remove\s+(the\s+)?(file|folder)"]),

    # ── Device ───────────────────────────────────────────────────────────────
    ("bluetooth_on",   [r"bluetooth\s+on", r"turn\s+on\s+bluetooth", r"enable\s+bluetooth"]),
    ("bluetooth_off",  [r"bluetooth\s+off", r"turn\s+off\s+bluetooth", r"disable\s+bluetooth"]),
    ("wifi_on",        [r"wifi\s+on", r"turn\s+on\s+wifi", r"enable\s+wifi"]),
    ("wifi_off",       [r"wifi\s+off", r"turn\s+off\s+wifi", r"disable\s+wifi"]),

    # ── Time / date (instant, no LLM) ────────────────────────────────────────
    ("time",           [r"what\s*(is\s+the\s+)?time", r"kitne\s+baje", r"current\s+time"]),
    ("date",           [r"what('s| is)\s*(the\s+)?date", r"today('s|s)?\s+date", r"aaj.*date"]),

    # ── Apps (LAST — generic catch-all for "open X") ─────────────────────────
    ("close_app",      [r"close\s+\w+", r"kill\s+\w+", r"stop\s+\w+", r"exit\s+\w+",
                        r"quit\s+\w+"]),
    ("open_app",       [r"open\s+\w+", r"launch\s+\w+", r"start\s+\w+"]),

    # ── Chat (greetings, questions, everything else) → Groq ──────────────────
    ("ollama_chat",    [r"\bhello\b", r"\bhi\b", r"\bhey\b", r"\bnamaste\b",
                        r"how are you", r"kaisa hai", r"kaise ho",
                        r"\bjoke\b", r"make me laugh",
                        r"\bthanks\b", r"\bthank you\b",
                        r"\bbye\b", r"\bgoodbye\b",
                        r"tell me", r"explain", r"what is", r"what are",
                        r"who is", r"who are", r"why\b", r"how\b", r"can you",
                        r"mujhe batao", r"bolo", r"samjhao",
                        r"write\s+.*(code|program)", r"help me",
                        r"give me", r"show me", r"teach me"]),
]


def normalize(text: str) -> str:
    """Lowercase, strip, replace Hinglish phrases."""
    text = text.lower().strip()
    for hindi, english in HINGLISH_MAP.items():
        text = text.replace(hindi, english)
    return text


def detect_language(text: str) -> str:
    """Rough language detection."""
    hindi_words = {"karo", "kholo", "band", "badhao", "kam", "batao",
                   "mujhe", "aapka", "yaar", "bhai", "kal", "aj", "aaj",
                   "kaise", "kaisa", "chahiye", "sunao", "dekho"}
    words = set(text.lower().split())
    if words & hindi_words:
        return "hinglish"
    return "en"


def _split_compound(text: str) -> list[str]:
    """Split compound commands on 'and', 'then', 'also', 'aur' (Hinglish)."""
    parts = re.split(r"\b(?:and then|and also|and|then|also|aur|phir)\b", text)
    return [p.strip() for p in parts if p.strip()]


def classify(text: str) -> dict:
    """
    Classify a command and return a structured task plan.
    Supports multi-step compound commands (e.g., "take a screenshot and open notepad").
    Returns: {"intent": str, "plan": list, "language": str}
    """
    lang = detect_language(text)

    # Check for compound commands
    parts = _split_compound(text)
    if len(parts) > 1:
        combined_plan = []
        first_intent = "unknown"
        for part in parts:
            sub_result = _classify_single(part, text)
            if first_intent == "unknown":
                first_intent = sub_result["intent"]
            combined_plan.extend(sub_result["plan"])
        return {"intent": first_intent, "plan": combined_plan, "language": lang, "raw": text}

    return _classify_single(text, text)


def _classify_single(text: str, raw: str) -> dict:
    """Classify a single (non-compound) command."""
    lang   = detect_language(text)
    norm   = normalize(text)
    intent = "unknown"
    plan   = []

    # Match intent patterns
    for intent_name, patterns in INTENT_PATTERNS:
        for pat in patterns:
            if re.search(pat, norm):
                intent = intent_name
                break
        if intent != "unknown":
            break

    # ── Build plan based on intent ───────────────────────────────────────────
    if intent == "open_app":
        app = _extract_app(norm)
        plan = [{"type": "system_command", "action": "open_app", "value": app}]

    elif intent == "close_app":
        app = _extract_app(norm)
        plan = [{"type": "system_command", "action": "close_app", "value": app}]

    elif intent == "volume_up":
        plan = [{"type": "system_command", "action": "volume_up", "value": 10}]

    elif intent == "volume_down":
        plan = [{"type": "system_command", "action": "volume_down", "value": 10}]

    elif intent == "mute":
        plan = [{"type": "system_command", "action": "mute", "value": None}]

    elif intent == "lock_screen":
        plan = [{"type": "system_command", "action": "lock_screen", "value": None}]

    elif intent == "screenshot":
        plan = [{"type": "system_command", "action": "screenshot", "value": None}]

    elif intent == "battery":
        plan = [{"type": "system_command", "action": "battery", "value": None}]

    elif intent in ("restart", "sleep"):
        plan = [{"type": "system_command", "action": intent, "value": None}]

    elif intent == "open_site":
        site = _extract_site(norm)
        plan = [{"type": "internet_action", "action": "open_site", "value": site}]

    elif intent == "youtube_play":
        query = _extract_youtube_query(norm)
        plan = [{"type": "internet_action", "action": "youtube_play", "value": query}]

    # ── New: Weather ─────────────────────────────────────────────────────────
    elif intent == "weather":
        city = _extract_city(norm)
        plan = [{"type": "weather", "action": "get_weather", "value": city}]

    # ── New: Reminders ───────────────────────────────────────────────────────
    elif intent == "set_reminder":
        text_val, minutes = _extract_reminder(norm)
        plan = [{"type": "reminder", "action": "set", "value": text_val, "minutes": minutes}]

    elif intent == "list_reminders":
        plan = [{"type": "reminder", "action": "list", "value": None}]

    # ── New: Web search ──────────────────────────────────────────────────────
    elif intent == "web_search":
        query = _extract_search_query(norm)
        plan = [{"type": "web_search", "action": "search", "value": query}]

    elif intent == "summarize":
        topic = re.sub(r"(?:summarize|summarise|summary|give me|tldr|gist|overview|of|the|a)\s*", "", norm).strip()
        plan = [{"type": "web_search", "action": "summarize", "value": topic}]

    # ── New: File operations ─────────────────────────────────────────────────
    elif intent == "create_file":
        fname = _extract_file_name(norm)
        plan = [{"type": "file_operation", "action": "create_file", "value": fname}]

    elif intent == "read_file":
        fname = _extract_file_name(norm)
        plan = [{"type": "file_operation", "action": "read_file", "value": fname}]

    elif intent == "open_file":
        fname = _extract_file_name(norm)
        plan = [{"type": "file_operation", "action": "open_file", "value": fname}]

    elif intent == "find_file":
        fname = _extract_file_name(norm)
        plan = [{"type": "file_operation", "action": "find_file", "value": fname}]

    elif intent == "create_folder":
        fname = _extract_file_name(norm)
        plan = [{"type": "file_operation", "action": "create_folder", "value": fname}]

    elif intent == "delete_file":
        fname = _extract_file_name(norm)
        plan = [{"type": "file_operation", "action": "delete_file", "value": fname,
                 "requires_confirm": True}]

    # ── New: Screen analysis ─────────────────────────────────────────────────
    elif intent == "analyze_screen":
        plan = [{"type": "vision", "action": "analyze_screen", "value": raw}]

    # ── New: Memory ──────────────────────────────────────────────────────────
    elif intent == "remember":
        fact = _extract_fact(norm)
        plan = [{"type": "memory", "action": "remember", "value": fact}]

    elif intent in ("bluetooth_on", "bluetooth_off"):
        state = "on" if intent == "bluetooth_on" else "off"
        plan = [{"type": "device_control", "action": "bluetooth", "value": state}]

    elif intent in ("wifi_on", "wifi_off"):
        state = "on" if intent == "wifi_on" else "off"
        plan = [{"type": "device_control", "action": "wifi", "value": state}]

    elif intent in ("time", "date"):
        plan = [{"type": "small_talk", "action": intent, "value": None}]

    elif intent == "ollama_chat":
        plan = [{"type": "ollama_chat", "action": "chat", "value": raw}]

    else:
        # Unknown → send to Groq
        intent = "ollama_chat"
        plan = [{"type": "ollama_chat", "action": "chat", "value": raw}]

    return {"intent": intent, "plan": plan, "language": lang, "raw": raw}


# ── Extraction helpers ─────────────────────────────────────────────────────────

def _extract_app(text: str) -> str:
    for app_name, app_cmd in APP_MAP.items():
        if app_name in text:
            return app_cmd
    # Extract word after 'open'/'launch'/'start'/'close'
    m = re.search(r"(?:open|launch|start|close|kill|stop)\s+(.+)", text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _extract_site(text: str) -> str:
    for site_name, url in SITE_MAP.items():
        if site_name in text:
            return url
    # Try to find a .com URL
    m = re.search(r"([\w]+\.(?:com|org|net|io|in|co))", text)
    if m:
        return "https://" + m.group(1)
    return text.strip()


def _extract_search_query(text: str) -> str:
    text = re.sub(r"(?:search|google|find|lookup|look up)\s+(?:for\s+)?", "", text).strip()
    text = re.sub(r"on\s+google$", "", text).strip()
    return text or "latest news"


def _extract_youtube_query(text: str) -> str:
    text = re.sub(r"(?:play|search|on|youtube|bajao|chalao)\s*", " ", text)
    return text.strip() or "lofi music"


def _extract_file_name(text: str) -> str:
    text = re.sub(r"(?:open|find|delete|remove|create|make|new|read|show|write)\s+(?:a\s+)?(?:the\s+)?(?:file|folder)?\s*", "", text)
    return text.strip()


def _extract_city(text: str) -> str:
    """Extract city name from weather queries."""
    text = re.sub(r"(?:weather|temperature|mausam|how hot|how cold|is it raining)\s*(?:in|at|of|for)?\s*", "", text).strip()
    text = re.sub(r"(?:what's the|what is the|check|get|show)\s*", "", text).strip()
    return text if text else None


def _extract_reminder(text: str) -> tuple:
    """Extract reminder text and minutes from a reminder command.
    Returns (reminder_text, minutes).
    """
    # Try to find "in X minutes/hours"
    m = re.search(r"in\s+(\d+)\s*(min|minute|minutes|hour|hours|hr|hrs|sec|seconds)", text)
    minutes = 5  # default 5 minutes
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if "hour" in unit or "hr" in unit:
            minutes = num * 60
        elif "sec" in unit:
            minutes = max(1, num // 60)
        else:
            minutes = num
        # Remove the time part to get the reminder text
        text = text[:m.start()].strip()

    # Clean up the reminder text
    text = re.sub(r"(?:remind me|set.*reminder|set.*timer|yaad dilana)\s*(?:to|that)?\s*", "", text).strip()
    return (text or "Reminder", minutes)


def _extract_url(text: str) -> str:
    """Extract a URL from text."""
    m = re.search(r"(https?://\S+)", text)
    if m:
        return m.group(1)
    # Try to find a domain
    m = re.search(r"([\w]+\.(?:com|org|net|io|in|co)\S*)", text)
    if m:
        return "https://" + m.group(1)
    text = re.sub(r"(?:summarize|summary|read this article)\s*", "", text).strip()
    return text


def _extract_fact(text: str) -> str:
    """Extract a fact from 'remember that ...' commands."""
    m = re.search(r"remember\s+that\s+(.+)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"yaad\s+rakh\s+(.+)", text)
    if m:
        return m.group(1).strip()
    return text.strip()

