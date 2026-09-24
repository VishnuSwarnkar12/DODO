"""
DODO — ui/control_panel.py
Main desktop window — teal dark theme, chat-first layout with icon sidebar.

Layout:
  ┌──────┬──────────────────────────────────┐
  │ Logo │  DODO  ● Online   [Model ▾]     │
  │  ⌂   │──────────────────────────────────│
  │  ◯   │                                  │
  │  ◇   │    Chat / Home / Memory content  │
  │  ⚙   │                                  │
  │      │──────────────────────────────────│
  │  ●   │  [ Ask DODO anything... ] [➤][🎤]│
  └──────┴──────────────────────────────────┘
"""

import os
import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from PIL import Image, ImageDraw
import pystray
import keyboard

from ui.widgets import PulsingOrb, ChatBubble, LogEntry, ToggleSwitch
from core.memory import load_config, get_user_name

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ── Teal palette ──────────────────────────────────────────────────────────────

BG          = "#0a0e14"
SIDEBAR     = "#0d1117"
SURFACE     = "#151b25"
SURFACE_ALT = "#1c2433"
ACCENT      = "#00d4aa"
ACCENT_SOFT = "#0d3d35"
TEXT        = "#e0e6f0"
MUTED       = "#5a6580"
DANGER      = "#ff4f7b"
GOLD        = "#ffd700"


