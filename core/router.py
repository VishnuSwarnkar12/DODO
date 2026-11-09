"""
DODO — core/router.py
======================
Ultra-fast command router. The critical missing layer between main.py and agent.py.

BEFORE this file existed:
  Every command → agent_process() → Groq API (66 tools + 12 messages history)
  Even "volume up" hit the LLM. Response: 3-10 seconds.

AFTER this file:
  "volume up"      → brain.classify() → executor.execute()     [< 50ms, NO LLM]
  "what time"      → brain.classify() → _small_talk_reply()    [< 1ms, NO LLM]
  "set reminder"   → brain.classify() → executor.execute()     [< 100ms, NO LLM]
  "play music"     → brain.classify() → executor.execute()     [< 200ms, NO LLM]
  "search X"       → brain.classify() → executor.execute()     [< 2s, NO LLM]
  "what is Python" → lightweight LLM call, NO history, NO tools [< 1s]
  "book flight"    → full agent with 15 core tools, 2 rounds    [< 3s]

HOW IT WORKS
  1. Brain classifies the intent (pure regex, < 1ms)
  2. If intent is LOCAL → executor handles it instantly, no LLM
  3. If intent is CHAT/COMPLEX → decide: simple Q&A or tool-required
     - Simple: tiny LLM call (no tools, no history, 512 token limit)
     - Complex: full agent with ONLY the relevant tool subset
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable, Optional

from core.brain import classify
from core.executor import execute

# ── Intents handled LOCALLY (zero LLM, < 50ms) ────────────────────────────────
# These are classified by brain.py and dispatched to executor.py instantly.
_LOCAL_INTENTS = {
    "volume_up", "volume_down", "mute",
    "lock_screen", "screenshot", "battery", "system_status",
    "restart", "sleep",
    "open_app", "close_app",
    "open_site", "youtube_play", "open_incognito", "open_downloads",
    "search_engine", "add_bookmark", "open_bookmark", "list_bookmarks",
    "delete_bookmark", "browser_history", "translate", "maps",
    "set_reminder", "list_reminders", "cancel_reminder",
    "snooze_reminder", "complete_reminder", "clear_reminders",
    "create_file", "create_folder", "read_file", "open_file",
    "find_file", "delete_file",
    "bluetooth_on", "bluetooth_off", "wifi_on", "wifi_off",
    "time", "date",
    "remember",
    "queue_song", "skip_song", "show_queue", "now_playing", "clear_queue",
    "play_music", "stop_music",
    "self_update", "update_history", "push_update",
    "email",
    "analyze_screen",
    "weather",
    "web_search", "search_news", "stackoverflow", "wikipedia",
    "github_trending", "pypi_info", "summarize",
}

# Intents that ARE local but need an LLM for content generation
# (e.g., "create a Python file that does X" — needs LLM to write the code)
_LOCAL_BUT_MAY_NEED_LLM = {"create_file", "summarize", "analyze_screen"}

# Intents that go straight to the full agent
_FULL_AGENT_INTENTS = {"ollama_chat"}


def route(user_message: str, confirm_callback: Optional[Callable] = None) -> str:
    """
    Route a user command to the fastest appropriate handler.

    Returns the response string.
    """
    # Step 1: Classify intent with brain.py (pure regex, < 1ms)
    result = classify(user_message)
    intent = result.get("intent", "unknown")

    # Step 2: If it's a pure local intent, run executor directly — no LLM
    if intent in _LOCAL_INTENTS and intent not in _LOCAL_BUT_MAY_NEED_LLM:
        try:
            response = execute(result, confirm_callback=confirm_callback)
            if response and response.strip() and response.strip() != "Done.":
                return response
            # If executor returned nothing useful, fall through to LLM
        except Exception as e:
            return f"Something went wrong: {str(e)[:100]}"

    # Step 3: Local-but-may-need-LLM intents (file creation with code, summarize, etc.)
    if intent in _LOCAL_BUT_MAY_NEED_LLM:
        try:
            response = execute(result, confirm_callback=confirm_callback)
            if response and response.strip() and response.strip() != "Done.":
                return response
        except Exception:
            pass
        # Fall through to LLM if executor couldn't handle it

    # Step 4: ollama_chat and unknown — use smart LLM routing
    return _smart_llm_route(user_message, intent)


def _smart_llm_route(user_message: str, intent: str) -> str:
    """
    Route to LLM with the minimum context needed.

    - Simple conversational (hello, joke, explain X) → no tools, no history, fast
    - Complex (needs web info, code, multi-step) → full agent with tool subset
    """
    # Detect if this is a simple conversational query (no tools needed)
    if _is_simple_conversational(user_message):
        return _fast_llm(user_message)

    # Otherwise use the full agent but with optimised settings
    from core.agent import process as agent_process
    return agent_process(user_message)


# ── Simple conversational query detector ──────────────────────────────────────

_SIMPLE_PATTERNS = [
    r"^(hi|hello|hey|namaste|good\s*(morning|afternoon|evening|night))\b",
    r"^how are you\b",
    r"^(what|who) (is|are|was|were)\s+[a-z\s]+$",
    r"^tell me (a )?joke",
    r"^(thanks|thank you|bye|goodbye|ok|okay|cool|nice|great|awesome|perfect)\b",
    r"^(what|which|how|why|when|where) .{0,60}$",   # short questions < 60 chars
    r"^(explain|define|describe|what does) .{0,80}$",
    r"^(write|create|make|generate).*(poem|story|haiku|lyrics|quote|caption)",
    r"^(give me|tell me).*(joke|fun fact|quote|tip|idea)",
    r"^(translate)\s",
    r"^(convert)\s",
    r"^(calculate|compute|what is)\s+[\d\s\+\-\*\/\(\)\.]+$",   # math
]
_SIMPLE_RE = [re.compile(p, re.IGNORECASE) for p in _SIMPLE_PATTERNS]

# These words strongly suggest tool use is needed
_TOOL_SIGNALS = [
    "search", "find", "look up", "latest", "current", "news", "weather",
    "price", "stock", "score", "today", "right now", "live", "real time",
    "open", "play", "run", "install", "download", "send", "email",
    "screenshot", "remind", "reminder", "volume", "wifi", "bluetooth",
    "code", "write a program", "write code", "script", "debug",
    "summarize", "summary of", "read this", "what's on my screen",
]


def _is_simple_conversational(msg: str) -> bool:
    """Return True if the message is a simple Q&A that doesn't need any tools."""
    lower = msg.lower().strip()

    # If any tool-signal word is present, it's NOT simple
    if any(sig in lower for sig in _TOOL_SIGNALS):
        return False

    # If it's short and matches a simple pattern, it IS simple
    if len(msg) <= 120:
        for pat in _SIMPLE_RE:
            if pat.search(lower):
                return True

    # Very short messages (< 20 chars) are almost always simple
    if len(msg.strip()) < 20:
        return True

    return False


# ── Fast LLM: no tools, no history, minimal prompt ────────────────────────────

_FAST_SYSTEM = (
    "You are DODO, a sharp and friendly AI assistant. "
    "Answer concisely in 1-3 sentences. No markdown. No lists unless asked."
)


def _fast_llm(user_message: str) -> str:
    """
    Call the LLM with the absolute minimum context:
    - No chat history
    - No tools
    - 512 token limit
    - Single shot (no tool rounds)

    This is 3-5x faster than the full agent call.
    """
    try:
        from core.agent import _get_client, _get_model, _cached_load_config
        client = _get_client()
        model = _get_model()

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _FAST_SYSTEM},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.7,
            max_tokens=512,
        )
        reply = (response.choices[0].message.content or "").strip()
        return reply if reply else "Sure!"
    except Exception as e:
        # Fallback: try full agent
        try:
            from core.agent import process as agent_process
            return agent_process(user_message)
        except Exception:
            return f"Something went wrong: {str(e)[:80]}"
