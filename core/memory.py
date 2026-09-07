"""
DODO — core/memory.py
Handles all local JSON memory: commands, preferences, config, facts, chat history.
"""

import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH        = os.path.join(BASE_DIR, "config.json")
COMMANDS_PATH      = os.path.join(BASE_DIR, "memory", "commands.json")
PREFERENCES_PATH   = os.path.join(BASE_DIR, "memory", "preferences.json")
FACTS_PATH         = os.path.join(BASE_DIR, "memory", "user_profile.json")
CHAT_HISTORY_PATH  = os.path.join(BASE_DIR, "memory", "chat_history.json")


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(data: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


# ── Preferences ───────────────────────────────────────────────────────────────

def load_preferences() -> dict:
    try:
        with open(PREFERENCES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_preferences(data: dict):
    with open(PREFERENCES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def get_user_name() -> str:
    prefs = load_preferences()
    return prefs.get("user_name", "Boss")

def update_session():
    prefs = load_preferences()
    prefs["last_seen"] = datetime.now().isoformat()
    prefs["session_count"] = prefs.get("session_count", 0) + 1
    save_preferences(prefs)


# ── Command Memory ─────────────────────────────────────────────────────────────

def load_commands() -> dict:
    try:
        with open(COMMANDS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"history": [], "frequency": {}}

def log_command(raw_text: str, intent: str):
    """Record a command to history and increment its frequency."""
    data = load_commands()

    entry = {
        "text": raw_text,
        "intent": intent,
        "time": datetime.now().isoformat()
    }
    data["history"] = ([entry] + data.get("history", []))[:100]  # keep last 100

    freq = data.get("frequency", {})
    freq[intent] = freq.get(intent, 0) + 1
    data["frequency"] = freq

    with open(COMMANDS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def get_top_commands(n: int = 5) -> list:
    """Return the n most frequently used intents."""
    data = load_commands()
    freq = data.get("frequency", {})
    sorted_cmds = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [cmd for cmd, _ in sorted_cmds[:n]]


# ── User Facts (Persistent Memory) ────────────────────────────────────────────

def load_facts() -> list:
    """Load stored user facts/preferences."""
    try:
        with open(FACTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("facts", [])
    except Exception:
        return []

def save_fact(fact: str):
    """
    Save a new fact about the user.
    Smart deduplication: if new fact contradicts an existing one
    (shares key topic words), REPLACE it instead of appending.
    This makes learning adaptive — corrections overwrite old info.
    """
    facts = load_facts()
    fact_lower = fact.lower()

    # Extract significant keywords from the new fact (skip common words)
    STOPWORDS = {"the","a","an","is","are","was","were","i","my","me","not","do",
                 "does","did","he","she","it","its","in","on","at","to","of","and",
                 "or","but","for","with","that","this","have","has","had","will","can",
                 "just","like","when","what","you","your","dodo","vishnu"}
    new_keywords = {w for w in fact_lower.split() if len(w) > 3 and w not in STOPWORDS}

    updated = False
    new_facts = []
    for existing in facts:
        existing_lower = existing.lower()
        existing_keywords = {w for w in existing_lower.split() if len(w) > 3 and w not in STOPWORDS}
        overlap = new_keywords & existing_keywords
        # If >40% keyword overlap → this fact is about the same topic → replace it
        if overlap and len(overlap) / max(len(new_keywords), 1) > 0.4:
            new_facts.append(fact)  # replace with corrected version
            updated = True
        else:
            new_facts.append(existing)

    if not updated:
        new_facts.append(fact)

    # Keep last 50 facts
    new_facts = new_facts[-50:]
    os.makedirs(os.path.dirname(FACTS_PATH), exist_ok=True)
    with open(FACTS_PATH, "w", encoding="utf-8") as f:
        json.dump({"facts": new_facts, "updated": datetime.now().isoformat()}, f, indent=4)

def get_facts_string() -> str:
    """Return all facts as a formatted string for system prompt injection.
    Always reads from disk so mid-session fact updates are immediately active."""
    facts = load_facts()  # Always fresh read — not cached
    if not facts:
        return ""
    return "Things you know about the user: " + "; ".join(facts)


# ── Chat History Persistence ──────────────────────────────────────────────────

def save_chat_history(history: list):
    """Persist conversation history to disk."""
    os.makedirs(os.path.dirname(CHAT_HISTORY_PATH), exist_ok=True)
    # Keep only last 20 messages
    with open(CHAT_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump({"history": history[-20:], "saved": datetime.now().isoformat()}, f, indent=4)

def load_chat_history() -> list:
    """Load persisted conversation history."""
    try:
        with open(CHAT_HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("history", [])
    except Exception:
        return []

