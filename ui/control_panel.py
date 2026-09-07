"""
DODO — ui/control_panel.py
Main customtkinter control panel window.

Layout order (CRITICAL for tkinter pack manager):
  1. Titlebar         — side=top
  2. Input bar        — side=bottom  ← MUST be packed before expanding sections
  3. Controls bar     — side=bottom  ← MUST be packed before expanding sections
  4. Orb / greeting   — side=top, fixed height
  5. Chat area        — fill=both, expand=True  ← takes all remaining space
  6. Log area         — side=bottom of remaining, fixed height
"""

import queue
import threading
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageDraw
import pystray
import keyboard

from ui.widgets import PulsingOrb, ChatBubble, LogEntry, ToggleSwitch
from core.memory import load_config, get_user_name

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ── Colours ────────────────────────────────────────────────────────────────────
BG_PRIMARY   = "#0d0d1a"
BG_SECONDARY = "#111128"
BG_CARD      = "#161630"
ACCENT       = "#4f9fff"
ACCENT2      = "#9b4fff"
TEXT_DIM     = "#5566aa"
TEXT_MAIN    = "#d0d8f0"
TEXT_BRIGHT  = "#ffffff"
DANGER       = "#ff4f7b"
SUCCESS      = "#00e5a0"


class ControlPanel(ctk.CTk):
    """DODO's main desktop window."""

    STATUS_LABELS = {
        "IDLE":         ("● IDLE",         "#4f9fff"),
        "LISTENING":    ("◉ LISTENING",    "#00aaff"),
        "PROCESSING":   ("⟳ PROCESSING",   "#9b4fff"),
        "SPEAKING":     ("▷ SPEAKING",     "#cc44ff"),
        "CALIBRATING":  ("◈ CALIBRATING",  "#ffaa00"),
        "MIC_ERROR":    ("✕ MIC ERROR",    "#ff4f7b"),
    }

    def __init__(self, command_queue: queue.Queue,
                 voice_engine=None, on_exit=None):
        super().__init__()

        self.command_queue = command_queue
        self.voice_engine  = voice_engine
        self.on_exit       = on_exit
        self._tray_icon    = None
        self._confirm_result = None
        self._ui_queue: queue.Queue = queue.Queue()

        # State for mic button animation
        self._mic_btn_listening = False

        cfg = load_config()
        self.user_name = get_user_name()
        hotkey         = cfg.get("hotkey", "ctrl+alt+d")

        # ── Window setup ──────────────────────────────────────────────────────
        self.title("DODO")
        self.geometry("440x740")
        self.minsize(380, 600)
        self.resizable(True, True)
        self.configure(fg_color=BG_PRIMARY)

        try:
            self.iconbitmap(self._get_icon_path())
        except Exception:
            pass

        self.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

        try:
            keyboard.add_hotkey(hotkey, self._hotkey_show)
        except Exception:
            pass

        # ── Build UI (ORDER MATTERS for tkinter pack manager) ─────────────────
        self._build_titlebar()          # ① top — always visible header
        self._build_input_bar()         # ② bottom — MUST come before expanding sections
        self._build_quick_controls()    # ③ bottom — MUST come before expanding sections
        self._build_orb_section()       # ④ top — fixed height orb + greeting
        self._build_log_section()       # ⑤ bottom of remaining space
        self._build_chat_section()      # ⑥ fills ALL remaining space (expand=True)

        # ── Start polling queue ───────────────────────────────────────────────
        self._poll_queue()

        # ── System tray ───────────────────────────────────────────────────────
        self._start_tray()

    # ═══════════════════════════════════════════════════════════════════════════
    # UI Building
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_titlebar(self):
        bar = ctk.CTkFrame(self, fg_color=BG_SECONDARY, height=46, corner_radius=0)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        name_frame = ctk.CTkFrame(bar, fg_color="transparent")
        name_frame.pack(side="left", padx=14)

        ctk.CTkLabel(
            name_frame, text="⬡",
            font=ctk.CTkFont("Segoe UI", 20),
            text_color=ACCENT
        ).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            name_frame, text="D O D O",
            font=ctk.CTkFont("Consolas", 15, "bold"),
            text_color=TEXT_BRIGHT
        ).pack(side="left")

        # Status pill
        self._status_label = ctk.CTkLabel(
            bar, text="● IDLE",
            font=ctk.CTkFont("Consolas", 11, "bold"),
            text_color=ACCENT,
            fg_color="#0d0d2e",
            corner_radius=10,
            padx=10, pady=3
        )
        self._status_label.pack(side="right", padx=14)

        bar.bind("<ButtonPress-1>",  self._drag_start)
        bar.bind("<B1-Motion>",      self._drag_motion)

    def _build_orb_section(self):
        """Animated orb + greeting label — fixed height at top."""
        frame = ctk.CTkFrame(self, fg_color=BG_PRIMARY)
        frame.pack(fill="x", side="top", pady=(6, 0))

        self._orb = PulsingOrb(frame, size=130)
        self._orb.pack(pady=(10, 3))

        greeting = f"Hello, {self.user_name}. I'm DODO, your assistant."
        self._greeting_label = ctk.CTkLabel(
            frame, text=greeting,
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=TEXT_DIM
        )
        self._greeting_label.pack(pady=(0, 6))

    def _build_chat_section(self):
        """
        Conversation area — expands to fill remaining vertical space.
        Must be packed LAST so expand=True takes all leftover room.
        """
        header = ctk.CTkFrame(self, fg_color=BG_SECONDARY, height=28, corner_radius=0)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header, text="💬  Conversation",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            text_color=TEXT_DIM
        ).pack(side="left", padx=12)

        self._chat_scroll = ctk.CTkScrollableFrame(
            self, fg_color=BG_CARD, corner_radius=0
        )
        # fill="both" + expand=True → takes ALL remaining space
        self._chat_scroll.pack(fill="both", expand=True, side="top")

    def _build_log_section(self):
        """Compact action log at the bottom of the main area."""
        header = ctk.CTkFrame(self, fg_color=BG_SECONDARY, height=26, corner_radius=0)
        header.pack(fill="x", side="bottom")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header, text="📋  Action Log",
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color=TEXT_DIM
        ).pack(side="left", padx=12)

        self._log_scroll = ctk.CTkScrollableFrame(
            self, fg_color=BG_SECONDARY, height=80, corner_radius=0
        )
        self._log_scroll.pack(fill="x", side="bottom")

    def _build_quick_controls(self):
        """
        Controls bar — packed side=bottom (before chat/log so it always shows).
        Contains: Mic & Online toggles, quick-action buttons, calibrate.
        """
        frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0)
        frame.pack(fill="x", side="bottom")

        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=(8, 6))

        # ── Row A: toggles + calibrate ────────────────────────────────────────
        row_a = ctk.CTkFrame(inner, fg_color="transparent")
        row_a.pack(fill="x", pady=(0, 4))

        self._mic_toggle = ToggleSwitch(
            row_a, "🎤 Mic",
            initial=load_config().get("mic_enabled", True),
            on_change=self._on_mic_toggle
        )
        self._mic_toggle.pack(side="left", padx=(0, 16))

        self._online_toggle = ToggleSwitch(
            row_a, "🌐 Online",
            initial=load_config().get("online_mode", True),
            on_change=self._on_online_toggle
        )
        self._online_toggle.pack(side="left", padx=(0, 16))

        ctk.CTkButton(
            row_a, text="🎙️ Calibrate", width=110, height=26,
            font=ctk.CTkFont("Segoe UI", 11),
            fg_color="#1e3020", hover_color="#2a4a30",
            text_color=SUCCESS, corner_radius=8,
            command=self._calibrate_mic
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            row_a, text="🔊 Test", width=70, height=26,
            font=ctk.CTkFont("Segoe UI", 11),
            fg_color="#201e3a", hover_color="#302a5a",
            text_color="#cc44ff", corner_radius=8,
            command=self._test_voice
        ).pack(side="left")

        self._calib_label = ctk.CTkLabel(
            row_a, text="",
            font=ctk.CTkFont("Segoe UI", 10),
            text_color="#ffaa00"
        )
        self._calib_label.pack(side="left", padx=8)

        # ── Row B: quick-action buttons ───────────────────────────────────────
        row_b = ctk.CTkFrame(inner, fg_color="transparent")
        row_b.pack(fill="x")

        for label, cmd in [
            ("📸 Screenshot", "take a screenshot"),
            ("🔋 Battery",    "battery status"),
            ("🔒 Lock",       "lock screen"),
        ]:
            ctk.CTkButton(
                row_b, text=label, width=110, height=26,
                font=ctk.CTkFont("Segoe UI", 11),
                fg_color="#1e1e3e", hover_color="#2a2a5a",
                text_color=TEXT_MAIN, corner_radius=8,
                command=lambda c=cmd: self._inject_command(c)
            ).pack(side="left", padx=(0, 6))

    def _build_input_bar(self):
        """
        Text input + mic button + send — always visible at the very bottom.
        Packed side=bottom BEFORE the expanding chat section.
        """
        bar = ctk.CTkFrame(self, fg_color=BG_SECONDARY, corner_radius=0)
        bar.pack(fill="x", side="bottom")

        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=8)

        # ── Text entry ────────────────────────────────────────────────────────
        self._input_box = ctk.CTkEntry(
            inner,
            placeholder_text="Type a command and press Enter or click Send…",
            font=ctk.CTkFont("Segoe UI", 13),
            fg_color="#0d0d2e",
            border_color=ACCENT,
            text_color=TEXT_MAIN,
            height=38,
            corner_radius=10,
        )
        self._input_box.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._input_box.bind("<Return>", self._on_text_submit)

        # ── Mic / voice button ────────────────────────────────────────────────
        self._mic_btn = ctk.CTkButton(
            inner,
            text="🎤",
            width=44, height=38,
            font=ctk.CTkFont("Segoe UI", 18),
            fg_color="#1e1e3e",
            hover_color="#2a2a5a",
            text_color=ACCENT,
            corner_radius=10,
            command=self._on_mic_btn_click,
        )
        self._mic_btn.pack(side="left", padx=(0, 6))

        # ── Send button ───────────────────────────────────────────────────────
        ctk.CTkButton(
            inner,
            text="Send",
            width=70, height=38,
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            fg_color=ACCENT,
            hover_color="#3a7fdd",
            text_color=TEXT_BRIGHT,
            corner_radius=10,
            command=self._on_text_submit,
        ).pack(side="left")

    # ═══════════════════════════════════════════════════════════════════════════
    # Public API (called from main thread via after())
    # ═══════════════════════════════════════════════════════════════════════════

    def set_status(self, state: str):
        """Update orb + status pill + mic button. Must be called on UI thread."""
        self._orb.set_state(state)
        label, color = self.STATUS_LABELS.get(state, ("● IDLE", ACCENT))
        self._status_label.configure(text=label, text_color=color)
        self._update_mic_btn(state)

    def add_chat(self, text: str, sender: str = "dodo"):
        bubble = ChatBubble(self._chat_scroll, text, sender)
        bubble.pack(fill="x", padx=6, pady=3)
        self.after(50, lambda: self._chat_scroll._parent_canvas.yview_moveto(1.0))

    def add_log(self, action_type: str, text: str):
        entry = LogEntry(self._log_scroll, action_type, text)
        entry.pack(fill="x", padx=4, pady=1)
        self.after(50, lambda: self._log_scroll._parent_canvas.yview_moveto(1.0))

    def ask_confirm(self, question: str) -> bool:
        return tk.messagebox.askyesno("DODO — Confirm", question, parent=self)

    def set_ui_queue(self, q: queue.Queue):
        self._ui_queue = q

    # ═══════════════════════════════════════════════════════════════════════════
    # Internal callbacks
    # ═══════════════════════════════════════════════════════════════════════════

    def _calibrate_mic(self):
        if not self.voice_engine:
            return
        self._calib_label.configure(text="Calibrating… stay quiet")
        def _run():
            threshold = self.voice_engine.calibrate(duration=2.5)
            self.after(0, lambda: self._calib_label.configure(
                text=f"✓ {threshold:.4f}"
            ))
            self.after(0, lambda: self.add_log(
                "system_command",
                f"Mic calibrated — threshold: {threshold:.4f}"
            ))
        threading.Thread(target=_run, daemon=True).start()

    def _test_voice(self):
        from core import speech
        self._calib_label.configure(text="Speaking…")
        def _done():
            self.after(0, lambda: self._calib_label.configure(text=""))
        speech.speak(
            "Hi! I am DODO, your AI desktop assistant. Voice is working perfectly.",
            on_done=_done
        )

    def _inject_command(self, cmd: str):
        """Push a text command as if spoken — shows in chat and processes it."""
        self.command_queue.put(cmd)

    def _on_text_submit(self, event=None):
        """Send the typed text as a command."""
        text = self._input_box.get().strip()
        if text:
            self._input_box.delete(0, "end")
            self.command_queue.put(text)

    # ── Mic button ─────────────────────────────────────────────────────────────

    def _on_mic_btn_click(self):
        """Click = skip wake word, go straight to LISTENING for one command."""
        if not self.voice_engine:
            return
        if not self.voice_engine.mic_enabled:
            # Flash red to signal mic is disabled
            self._mic_btn.configure(fg_color=DANGER, text_color=TEXT_BRIGHT)
            self.after(600, lambda: self._mic_btn.configure(
                fg_color="#1e1e3e", text_color=TEXT_DIM
            ))
            return
        if self.voice_engine.state == "LISTENING":
            return   # already listening
        self.voice_engine.listen_once()

    def _update_mic_btn(self, state: str):
        """Sync mic button appearance to current voice engine state."""
        if not hasattr(self, "_mic_btn"):
            return
        mic_on = (self.voice_engine.mic_enabled if self.voice_engine else True)

        if state == "LISTENING":
            self._mic_btn.configure(
                fg_color="#cc1a3a", hover_color="#aa1530",
                text_color=TEXT_BRIGHT, text="🔴"
            )
            self._mic_btn_listening = True
            self._pulse_mic_btn()
        elif state in ("PROCESSING", "SPEAKING"):
            self._mic_btn.configure(
                fg_color="#2a1a4a", hover_color="#2a1a4a",
                text_color=ACCENT2, text="🎤"
            )
            self._mic_btn_listening = False
        elif not mic_on or state == "MIC_ERROR":
            self._mic_btn.configure(
                fg_color="#1a1a1a", hover_color="#1a1a1a",
                text_color="#444444", text="🎤"
            )
            self._mic_btn_listening = False
        else:
            # IDLE / CALIBRATING
            self._mic_btn.configure(
                fg_color="#1e1e3e", hover_color="#2a2a5a",
                text_color=ACCENT, text="🎤"
            )
            self._mic_btn_listening = False

    def _pulse_mic_btn(self):
        """Alternating pulse animation while LISTENING."""
        if not self._mic_btn_listening:
            return
        current = self._mic_btn.cget("fg_color")
        next_c  = "#aa1530" if current == "#cc1a3a" else "#cc1a3a"
        self._mic_btn.configure(fg_color=next_c)
        self.after(400, self._pulse_mic_btn)

    def _on_mic_toggle(self, enabled: bool):
        if self.voice_engine:
            self.voice_engine.set_mic_enabled(enabled)
        self._update_mic_btn("IDLE")

    def _on_online_toggle(self, enabled: bool):
        if self.voice_engine:
            self.voice_engine.set_online_mode(enabled)

    # ── Queue polling ──────────────────────────────────────────────────────────

    def _poll_queue(self):
        """Drain the UI event queue on the main thread (thread-safe)."""
        try:
            while True:
                try:
                    event = self._ui_queue.get_nowait()
                except queue.Empty:
                    break

                event_type = event.get("type")
                try:
                    if event_type == "status":
                        self.set_status(event["state"])
                    elif event_type == "chat":
                        self.add_chat(event["text"], event.get("sender", "dodo"))
                    elif event_type == "log":
                        self.add_log(event.get("action_type", "unknown"), event["text"])
                except Exception:
                    pass
        except Exception:
            pass
        self.after(80, self._poll_queue)   # poll every 80ms (was 100ms)

    # ── Drag ──────────────────────────────────────────────────────────────────
    def _drag_start(self, event):
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _drag_motion(self, event):
        self.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    # ── System tray ───────────────────────────────────────────────────────────
    def _start_tray(self):
        img  = self._make_tray_icon()
        menu = pystray.Menu(
            pystray.MenuItem("Show DODO", self._tray_show, default=True),
            pystray.MenuItem("Exit",      self._tray_exit),
        )
        self._tray_icon = pystray.Icon("DODO", img, "DODO", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _make_tray_icon(self) -> Image.Image:
        img  = Image.new("RGB", (64, 64), "#0d0d1a")
        draw = ImageDraw.Draw(img)
        draw.ellipse([8, 8, 56, 56], fill="#4f9fff")
        draw.ellipse([20, 20, 44, 44], fill="#0d0d1a")
        draw.ellipse([26, 26, 38, 38], fill="#9b4fff")
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
        import os
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "assets", "dodo_icon.ico"
        )
