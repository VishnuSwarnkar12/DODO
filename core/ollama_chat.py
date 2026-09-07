"""
DODO — core/ollama_chat.py
Conversational AI via OpenAI SDK → Groq's OpenAI-compatible endpoint.
Same Groq API key, same free tier — industry-standard client, easy to switch providers.
"""

import base64
from openai import OpenAI
from core.memory import (
    load_config, get_user_name, get_facts_string,
    save_chat_history, load_chat_history
)

# System prompt that gives DODO its personality
_SYSTEM_PROMPT = """You are DODO, a powerful AI desktop assistant for Windows built for {name}.
Personality: Smart, witty, concise. Speak naturally. Keep answers to 1-3 sentences unless more detail is asked.
Language: Reply in whatever language the user uses (English, Hindi, Hinglish).

YOUR CAPABILITIES — things you CAN do (tell the user to ask via voice or text):
- Open/close any app: "open chrome", "close notepad"
- Open websites: "open youtube", "open github.com"
- Search Google/YouTube: "search for python tutorials", "play lofi on youtube"
- Control system: volume up/down, mute, lock screen, screenshot, battery check, restart, sleep
- Weather: "what's the weather in Delhi?"
- Reminders: "remind me to call mom in 30 minutes"
- Search the web for answers: "search for latest iPhone price"
- Summarize topics: "summarize The Alchemist"
- Create files: "create a file called notes.txt"
- Create folders: "create a folder called projects"
- Find files: "find file homework.pdf"
- Toggle WiFi/Bluetooth: "turn on bluetooth"
- Remember facts: "remember that my birthday is March 5"

IMPORTANT RULES:
- When user asks you to write code, DO write the code directly in your reply. You're smart enough.
- When user asks about current events or real-time info, say "Try asking: search for [topic]" so DODO's search skill handles it.
- When user asks to do something you can do, just confirm and guide them on the exact command.
- NEVER say "I can't" without offering an alternative approach.
- Be proactive. If user says "I need to study", suggest setting a reminder or opening relevant apps.
{facts}"""

# Conversation history (list of {role, content} dicts)
_history: list[dict] = []
_MAX_HISTORY = 20   # keep last 20 turns (10 exchanges)
_initialized = False

# OpenAI client (initialized lazily)
_client = None


def _ensure_initialized():
    """Load persisted chat history on first call."""
    global _history, _initialized
    if not _initialized:
        _history = load_chat_history()
        _initialized = True


def _get_client() -> OpenAI:
    """Return a cached OpenAI client pointed at Groq's compatible endpoint."""
    global _client
    if _client is None:
        cfg = load_config()
        api_key = cfg.get("groq_api_key", "")
        base_url = cfg.get("openai_base_url", "https://api.groq.com/openai/v1")
        if not api_key:
            raise RuntimeError(
                "Groq API key not found. Add your key to config.json as 'groq_api_key'."
            )
        _client = OpenAI(api_key=api_key, base_url=base_url)
    return _client


def _get_model() -> str:
    cfg = load_config()
    return cfg.get("groq_model", "llama-3.3-70b-versatile")


def chat(user_message: str) -> str:
    """
    Send a message and get a reply.
    Maintains conversation history for follow-up context.
    Injects user facts into the system prompt for personalization.
    Returns the assistant's reply as a string.
    """
    global _history
    _ensure_initialized()

    model = _get_model()
    name  = get_user_name()
    facts = get_facts_string()
    system = _SYSTEM_PROMPT.format(name=name, facts=facts)

    # Add user turn to history
    _history.append({"role": "user", "content": user_message})

    # Trim history to avoid context overflow
    if len(_history) > _MAX_HISTORY:
        _history = _history[-_MAX_HISTORY:]

    messages = [{"role": "system", "content": system}] + _history

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=256,
        )
        reply = response.choices[0].message.content.strip()

        # Add assistant reply to history
        _history.append({"role": "assistant", "content": reply})

        # Persist chat history to disk
        save_chat_history(_history)

        return reply

    except Exception as e:
        err_msg = str(e)
        if "api_key" in err_msg.lower() or "auth" in err_msg.lower():
            return "API key is invalid. Please update 'groq_api_key' in config.json."
        if "rate" in err_msg.lower() or "limit" in err_msg.lower():
            return "I've hit the rate limit. Give me a moment and try again."
        return f"I had trouble thinking just now. Error: {err_msg[:100]}"


def chat_with_image(user_message: str, image_path: str) -> str:
    """
    Send a message along with an image to a vision-capable model.
    Falls back to text-only chat if vision model is unavailable.
    """
    try:
        with open(image_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("utf-8")

        client = _get_client()
        cfg = load_config()
        vision_model = cfg.get("openai_vision_model", "meta-llama/llama-4-scout-17b-16e-instruct")

        response = client.chat.completions.create(
            model=vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": user_message},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{img_data}"
                    }}
                ]
            }],
            max_tokens=256,
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        # Fall back to text-only description
        return chat(f"{user_message} (Note: I tried to analyze an image but vision is not available: {str(e)[:60]})")


def clear_history():
    """Reset conversation history (e.g. on new session)."""
    global _history, _client, _initialized
    _history = []
    _client = None
    _initialized = False
    save_chat_history([])


def get_history() -> list[dict]:
    """Return the current conversation history."""
    _ensure_initialized()
    return list(_history)
