"""
DODO — core/agent.py
Agentic AI brain via OpenAI SDK → Groq's OpenAI-compatible endpoint.
Same Groq API key, same free tier — but using the industry-standard OpenAI client.
"""

import json
import os
from datetime import datetime
from openai import OpenAI
from core.memory import (
    load_config, get_user_name, get_facts_string,
    save_chat_history, load_chat_history, save_fact, log_command
)

# ── Tool definitions (sent to Groq so it knows what it can do) ───────────────
# Keep descriptions SHORT to minimize token usage.

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the internet for real-time info, news, facts, prices, sports, current events. Returns snippets only. If user gives a direct URL or wants full page content, use read_url instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_url",
            "description": "Fetch and READ the actual content of any webpage — blog posts, news articles, sports scores, any URL. Use this when: user gives a direct URL, user wants latest blog post, user wants to read live content from a specific site. Much better than web_search for specific pages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to fetch and read"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city. Default: Delhi.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Open/launch a Windows application (chrome, notepad, vscode, etc.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "App name or executable"}
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "close_app",
            "description": "Close/kill a running application",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "App name to close"}
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Open a website in the default browser. Pass full URL or site name like 'youtube', 'google', 'github'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL or site name"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "play_music",
            "description": "Play a song or artist LOCALLY like Alexa — no browser needed. Uses yt-dlp to find audio and plays it via the system media player. Use for: 'play Ritviz', 'play song X', 'play some music'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Song name or artist to play"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stop_music",
            "description": "Stop the currently playing music.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_search",
            "description": "Open YouTube in browser and show search results (does NOT play audio). Use play_music instead if user wants to actually hear the song.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search on YouTube"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Capture the screen and save it to Pictures folder",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a file with content on the user's computer. Use for notes, code, scripts, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "File name with extension (e.g. notes.txt, calc.c, app.py)"},
                    "content": {"type": "string", "description": "File content to write"},
                    "location": {"type": "string", "description": "Folder path. Default: Desktop"}
                },
                "required": ["filename", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_folder",
            "description": "Create a new folder",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Folder name"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_file",
            "description": "Search for a file on the computer",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "File name or partial name to search"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read contents of a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path or name to read"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Set a reminder that will alert the user after N minutes",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Reminder text"},
                    "minutes": {"type": "integer", "description": "Minutes from now"}
                },
                "required": ["text", "minutes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "Show all pending reminders",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "volume_control",
            "description": "Adjust volume: 'up', 'down', or 'mute'",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["up", "down", "mute"]}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set system volume to an EXACT percentage (0-100). Use this instead of volume_control when user says 'set volume to X%' or 'volume to 50' or 'half volume'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "description": "Volume level 0-100"}
                },
                "required": ["level"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into whatever window/app is currently focused on screen. Use after opening a browser or app when user wants to type something into it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type"},
                    "press_enter": {"type": "boolean", "description": "Press Enter after typing. Default: true"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "system_action",
            "description": "System actions: 'lock' (lock screen), 'battery' (check battery), 'restart', 'sleep'",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["lock", "battery", "restart", "sleep"]}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Remember a fact about the user for future reference",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "Fact to remember"}
                },
                "required": ["fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_time_date",
            "description": "Get current time and/or date",
            "parameters": {
                "type": "object",
                "properties": {
                    "what": {"type": "string", "enum": ["time", "date", "both"]}
                },
                "required": ["what"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_device",
            "description": "Toggle WiFi or Bluetooth on/off",
            "parameters": {
                "type": "object",
                "properties": {
                    "device": {"type": "string", "enum": ["wifi", "bluetooth"]},
                    "state": {"type": "string", "enum": ["on", "off"]}
                },
                "required": ["device", "state"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command or compile+run code on the user's PC. Use for: running python scripts, compiling C code, executing terminal commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run (e.g. 'python script.py', 'gcc calc.c -o calc && calc', 'dir')"},
                    "working_dir": {"type": "string", "description": "Directory to run from. Default: Desktop"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Write and send an email via Gmail. Use when user says 'send email', 'email X about Y', 'write an email to'. The LLM should compose the subject and body based on context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to":      {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body":    {"type": "string", "description": "Full email body text"}
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_screen",
            "description": "Look at the user's screen RIGHT NOW using a screenshot + vision AI. Use when user says: 'what's on my screen', 'what's the error', 'look at this', 'help me with what I'm coding', 'read my screen'. Takes a screenshot and sends to vision model for analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "What to look for on screen, e.g. 'what error is showing?' or 'what code is this?'"}
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "watch_code",
            "description": "Start or stop watching a code file for changes. When the file is saved, DODO auto-checks syntax and flags errors. Use when user says: 'watch my code', 'monitor this file', 'check my code as I type'. Use action='stop' to stop watching.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["start", "stop", "status"], "description": "'start' to begin watching, 'stop' to stop, 'status' to check"},
                    "filepath": {"type": "string", "description": "File path to watch (only needed for 'start'). If not given, auto-detects VS Code active file."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_code",
            "description": "Edit code directly in a file. VS Code auto-reloads. Use when user says: 'fix line X', 'add a function', 'change this code', 'replace X with Y'. The edit is done by finding old_text and replacing with new_text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Absolute path to the file to edit. If not given, auto-detects VS Code active file."},
                    "old_text": {"type": "string", "description": "Exact text to find and replace in the file"},
                    "new_text": {"type": "string", "description": "New text to replace it with"}
                },
                "required": ["old_text", "new_text"]
            }
        }
    },
]

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are DODO, a powerful AI desktop assistant for Windows. User: {name}.
Be smart, concise, witty. Reply in the user's language (English/Hindi/Hinglish).
Keep answers short (1-3 sentences) unless asked for detail.
You have tools to control the computer, search the web, create files, etc. USE THEM.
When asked for news, facts, or real-time info — ALWAYS use web_search first.
When asked to write code — use create_file to actually save it, don't just show it.
When asked to summarize a book/topic — use web_search to find info, then summarize.
Be proactive. Chain multiple tools when needed.
NEVER say "I can't" — find a way using your tools.

CRITICAL — TOOL USAGE RULES:
- For "play [song]" or "play music" → use play_music tool. NEVER use run_command.
- For "send email" or "email someone" → use send_email tool. NEVER use run_command.
- For "open [website]" → use open_website tool. NEVER use run_command.
- For "open [app]" → use open_app tool. NEVER use run_command.
- run_command is ONLY for: running Python scripts, compiling C/C++ code, git commands, pip installs. Nothing else.
- NEVER open cmd/terminal unless the user explicitly asks to run code or a command.

CRITICAL — HONESTY RULES:
- NEVER claim to see, confirm, or verify any UI state (browser tabs, playing videos, open windows). You cannot see the screen.
- If you opened a URL, say "I've opened Gmail for you" — NEVER say "I can see the email is sent".
- Tool results are your ONLY source of truth.
- When the user corrects you → ALWAYS call remember_fact() with the correct info before responding.
{facts}"""

# ── Tool execution ────────────────────────────────────────────────────────────

def _execute_tool(name: str, args: dict) -> str:
    """Execute a tool and return the result as a string."""
    try:
        if name == "web_search":
            import skills.web_search as ws
            answer = ws.quick_answer(args["query"])
            if answer:
                return answer
            return ws.search(args["query"], max_results=6)

        elif name == "read_url":
            import skills.web_search as ws
            url = args["url"]
            if not url.startswith("http"):
                url = "https://" + url
            content = ws.summarize_url(url)
            return f"Page content from {url}:\n\n{content}"

        elif name == "get_weather":
            import skills.weather as w
            return w.get_weather(args.get("city"))

        elif name == "open_app":
            import skills.system_control as sc
            # Map common names
            APP_MAP = {
                "chrome": "chrome", "google chrome": "chrome", "browser": "chrome",
                "notepad": "notepad", "calculator": "calc", "calc": "calc",
                "vscode": "code", "vs code": "code", "visual studio code": "code",
                "explorer": "explorer", "file manager": "explorer",
                "cmd": "cmd", "command prompt": "cmd", "terminal": "cmd",
                "powershell": "powershell", "task manager": "taskmgr",
                "settings": "ms-settings:", "spotify": "spotify",
                "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
            }
            app = APP_MAP.get(args["app_name"].lower(), args["app_name"])
            ok = sc.open_app(app)
            return f"Opened {args['app_name']}." if ok else f"Couldn't open {args['app_name']}."

        elif name == "close_app":
            import skills.system_control as sc
            ok = sc.close_app(args["app_name"])
            return f"Closed {args['app_name']}." if ok else f"Couldn't close {args['app_name']}."

        elif name == "open_website":
            import skills.browser_control as bc
            SITE_MAP = {
                "youtube": "https://youtube.com", "google": "https://google.com",
                "github": "https://github.com", "gmail": "https://mail.google.com",
                "reddit": "https://reddit.com", "twitter": "https://twitter.com",
                "x": "https://x.com", "instagram": "https://instagram.com",
                "whatsapp": "https://web.whatsapp.com", "linkedin": "https://linkedin.com",
                "spotify": "https://open.spotify.com", "netflix": "https://netflix.com",
                "amazon": "https://amazon.in", "flipkart": "https://flipkart.com",
                "facebook": "https://facebook.com", "chatgpt": "https://chat.openai.com",
                "stackoverflow": "https://stackoverflow.com",
            }
            url = args["url"]
            url = SITE_MAP.get(url.lower(), url)
            if not url.startswith("http"):
                url = "https://" + url
            bc.open_url(url)
            return f"Opened {url}"

        elif name == "play_music":
            import skills.music_player as mp
            return mp.play_song(args["query"])

        elif name == "stop_music":
            import skills.music_player as mp
            return mp.stop_music()

        elif name == "youtube_search":
            import skills.browser_control as bc
            bc.youtube_search(args["query"])
            return f"Searching YouTube for: {args['query']}"

        elif name == "take_screenshot":
            import skills.system_control as sc
            path = sc.take_screenshot()
            return f"Screenshot saved to {path}"

        elif name == "create_file":
            import skills.file_ops as fo
            path = fo.create_file(
                args["filename"],
                args.get("content", ""),
                args.get("location")
            )
            return f"File created: {path}" if path else "Failed to create file."

        elif name == "create_folder":
            import skills.file_ops as fo
            path = fo.create_folder(args["name"])
            return f"Folder created: {path}"

        elif name == "find_file":
            import skills.file_ops as fo
            results = fo.find_file(args["name"])
            if results:
                return "Found: " + ", ".join(results[:5])
            return f"No files found matching '{args['name']}'."

        elif name == "read_file":
            import skills.file_ops as fo
            path = args["path"]
            # If it's just a name, try to find it first
            if not os.path.exists(path):
                results = fo.find_file(path)
                if results:
                    path = results[0]
                else:
                    return f"File not found: {args['path']}"
            return fo.read_file(path)

        elif name == "set_reminder":
            import skills.reminders as rm
            return rm.set_reminder(args["text"], args["minutes"])

        elif name == "list_reminders":
            import skills.reminders as rm
            return rm.list_reminders()

        elif name == "volume_control":
            import skills.system_control as sc
            action = args["action"]
            if action == "up":
                vol = sc.volume_up()
                return f"Volume up to {vol}%."
            elif action == "down":
                vol = sc.volume_down()
                return f"Volume down to {vol}%."
            elif action == "mute":
                sc.toggle_mute()
                return "Toggled mute."

        elif name == "set_volume":
            import skills.system_control as sc
            level = max(0, min(100, int(args["level"])))
            vol = sc.set_volume_exact(level)
            return f"Volume set to {vol}%."

        elif name == "type_text":
            import pyautogui, time
            time.sleep(1.2)  # wait for window to be focused/ready
            text = args["text"]
            # Use clipboard paste instead of typewrite — supports Unicode/Hindi/emoji
            try:
                import pyperclip
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
            except ImportError:
                # Fallback: typewrite only works with ASCII
                pyautogui.typewrite(text, interval=0.04)
            if args.get("press_enter", False):
                time.sleep(0.2)
                pyautogui.press("enter")
            return f"Typed: {text}"

        elif name == "system_action":
            import skills.system_control as sc
            action = args["action"]
            if action == "lock":
                sc.lock_screen()
                return "Screen locked."
            elif action == "battery":
                return sc.get_battery()
            elif action in ("restart", "sleep"):
                sc.power_action(action)
                return f"System will {action} now."

        elif name == "remember_fact":
            save_fact(args["fact"])
            return f"Remembered: {args['fact']}"

        elif name == "send_email":
            import skills.email_sender as em
            return em.send_email(args["to"], args["subject"], args["body"])

        elif name == "get_time_date":
            now = datetime.now()
            what = args.get("what", "both")
            if what == "time":
                return now.strftime("It's %I:%M %p.")
            elif what == "date":
                return now.strftime("Today is %A, %B %d, %Y.")
            else:
                return now.strftime("It's %I:%M %p on %A, %B %d, %Y.")

        elif name == "toggle_device":
            import skills.device_control as dc
            dev = args["device"]
            state = args["state"] == "on"
            if dev == "wifi":
                ok = dc.set_wifi(state)
            else:
                ok = dc.set_bluetooth(state)
            return f"{dev.title()} turned {args['state']}." if ok else f"Couldn't toggle {dev}."

        elif name == "run_command":
            import subprocess
            home = os.path.expanduser("~")
            # Check OneDrive Desktop first, then regular Desktop
            desktop = os.path.join(home, "OneDrive", "AppData", "Desktop")
            if not os.path.exists(desktop):
                desktop = os.path.join(home, "Desktop")
            cwd = args.get("working_dir", desktop)
            if not os.path.exists(cwd):
                cwd = desktop
            cmd = args["command"]
            try:
                # Run in a visible terminal window so user can see the output
                subprocess.Popen(
                    f'start cmd.exe /k "{cmd}"',
                    shell=True, cwd=cwd
                )
                return f"Running: {cmd} (terminal window opened)"
            except Exception as ex:
                return f"Failed to run command: {str(ex)[:100]}"

        elif name == "analyze_screen":
            import skills.screen_monitor as sm
            return sm.analyze_screen(args.get("question", "What do you see on the screen?"))

        elif name == "watch_code":
            import skills.screen_monitor as sm
            action = args.get("action", "start")
            if action == "stop":
                return sm.stop_watching()
            elif action == "status":
                return sm.get_watch_status()
            else:
                filepath = args.get("filepath", "")
                if not filepath:
                    # Auto-detect from VS Code
                    editor_info = sm.read_active_editor()
                    if "error" in editor_info:
                        return f"Could not detect active file: {editor_info['error']}"
                    filepath = editor_info["filepath"]
                return sm.watch_file(filepath)

        elif name == "edit_code":
            import skills.screen_monitor as sm
            filepath = args.get("filepath", "")
            if not filepath:
                editor_info = sm.read_active_editor()
                if "error" in editor_info:
                    return f"Could not detect active file: {editor_info['error']}"
                filepath = editor_info["filepath"]
            return sm.edit_file(filepath, args["old_text"], args["new_text"])

        return f"Unknown tool: {name}"

    except Exception as e:
        return f"Tool error ({name}): {str(e)[:150]}"


# ── Agent state ───────────────────────────────────────────────────────────────

_client = None
_history: list[dict] = []
_initialized = False


def _ensure_init():
    global _history, _initialized
    if not _initialized:
        _history = load_chat_history()
        _initialized = True


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        cfg = load_config()
        key = cfg.get("groq_api_key", "")
        base_url = cfg.get("openai_base_url", "https://api.groq.com/openai/v1")
        if not key:
            raise RuntimeError("Groq API key missing from config.json.")
        _client = OpenAI(api_key=key, base_url=base_url)
    return _client


def _get_model() -> str:
    return load_config().get("groq_model", "qwen/qwen3.8-27b")


# ── Main agent entry point ────────────────────────────────────────────────────

_MAX_TOOL_ROUNDS = 6   # max LLM→tool loops per user turn
_MAX_HISTORY     = 20  # messages kept in context

def process(user_message: str) -> str:
    """
    Process a user message using the agentic tool-calling loop.
    The LLM decides what tools to call, executes them, and returns a final answer.
    """
    global _history
    _ensure_init()

    client = _get_client()
    model = _get_model()
    name = get_user_name()
    facts = get_facts_string()
    system = _SYSTEM_PROMPT.format(name=name, facts=facts)

    # Add user message to history
    _history.append({"role": "user", "content": user_message})
    if len(_history) > _MAX_HISTORY:
        _history = _history[-_MAX_HISTORY:]

    # Log the command
    log_command(user_message, "agent")

    # Build messages for the API call
    messages = [{"role": "system", "content": system}] + _history

    try:
        # Track executed tool calls this turn to prevent duplicates
        _executed_this_turn: set = set()

        # Agent loop: call LLM → execute tools → repeat until done
        for _ in range(_MAX_TOOL_ROUNDS):
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=2048,
            )

            msg = response.choices[0].message

            # If no tool calls, we have the final answer
            if not msg.tool_calls:
                reply = (msg.content or "").strip()
                if not reply:
                    reply = "Done."
                _history.append({"role": "assistant", "content": reply})
                save_chat_history(_history)
                return reply

            # Execute each tool call
            # Add assistant message with tool calls to messages
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in msg.tool_calls
                ]
            })

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                # Deduplication: skip if exact same tool+args already ran this turn
                call_fingerprint = f"{tool_name}:{tc.function.arguments}"
                if call_fingerprint in _executed_this_turn:
                    result = f"(skipped duplicate call to {tool_name})"
                else:
                    _executed_this_turn.add(call_fingerprint)
                    result = _execute_tool(tool_name, tool_args)

                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result)
                })

        # Exceeded max rounds — ask the LLM to synthesise all tool results
        # into a proper answer instead of dumping raw snippets
        try:
            synthesis = client.chat.completions.create(
                model=model,
                messages=messages + [{
                    "role": "user",
                    "content": "Now summarise everything you found and give a complete, helpful answer."
                }],
                tools=TOOLS,
                tool_choice="none",
                temperature=0.5,
                max_tokens=2048,
            )
            reply = (synthesis.choices[0].message.content or "").strip()
            if not reply:
                reply = "I gathered info but couldn't form a reply. Try asking again."
        except Exception:
            reply = "I ran out of steps. Try rephrasing your request."

        _history.append({"role": "assistant", "content": reply})
        save_chat_history(_history)
        return reply

    except Exception as e:
        err = str(e)
        if "rate" in err.lower() or "limit" in err.lower():
            return "I've hit the rate limit. Give me a moment."
        if "api_key" in err.lower():
            return "API key issue. Check config.json."

        # Network/connection error → try offline fallback via regex brain
        is_offline = any(kw in err.lower() for kw in [
            "connection", "network", "timeout", "unreachable",
            "name or service", "socket", "refused", "ssl"
        ])
        if is_offline:
            try:
                from core.brain import classify
                from core.executor import execute
                intent, entities = classify(user_message)
                if intent and intent != "ollama_chat":
                    result = execute(intent, entities, user_message)
                    return f"[Offline mode] {result}"
            except Exception:
                pass
            return "⚠️ No internet. I can still do local commands: volume, open apps, lock screen, reminders, time."

        return f"Something went wrong: {err[:150]}"


def clear_history():
    """Reset conversation."""
    global _history, _client, _initialized
    _history = []
    _client = None
    _initialized = False
    save_chat_history([])
