# 🤖 DODO — AI Desktop Assistant

> A powerful, always-listening AI assistant for Windows — like Alexa, but for your PC.

Built with Python + OpenAI SDK (via Groq) + Whisper STT + Edge TTS.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![AI](https://img.shields.io/badge/AI-Groq%20%7C%20OpenAI%20GPT--120B-purple)

---

## ✨ What DODO Can Do

| Category | Commands |
|----------|----------|
| 🎵 **Music** | "Play Ritviz", "Stop music" — streams audio locally, no browser |
| 🌐 **Web** | "Open YouTube", "Search for X", "Go to my blog" |
| 📧 **Email** | "Send email to X about Y" — opens Gmail compose pre-filled |
| 🔊 **Volume** | "Set volume to 50%", "Mute", "Volume up" |
| 📁 **Files** | "Create file X", "Open folder Y", "Find file Z" |
| ☁️ **Weather** | "What's the weather in Delhi?" |
| ⏰ **Reminders** | "Remind me at 5pm", "Drink water every 2 hours", "Urgent reminder to call mom in 15 mins" — recurring alerts, snooze, desktop toasts, and dedicated GUI tab |
| 📸 **Screenshot** | "Take a screenshot", "Screenshot active window", "Screenshot in 5 seconds" — multi-mode capture, auto-clipboard sync, audio FX, AI Vision HUD, & floating modal |
| 👁️ **Vision** | "Look through my camera", "What do you see?" — real-time object & facial expression recognition |
| 🖥️ **System** | "Lock screen", "Battery status", "Restart", "Sleep" |
| 📱 **Apps** | "Open Chrome", "Close Spotify", "Open VS Code" |
| 🧠 **Memory** | Remembers your name, preferences, corrections across sessions |
| 🔌 **Offline** | Falls back to local commands when internet is down |

---

## 🚀 Quick Install

### 1. Clone the repo
```bash
git clone https://github.com/VishnuSwarnkar12/DODO.git
cd DODO
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Set up config
```bash
copy config.example.json config.json
```
Then open `config.json` and fill in:
- `"user_name"` — your name
- `"groq_api_key"` — get a **free** key at [console.groq.com](https://console.groq.com) (no credit card)

### 4. Run DODO
```bash
python main.py
```

---

## 🎤 How to Use

**Voice mode:** Say **"DODO"** to wake it up, then speak your command.

**Text mode:** Type in the bottom bar and press Enter or click Send.

---

## ⏰ Advanced Reminders & Time Management

DODO includes an intelligent reminder engine with thread-safe persistence and multi-channel alerting:

- **Natural Language Parsing**:
  - **Relative offsets**: `"in 15 minutes"`, `"in 2 hours"`, `"in 30 seconds"`, `"half an hour"`
  - **Clock times**: `"at 5pm"`, `"at 5:30 pm"`, `"at 9am"`, `"at noon"`, `"at midnight"`
  - **Day & weekday scheduling**: `"tomorrow at 9am"`, `"tonight at 8pm"`, `"on Friday at 4pm"`, `"next Monday at 10am"`
  - **Hindi / Hinglish**: `"5 minute baad"`, `"1 ghante baad"`, `"kal subah 9 baje"`, `"shaam ko 7 baje"`
- **Recurring Schedules**: Set repeating reminders with `daily`, `weekdays`, `weekly`, `hourly`, or interval repeats (e.g. `"remind me to stretch every 2 hours"`).
- **Priorities & Snooze**: Tag urgent tasks (`"remind me urgently to submit project"`) with visual and audio alerts, and quickly snooze tasks by 10 minutes (`"snooze reminder 2"`).
- **Multi-Channel Alerting**:
  - 🔊 **Audio Chime** (Windows alert chime)
  - 🗣️ **Voice TTS** (Edge Neural speech announcement)
  - 🪟 **Native Windows Notifications** (System tray bubble / desktop toast)
  - 💬 **Interactive Chat & Activity Log**
- **Dedicated GUI Tab (`⏰`)**:
  - Filter by **Pending**, **Today**, **Completed**, or **All**
  - Quick preset buttons (`+10m`, `+30m`, `+1h`, `Tomorrow`)
  - One-click **✓ Done**, **💤 Snooze (+10m)**, and **✕ Delete**
  - Live humanized countdowns (*"In 12 mins"*, *"Today at 5:30 PM"*, *"Overdue by 3 mins"*)
- **Missed Reminder Recovery**: Detects tasks that came due while the PC was asleep or offline, gently notifying you upon launch.

---

## 📸 Futuristic Screenshot & Screen Intelligence

DODO features a multi-mode screen capture and vision intelligence engine designed for speed and productivity:

- **Multi-Mode Capturing**:
  - **🖥️ Fullscreen**: Captures primary or multi-monitor virtual desktop.
  - **🪟 Active Window**: Automatically identifies the focused application (VS Code, Chrome, etc.) and crops cleanly to its shadowless window frame via Windows DWM APIs.
  - **⏱️ Delayed Countdown Timer**: 3-second or 5-second countdown timer allowing you to switch tabs, expand dropdown menus, or select windows before snapping.
- **Instant Windows Clipboard Sync**: Automatically copies the captured bitmap directly to the Windows Clipboard (`CF_DIB`) so you can immediately `Ctrl+V` paste into Discord, Slack, WhatsApp, Twitter, or email.
- **Futuristic Audio Shutter FX**: Dual-frequency sci-fi camera snap audio chirp (`1600Hz` $\to$ `2400Hz`) provides instant feedback.
- **🔍 AI Vision HUD Analysis**: Option to inspect the screen with multimodal AI (Gemini / NVIDIA / Groq) to explain error tracebacks, read active code, or describe UI elements.
- **Interactive Screenshot HUD**:
  - Floating launcher accessible via the `📸` button in the UI input bar or Home screen Quick Actions.
  - Quick action buttons to open the captured image or jump directly to the screenshots folder in Windows Explorer.
- **Structured Storage & Smart Retention**:
  - Automatically organized under `Pictures/DODO_Screenshots/` with timestamped and app-tagged filenames.
  - Scalable background cleanup keeps storage clean without deleting recent captures.

---

## 🔑 Getting a Free Groq API Key

1. Go to [console.groq.com](https://console.groq.com)
2. Sign up (free, no credit card)
3. Go to **API Keys** → **Create API Key**
4. Paste it into `config.json` as `"groq_api_key"`

Groq gives you access to **OpenAI GPT-120B** for free with generous daily limits.

---

## 🎵 Music Playback Setup (Optional)

DODO uses `yt-dlp` to stream audio. For best experience, install **VLC**:
- Download: [videolan.org/vlc](https://www.videolan.org/vlc/)
- Without VLC, DODO falls back to Windows Media Player

---

## 📦 Requirements

- **Python 3.10+**
- **Windows 10/11**
- **Microphone** (for voice mode)
- Free [Groq API key](https://console.groq.com)
- VLC (optional, for local music playback)

---

## 🏗️ Project Structure

```
DODO/
├── main.py              # Entry point — starts voice + UI + background services
├── config.example.json  # Config template (copy to config.json)
├── requirements.txt     # Python dependencies
├── core/
│   ├── agent.py         # AI agent — tool-calling loop (Groq / OpenAI compatible)
│   ├── brain.py         # Intent classification & natural language parsing
│   ├── voice.py         # Wake word detection & audio capture (Whisper)
│   ├── speech.py        # Text-to-speech (Edge Neural TTS)
│   ├── object_recognition.py # Real-time YOLOv8 object recognition
│   ├── memory.py        # Persistent user memory & facts
│   └── executor.py      # Local action dispatcher & offline fallback
├── skills/
│   ├── reminders.py     # Advanced reminder, timer, & recurring task engine
│   ├── screenshot_engine.py # Futuristic multi-mode capture, clipboard sync & audio FX
│   ├── screen_monitor.py # Multi-monitor capture & AI screen analysis
│   ├── webcam_vision.py # Live camera vision analysis
│   ├── face_expression.py # Facial expression tracking
│   ├── music_player.py  # Local streaming music via yt-dlp
│   ├── email_sender.py  # Gmail compose / SMTP
│   ├── web_search.py    # DuckDuckGo search
│   ├── weather.py       # Live weather
│   ├── browser_control.py # Browser automation
│   ├── file_ops.py      # File & folder operations
│   └── system_control.py# Windows volume, battery, lock, & apps
├── ui/
│   ├── control_panel.py # CustomTkinter desktop panel (Home, Chat, Reminders, Memory, Settings)
│   └── widgets.py       # Pulsing orb, chat bubbles, toggle switches
└── memory/              # Local JSON data (auto-created on first run, git-ignored)
    ├── reminders.json
    ├── chat_history.json
    ├── user_profile.json
    └── commands.json
```

---

## ⚙️ Configuration Options

| Key | Default | Description |
|-----|---------|-------------|
| `user_name` | `"YourName"` | Your name (DODO greets you by it) |
| `groq_api_key` | `""` | Your Groq API key (required) |
| `groq_model` | `"openai/gpt-oss-120b"` | AI model to use |
| `wake_word` | `"dodo"` | Word that activates voice listening |
| `tts_voice` | `"en-IN-NeerjaNeural"` | TTS voice (any Edge Neural voice) |
| `whisper_model` | `"base"` | Whisper model for STT (tiny/base/small) |
| `startup_greeting` | `true` | Greeting message on launch |
| `gmail_address` | `""` | Optional: for auto-send emails via SMTP |
| `gmail_app_password` | `""` | Optional: Gmail App Password for SMTP |

---

## 🧠 Adaptive Memory

DODO remembers things you tell it across sessions:
- Corrects wrong facts when you say "no, it's actually..."
- Stores preferences, your name, file locations, habits
- Chat history persisted for context

Memory is stored locally in the `memory/` folder — never sent anywhere except to the Groq API for processing.

---

## 🛠️ Troubleshooting

**"No module named X"**
```bash
pip install -r requirements.txt
```

**Microphone not detected**
- Check Windows sound settings → ensure microphone is set as default input
- Try `whisper_model: "tiny"` in config for faster detection

**Music not playing**
- Install VLC: [videolan.org/vlc](https://www.videolan.org/vlc/)
- Or use `"play X on youtube"` to open in browser instead

**Voice not responding**
- Say the wake word clearly: **"DODO"**
- Check that `mic_enabled: true` in config.json

---

## 🤝 Contributing

Pull requests welcome! Areas that need work:
- More skills (calendar, notes, clipboard)
- Better wake word accuracy
- Mobile companion app
- macOS / Linux support

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

*Built with ❤️ by [Vishnu](https://vishnutells.blogspot.com)*