class ControlPanel(ctk.CTk):
    """DODO's main desktop window — teal dark theme."""

    STATUS_LABELS = {
        "IDLE":        ("● IDLE",        ACCENT),
        "LISTENING":   ("◉ LISTENING",   "#00aaff"),
        "PROCESSING":  ("⟳ PROCESSING",  "#9b4fff"),
        "SPEAKING":    ("▷ SPEAKING",    "#00ffcc"),
        "CALIBRATING": ("◈ CALIBRATING", "#ffaa00"),
        "MIC_ERROR":   ("✕ MIC ERROR",   DANGER),
    }

    def __init__(self, command_queue: queue.Queue,
                 voice_engine=None, on_exit=None):
        super().__init__()

        self.command_queue = command_queue
        self.voice_engine  = voice_engine
        self.on_exit       = on_exit
        self._tray_icon    = None
        self._ui_queue: queue.Queue = queue.Queue()
        self._config       = load_config()
        self.user_name     = get_user_name()
        hotkey             = self._config.get("hotkey", "ctrl+alt+d")

        # ── Window ────────────────────────────────────────────────────────────
        self.title("DODO")
        self.geometry("920x660")
        self.minsize(700, 500)
        self.configure(fg_color=BG)
        try:
            self.iconbitmap(self._get_icon_path())
        except Exception:
            pass
        try:
            keyboard.add_hotkey(hotkey, self._hotkey_show)
        except Exception:
            pass
        self.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)
        self._start_tray()

        # ── Build skeleton ────────────────────────────────────────────────────
        self._build_sidebar()
        self._content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self._content.pack(side="left", fill="both", expand=True)

        # Refs to per-view widgets (cleared on view switch)
        self._chat_scroll  = None
        self._log_scroll   = None
        self._orb          = None
        self._hero_status  = None
        self._header_status= None
        self._input_box    = None
        self._mic_btn      = None

        # Persistent message buffers — survive tab switches
        self._chat_messages: list[tuple[str, str]] = []   # [(text, sender), ...]
        self._log_messages:  list[tuple[str, str]] = []   # [(action_type, text), ...]

        self._active_view = "chat"
        self._show_view("chat")
        self._poll_queue()

    # ══════════════════════════════════════════════════════════════════════════
    # SIDEBAR — slim 64 px icon rail
    # ══════════════════════════════════════════════════════════════════════════

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=64, fg_color=SIDEBAR, corner_radius=0)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        # Logo
        ctk.CTkLabel(sb, text="◈", font=ctk.CTkFont("Segoe UI", 26),
                     text_color=ACCENT).pack(pady=(20, 28))

        # Nav icons
        self._nav_buttons = {}
        for key, icon in [("home", "⌂"), ("chat", "◯"), ("memory", "◇"), ("settings", "⚙")]:
            btn = ctk.CTkButton(
                sb, text=icon, width=42, height=42,
                font=ctk.CTkFont("Segoe UI", 18),
                fg_color="transparent", hover_color=SURFACE_ALT,
                text_color=MUTED, corner_radius=10,
                command=lambda v=key: self._show_view(v),
            )
            btn.pack(pady=3)
            self._nav_buttons[key] = btn

        # Status dot
        self._side_status = ctk.CTkLabel(
            sb, text="●", font=ctk.CTkFont("Segoe UI", 14), text_color=ACCENT
        )
        self._side_status.pack(side="bottom", pady=18)

    # ══════════════════════════════════════════════════════════════════════════
    # VIEW SWITCHING
    # ══════════════════════════════════════════════════════════════════════════

    def _clear_content(self):
        for w in self._content.winfo_children():
            w.destroy()
        # Invalidate refs to destroyed widgets
        self._chat_scroll = self._log_scroll = None
        self._orb = self._hero_status = self._header_status = None
        self._input_box = self._mic_btn = None

    def _show_view(self, view: str):
        self._active_view = view
        self._clear_content()
        for k, b in self._nav_buttons.items():
            b.configure(
                fg_color=ACCENT_SOFT if k == view else "transparent",
                text_color=ACCENT if k == view else MUTED,
            )
        builders = {
            "home": self._build_home, "chat": self._build_chat_view,
            "memory": self._build_memory, "settings": self._build_settings,
        }
        builders.get(view, self._build_chat_view)()

    # ══════════════════════════════════════════════════════════════════════════
    # SHARED COMPONENTS
    # ══════════════════════════════════════════════════════════════════════════

    def _build_header(self, parent):
        hdr = ctk.CTkFrame(parent, fg_color=SIDEBAR, height=52, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="DODO", font=ctk.CTkFont("Consolas", 16, "bold"),
                     text_color=TEXT).pack(side="left", padx=(20, 8))
        self._header_status = ctk.CTkLabel(
            hdr, text="● Online", font=ctk.CTkFont("Segoe UI", 11), text_color=ACCENT
        )
        self._header_status.pack(side="left")
        # Model selector
        models = ["Gemini 2.0 Flash", "Groq GPT-OSS-120B"]
        current = "Gemini 2.0 Flash" if self._config.get("ai_provider") == "gemini" else "Groq GPT-OSS-120B"
        sel = ctk.CTkOptionMenu(
            hdr, values=models, fg_color=SURFACE_ALT, button_color=SURFACE_ALT,
            button_hover_color="#253040", dropdown_fg_color=SURFACE,
            text_color=TEXT, font=ctk.CTkFont("Segoe UI", 11),
            command=self._on_model_change, width=200, height=30, corner_radius=8,
        )
        sel.set(current)
        sel.pack(side="right", padx=20)

    def _build_input_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color=SIDEBAR, height=64, corner_radius=0)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=10)

        self._input_box = ctk.CTkTextbox(
            inner, height=40, width=200,
            fg_color=SURFACE, border_color=SURFACE_ALT, border_width=1,
            text_color=TEXT, font=ctk.CTkFont("Segoe UI", 13),
            corner_radius=12, wrap="word",
        )
        self._input_box.pack(side="left", fill="x", expand=True, padx=(0, 8))
        # Enter sends, Shift+Enter makes new line
        self._input_box.bind("<Return>", self._on_enter_key)
        self._input_box.bind("<Shift-Return>", self._on_shift_enter)

        ctk.CTkButton(
            inner, text="➤", width=40, height=40, corner_radius=20,
            fg_color=ACCENT, hover_color="#00b894",
            text_color=BG, font=ctk.CTkFont("Segoe UI", 16, "bold"),
            command=self._on_text_submit,
        ).pack(side="left", padx=(0, 6))

        self._mic_btn = ctk.CTkButton(
            inner, text="🎤", width=40, height=40, corner_radius=20,
            fg_color=SURFACE_ALT, hover_color="#253040",
            text_color=ACCENT, font=ctk.CTkFont("Segoe UI", 16),
            command=self._on_mic_btn_click,
        )
        self._mic_btn.pack(side="left")

    # ══════════════════════════════════════════════════════════════════════════
    # HOME VIEW — orb + greeting + quick actions
    # ══════════════════════════════════════════════════════════════════════════

    def _build_home(self):
        self._build_header(self._content)

        body = ctk.CTkScrollableFrame(self._content, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=30, pady=20)

        # Greeting
        ctk.CTkLabel(body, text="GOOD TO SEE YOU", font=ctk.CTkFont("Consolas", 10, "bold"),
                     text_color=ACCENT).pack(anchor="w")
        ctk.CTkLabel(body, text=f"What should we do, {self.user_name}?",
                     font=ctk.CTkFont("Segoe UI", 26, "bold"),
                     text_color=TEXT).pack(anchor="w", pady=(4, 0))
        ctk.CTkLabel(body, text="Speak naturally or choose a quick action.",
                     font=ctk.CTkFont("Segoe UI", 12),
                     text_color=MUTED).pack(anchor="w", pady=(4, 24))

        # Orb hero card
        orb_card = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=14)
        orb_card.pack(fill="x", pady=(0, 20))
        orb_inner = ctk.CTkFrame(orb_card, fg_color="transparent")
        orb_inner.pack(fill="x", padx=22, pady=18)

        self._orb = PulsingOrb(orb_inner, size=120, bg=SURFACE)
        self._orb.pack(side="left", padx=(0, 22))

        info = ctk.CTkFrame(orb_inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        self._hero_status = ctk.CTkLabel(
            info, text="● IDLE", font=ctk.CTkFont("Consolas", 18, "bold"), text_color=ACCENT
        )
        self._hero_status.pack(anchor="w")
        ctk.CTkLabel(info, text="DODO is ready for your next move.",
                     font=ctk.CTkFont("Segoe UI", 12), text_color=MUTED
        ).pack(anchor="w", pady=(4, 0))
        ctk.CTkButton(
            info, text="Start listening", width=140, height=36, corner_radius=18,
            fg_color=ACCENT, hover_color="#00b894", text_color=BG,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), command=self._on_mic_btn_click,
        ).pack(anchor="w", pady=(14, 0))

        # Quick actions
        ctk.CTkLabel(body, text="QUICK ACTIONS", font=ctk.CTkFont("Consolas", 10, "bold"),
                     text_color=MUTED).pack(anchor="w", pady=(0, 8))
        qa = ctk.CTkFrame(body, fg_color="transparent")
        qa.pack(fill="x")
        for label, cmd in [("📸  Screenshot", "take a screenshot"),
                           ("🔋  Battery", "battery status"),
                           ("🌐  My Blog", "open my blog"),
                           ("🔒  Lock", "lock screen")]:
            ctk.CTkButton(
                qa, text=label, width=130, height=38, corner_radius=10,
                fg_color=SURFACE, hover_color=SURFACE_ALT,
                text_color=TEXT, font=ctk.CTkFont("Segoe UI", 11),
                command=lambda c=cmd: self._inject_command(c),
            ).pack(side="left", padx=(0, 8))

        self._build_input_bar(self._content)

    # ══════════════════════════════════════════════════════════════════════════
    # CHAT VIEW — default, full-height conversation
    # ══════════════════════════════════════════════════════════════════════════

    def _build_chat_view(self):
        self._build_header(self._content)

        # Chat area
        self._chat_scroll = ctk.CTkScrollableFrame(
            self._content, fg_color=BG, corner_radius=0
        )
        self._chat_scroll.pack(fill="both", expand=True, padx=12, pady=(8, 0))

        # Restore buffered chat messages
        for text, sender in self._chat_messages:
            bubble = ChatBubble(self._chat_scroll, text, sender)
            bubble.pack(fill="x", padx=(80, 6) if sender == "user" else (6, 80), pady=4)
        self.after(50, lambda: self._safe_scroll(self._chat_scroll))

        # Activity log strip
        log_strip = ctk.CTkFrame(self._content, fg_color=SURFACE, height=75, corner_radius=0)
        log_strip.pack(fill="x")
        log_strip.pack_propagate(False)
        ctk.CTkLabel(log_strip, text="ACTIVITY", font=ctk.CTkFont("Consolas", 9, "bold"),
                     text_color=MUTED).pack(anchor="w", padx=16, pady=(6, 0))
        self._log_scroll = ctk.CTkScrollableFrame(log_strip, fg_color="transparent", height=36)
        self._log_scroll.pack(fill="both", expand=True, padx=8, pady=(2, 4))

        # Restore buffered log messages
        for action_type, text in self._log_messages:
            entry = LogEntry(self._log_scroll, action_type, text)
            entry.pack(fill="x", padx=4, pady=2)
        self.after(50, lambda: self._safe_scroll(self._log_scroll))

        self._build_input_bar(self._content)

    # ══════════════════════════════════════════════════════════════════════════
    # MEMORY VIEW
    # ══════════════════════════════════════════════════════════════════════════

    def _build_memory(self):
        self._build_header(self._content)
        body = ctk.CTkScrollableFrame(self._content, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=30, pady=20)

        ctk.CTkLabel(body, text="PERSONAL CONTEXT", font=ctk.CTkFont("Consolas", 10, "bold"),
                     text_color=ACCENT).pack(anchor="w")
        ctk.CTkLabel(body, text="What DODO remembers",
                     font=ctk.CTkFont("Segoe UI", 22, "bold"),
                     text_color=TEXT).pack(anchor="w", pady=(4, 16))

        try:
            from core.memory import load_facts
            facts = load_facts()
        except Exception:
            facts = []

        if not facts:
            ctk.CTkLabel(body, text="No facts stored yet. Tell DODO something to remember!",
                         text_color=MUTED, font=ctk.CTkFont("Segoe UI", 13)).pack(pady=30)
        else:
            for fact in facts:
                row = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=10)
                row.pack(fill="x", pady=3)
                ctk.CTkLabel(row, text="◇", text_color=ACCENT,
                             font=ctk.CTkFont("Segoe UI", 16)).pack(side="left", padx=(14, 8), pady=10)
                ctk.CTkLabel(row, text=fact, text_color=TEXT, justify="left", anchor="w",
                             wraplength=600, font=ctk.CTkFont("Segoe UI", 12)
                ).pack(side="left", fill="x", expand=True, padx=(0, 14), pady=10)

        self._build_input_bar(self._content)

    # ══════════════════════════════════════════════════════════════════════════
    # SETTINGS VIEW
    # ══════════════════════════════════════════════════════════════════════════

    def _build_settings(self):
        self._build_header(self._content)
        body = ctk.CTkScrollableFrame(self._content, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=30, pady=20)

        ctk.CTkLabel(body, text="CONTROL ROOM", font=ctk.CTkFont("Consolas", 10, "bold"),
                     text_color=ACCENT).pack(anchor="w")
        ctk.CTkLabel(body, text="Settings", font=ctk.CTkFont("Segoe UI", 22, "bold"),
                     text_color=TEXT).pack(anchor="w", pady=(4, 16))

        # ── General card ──────────────────────────────────────────────────────
        card = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=14)
        card.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(card, text="GENERAL", font=ctk.CTkFont("Consolas", 10, "bold"),
                     text_color=MUTED).pack(anchor="w", padx=18, pady=(14, 8))
        self._s_name  = self._settings_entry(card, "Your name",  self._config.get("user_name", ""))
        self._s_wake  = self._settings_entry(card, "Wake word",  self._config.get("wake_word", "dodo"))
        self._s_hotkey= self._settings_entry(card, "Hotkey",     self._config.get("hotkey", "ctrl+alt+d"))

        tog = ctk.CTkFrame(card, fg_color="transparent")
        tog.pack(fill="x", padx=18, pady=(6, 14))
        self._tog_mic = ToggleSwitch(tog, "Microphone",
                                     self._config.get("mic_enabled", True), self._on_mic_toggle)
        self._tog_mic.pack(side="left", padx=(0, 28))
        self._tog_online = ToggleSwitch(tog, "Online mode",
                                        self._config.get("online_mode", True), self._on_online_toggle)
        self._tog_online.pack(side="left")

        # ── AI Model card ─────────────────────────────────────────────────────
        ai = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=14)
        ai.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(ai, text="AI MODEL", font=ctk.CTkFont("Consolas", 10, "bold"),
                     text_color=MUTED).pack(anchor="w", padx=18, pady=(14, 8))

        prov = ctk.CTkFrame(ai, fg_color="transparent")
        prov.pack(fill="x", padx=18, pady=(0, 8))
        self._provider_var = tk.StringVar(value=self._config.get("ai_provider", "groq"))
        ctk.CTkRadioButton(prov, text="Gemini (Google)", variable=self._provider_var,
                           value="gemini", text_color=TEXT, fg_color=ACCENT,
                           hover_color=ACCENT).pack(side="left", padx=(0, 20))
        ctk.CTkRadioButton(prov, text="Groq", variable=self._provider_var,
                           value="groq", text_color=TEXT, fg_color=ACCENT,
                           hover_color=ACCENT).pack(side="left")

        self._s_gemini = self._settings_entry(ai, "Gemini API key",
                                              self._config.get("gemini_api_key", ""), show="•")
        self._s_groq   = self._settings_entry(ai, "Groq API key",
                                              self._config.get("groq_api_key", ""), show="•")
        ctk.CTkFrame(ai, height=8, fg_color="transparent").pack()

        # Save
        ctk.CTkButton(
            body, text="Save settings", width=160, height=38, corner_radius=10,
            fg_color=ACCENT, hover_color="#00b894", text_color=BG,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), command=self._save_settings,
        ).pack(anchor="w", pady=(4, 20))

    def _settings_entry(self, parent, label: str, value: str, show=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 6))
        ctk.CTkLabel(row, text=label, width=130, anchor="w", text_color=MUTED,
                     font=ctk.CTkFont("Segoe UI", 12)).pack(side="left")
        e = ctk.CTkEntry(row, height=34, fg_color="#0d1117", border_color=SURFACE_ALT,
                         text_color=TEXT, corner_radius=8)
        if show:
            e.configure(show=show)
        e.insert(0, str(value))
        e.pack(side="left", fill="x", expand=True)
        return e

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC API (called by main.py processing loop)
    # ══════════════════════════════════════════════════════════════════════════

    def set_status(self, state: str):
        label, color = self.STATUS_LABELS.get(state, self.STATUS_LABELS["IDLE"])
        try:
            self._orb.set_state(state)
        except Exception:
            pass
        try:
            self._hero_status.configure(text=label, text_color=color)
        except Exception:
            pass
        try:
            self._header_status.configure(text=f"● {state.capitalize()}", text_color=color)
        except Exception:
            pass
        try:
            self._side_status.configure(text_color=color)
        except Exception:
            pass
        self._update_mic_btn(state)

    def add_chat(self, text: str, sender: str = "dodo"):
        # Persist to buffer
        self._chat_messages.append((text, sender))
        # Keep buffer from growing forever (last 100 messages)
        if len(self._chat_messages) > 100:
            self._chat_messages = self._chat_messages[-100:]
        # If chat view not active, switch to it
        if self._chat_scroll is None:
            self._show_view("chat")
            return  # _build_chat_view already restored the message
        bubble = ChatBubble(self._chat_scroll, text, sender)
        bubble.pack(fill="x", padx=(80, 6) if sender == "user" else (6, 80), pady=4)
        self.after(50, lambda: self._safe_scroll(self._chat_scroll))

    def add_log(self, action_type: str, text: str):
        # Persist to buffer
        self._log_messages.append((action_type, text))
        if len(self._log_messages) > 50:
            self._log_messages = self._log_messages[-50:]
        if self._log_scroll is None:
            return
        entry = LogEntry(self._log_scroll, action_type, text)
        entry.pack(fill="x", padx=4, pady=2)
        self.after(50, lambda: self._safe_scroll(self._log_scroll))

    def ask_confirm(self, question: str) -> bool:
        return messagebox.askyesno("DODO — Confirm", question, parent=self)

    def set_ui_queue(self, q: queue.Queue):
        self._ui_queue = q

    @staticmethod
    def _safe_scroll(scrollable):
        try:
            scrollable._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    # HANDLERS
    # ══════════════════════════════════════════════════════════════════════════

    def _inject_command(self, command: str):
        self.command_queue.put(command)

    def _on_text_submit(self, event=None):
        if self._input_box is None:
            return "break"
        text = self._input_box.get("1.0", "end-1c").strip()
        if text:
            self._input_box.delete("1.0", "end")
            self.command_queue.put(text)
        return "break"

    def _on_enter_key(self, event=None):
        """Enter alone → send message."""
        self._on_text_submit()
        return "break"  # prevent default newline

    def _on_shift_enter(self, event=None):
        """Shift+Enter → insert newline."""
        if self._input_box:
            self._input_box.insert("end", "\n")
        return "break"

    def _on_mic_btn_click(self):
        if not self.voice_engine:
            return
        if not self.voice_engine.mic_enabled:
            try:
                self._mic_btn.configure(fg_color=DANGER)
                self.after(600, lambda: self._mic_btn.configure(fg_color=SURFACE_ALT))
            except Exception:
                pass
            return
        if self.voice_engine.state != "LISTENING":
            self.voice_engine.listen_once()

    def _update_mic_btn(self, state: str):
        try:
            if state == "LISTENING":
                self._mic_btn.configure(text="⏹", fg_color=DANGER, text_color=TEXT)
            else:
                self._mic_btn.configure(text="🎤", fg_color=SURFACE_ALT, text_color=ACCENT)
        except Exception:
            pass

    def _on_model_change(self, choice: str):
        self._config["ai_provider"] = "gemini" if "Gemini" in choice else "groq"
        self._save_config_file()
        try:
            from core.agent import reset_client
            reset_client()
        except Exception:
            pass

    def _on_mic_toggle(self, enabled: bool):
        if self.voice_engine:
            self.voice_engine.set_mic_enabled(enabled)
        self._config["mic_enabled"] = enabled

    def _on_online_toggle(self, enabled: bool):
        if self.voice_engine:
            self.voice_engine.set_online_mode(enabled)
        self._config["online_mode"] = enabled

    def _save_settings(self):
        self._config["user_name"] = self._s_name.get().strip() or "Boss"
        self._config["wake_word"] = self._s_wake.get().strip() or "dodo"
        self._config["hotkey"]    = self._s_hotkey.get().strip() or "ctrl+alt+d"
        self._config["ai_provider"] = self._provider_var.get()
        gk = self._s_gemini.get().strip()
        rk = self._s_groq.get().strip()
        if gk:
            self._config["gemini_api_key"] = gk
        if rk:
            self._config["groq_api_key"] = rk
        self._save_config_file()
        self.user_name = self._config["user_name"]
        try:
            from core.agent import reset_client
            reset_client()
        except Exception:
            pass
        self._show_view("settings")

    def _save_config_file(self):
        try:
            from core.memory import CONFIG_PATH
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=4)
        except Exception as e:
            print(f"[DODO] Config save error: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # UI QUEUE POLLING
    # ══════════════════════════════════════════════════════════════════════════

    def _poll_queue(self):
        try:
            while True:
                event = self._ui_queue.get_nowait()
                t = event.get("type")
                if t == "status":
                    self.set_status(event["state"])
                elif t == "chat":
                    self.add_chat(event["text"], event.get("sender", "dodo"))
                elif t == "log":
                    self.add_log(event.get("action_type", "unknown"), event["text"])
        except queue.Empty:
            pass
        except Exception:
            pass
        self.after(80, self._poll_queue)

    # ══════════════════════════════════════════════════════════════════════════
    # SYSTEM TRAY + HOTKEYS
    # ══════════════════════════════════════════════════════════════════════════

    def _start_tray(self):
        img  = self._make_tray_icon()
        menu = pystray.Menu(
            pystray.MenuItem("Show DODO", self._tray_show, default=True),
            pystray.MenuItem("Exit", self._tray_exit),
        )
        self._tray_icon = pystray.Icon("DODO", img, "DODO", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _make_tray_icon(self):
        img = Image.new("RGB", (64, 64), BG)
        d   = ImageDraw.Draw(img)
        d.ellipse([8, 8, 56, 56], fill=ACCENT)
        d.ellipse([20, 20, 44, 44], fill=BG)
        d.ellipse([27, 27, 37, 37], fill=GOLD)
        return img

    def _minimize_to_tray(self):
        self.withdraw()

    def _tray_show(self, icon=None, item=None):
        self.after(0, self.deiconify)
        self.after(0, self.lift)

    def _tray_exit(self, icon=None, item=None):
        if self._tray_icon:
            self._tray_icon.stop()
        if self.on_exit:
            self.on_exit()
        self.after(0, self.quit)

    def _hotkey_show(self):
        self.after(0, self._tray_show)

    def _get_icon_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "assets", "dodo_icon.ico")
