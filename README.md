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
| ⏰ **Reminders** | "Remind me to call mom in 30 minutes" |
| 🖥️ **System** | "Lock screen", "Battery status", "Take screenshot", "Restart" |
| 📱 **Apps** | "Open Chrome", "Close Spotify", "Open VS Code" |
| 🧠 **Memory** | Remembers your name, preferences, corrections across sessions |
| 🔌 **Offline** | Falls back to local commands when internet is down |

---

## 🚀 Quick Install

### 1. Clone the repo
```bash
git clone https://github.com/YOUR_USERNAME/DODO.git
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
├── main.py              # Entry point — starts voice + UI
├── config.example.json  # Config template (copy to config.json)
├── requirements.txt     # Python dependencies
├── core/
│   ├── agent.py         # AI brain — tool-calling loop (GPT-120B via Groq)
│   ├── voice.py         # Wake word detection (Whisper)
│   ├── speech.py        # Text-to-speech (Edge Neural TTS)
│   ├── memory.py        # Persistent user memory & facts
│   └── executor.py      # Offline regex fallback dispatcher
├── skills/
│   ├── music_player.py  # Alexa-style local music via yt-dlp
│   ├── email_sender.py  # Gmail compose / SMTP
│   ├── web_search.py    # DuckDuckGo search
│   ├── weather.py       # Live weather
│   ├── reminders.py     # Reminder scheduler
│   ├── browser_control.py
│   ├── file_ops.py
│   └── system_control.py
├── ui/
│   └── control_panel.py # Tkinter control panel
└── memory/              # Created automatically on first run
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
