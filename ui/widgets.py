"""
DODO — ui/widgets.py
Custom reusable UI components — teal dark theme.
"""

import customtkinter as ctk
import tkinter as tk
import math
import time


# ── Pulsing Orb ───────────────────────────────────────────────────────────────

class PulsingOrb(tk.Canvas):
    """Animated orb that pulses to reflect DODO's state — teal palette."""

    COLORS = {
        "IDLE":        ("#0a1a1a", "#0d3d35", "#00d4aa"),
        "LISTENING":   ("#001a2a", "#003355", "#00aaff"),
        "PROCESSING":  ("#1a0044", "#3300aa", "#9b4fff"),
        "SPEAKING":    ("#0a2a2a", "#0d4d40", "#00ffcc"),
        "CALIBRATING": ("#2a1a00", "#664400", "#ffaa00"),
        "MIC_ERROR":   ("#330000", "#660000", "#ff4444"),
    }

    def __init__(self, parent, size=180, **kwargs):
        bg = kwargs.pop("bg", "#0a0e14")
        super().__init__(parent, width=size, height=size,
                         bg=bg, highlightthickness=0, **kwargs)
        self.size       = size
        self.cx         = size // 2
        self.cy         = size // 2
        self.state_name = "IDLE"
        self._tick      = 0
        self._animating = True
        self._draw_frame()
        self._animate()

    def set_state(self, state: str):
        self.state_name = state
        self._tick = 0

    def _animate(self):
        if not self._animating:
            return
        self._tick += 1
        self._draw_frame()
        self.after(40, self._animate)

    def _draw_frame(self):
        self.delete("all")
        colors = self.COLORS.get(self.state_name, self.COLORS["IDLE"])
        t      = self._tick
        speeds = {"IDLE": 0.03, "LISTENING": 0.12, "PROCESSING": 0.10,
                  "SPEAKING": 0.08, "MIC_ERROR": 0.15}
        speed  = speeds.get(self.state_name, 0.04)
        phase  = math.sin(t * speed)

        n_rings = 3 if self.state_name in ("LISTENING", "PROCESSING") else 2
        for i in range(n_rings):
            ring_phase = math.sin(t * speed + i * 1.5)
            r = self.cx * (0.55 + 0.18 * i + 0.05 * ring_phase)
            alpha_factor = max(0, 0.5 - i * 0.15 + 0.1 * ring_phase)
            color = self._blend(colors[0], colors[1], alpha_factor)
            self._draw_circle(self.cx, self.cy, r, fill=color, outline="")

        core_r = self.cx * (0.42 + 0.04 * phase)
        self._draw_circle(self.cx, self.cy, core_r, fill=colors[1], outline="")

        spot_r = self.cx * (0.28 + 0.02 * phase)
        self._draw_circle(self.cx, self.cy, spot_r, fill=colors[2], outline="")

        self._draw_circle(
            self.cx - core_r * 0.3, self.cy - core_r * 0.3,
            core_r * 0.18, fill="#aaccff", outline=""
        )

    def _draw_circle(self, x, y, r, **kwargs):
        self.create_oval(x - r, y - r, x + r, y + r, **kwargs)

    @staticmethod
    def _blend(c1: str, c2: str, t: float) -> str:
        t  = max(0.0, min(1.0, t))
        r1, g1, b1 = int(c1[1:3],16), int(c1[3:5],16), int(c1[5:7],16)
        r2, g2, b2 = int(c2[1:3],16), int(c2[3:5],16), int(c2[5:7],16)
        return "#{:02x}{:02x}{:02x}".format(
            int(r1 + (r2-r1)*t), int(g1 + (g2-g1)*t), int(b1 + (b2-b1)*t)
        )

    def destroy(self):
        self._animating = False
        super().destroy()


# ── Chat Bubble ───────────────────────────────────────────────────────────────

class ChatBubble(ctk.CTkFrame):
    """A chat message bubble — DODO left (dark) / user right (blue)."""

    def __init__(self, parent, text: str, sender: str = "dodo", **kwargs):
        is_dodo = sender == "dodo"
        bg = "#1a1f2e" if is_dodo else "#1a3a5c"
        super().__init__(parent, fg_color=bg, corner_radius=14, **kwargs)

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=8)

        if is_dodo:
            ctk.CTkLabel(
                inner, text="◈", font=ctk.CTkFont("Segoe UI", 18),
                text_color="#00d4aa", width=24
            ).pack(side="left", anchor="n", padx=(0, 8), pady=(2, 0))

        text_col = ctk.CTkFrame(inner, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True)

        prefix_color = "#00d4aa" if is_dodo else "#5a9fd4"
        prefix_text  = "DODO" if is_dodo else "You"
        ctk.CTkLabel(
            text_col, text=prefix_text,
            font=ctk.CTkFont("Consolas", 10, "bold"),
            text_color=prefix_color
        ).pack(anchor="w")

        ctk.CTkLabel(
            text_col, text=text, wraplength=420,
            font=ctk.CTkFont("Segoe UI", 13),
            text_color="#e0e6f0", justify="left"
        ).pack(anchor="w", pady=(2, 0))


# ── Log Entry ─────────────────────────────────────────────────────────────────

class LogEntry(ctk.CTkFrame):
    """One line in the activity log — teal accents."""

    ICON = {
        "system_command": "⚡", "internet_action": "🌐",
        "file_operation": "📁", "device_control": "🔧",
        "small_talk": "💬", "agent": "🤖",
        "reminder": "⏰", "unknown": "▸",
    }

    def __init__(self, parent, action_type: str, text: str, **kwargs):
        super().__init__(parent, fg_color="#151b25", corner_radius=6, **kwargs)
        icon = self.ICON.get(action_type, "▸")
        ts   = time.strftime("%H:%M")
        ctk.CTkLabel(
            self, text=f"{icon}  {ts}  {text}",
            font=ctk.CTkFont("Consolas", 11),
            text_color="#5a6580", anchor="w"
        ).pack(side="left", padx=10, pady=4)


# ── Toggle Switch ─────────────────────────────────────────────────────────────

class ToggleSwitch(ctk.CTkFrame):
    """ON/OFF toggle with label — teal theme."""

    def __init__(self, parent, label: str, initial: bool = True,
                 on_change=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._on_change = on_change

        ctk.CTkLabel(
            self, text=label,
            font=ctk.CTkFont("Segoe UI", 12),
            text_color="#5a6580"
        ).pack(side="left", padx=(0, 8))

        self._switch = ctk.CTkSwitch(
            self, text="", width=44, height=22,
            command=self._changed,
            fg_color="#1c2433",
            progress_color="#00d4aa",
            button_color="#ffffff",
            button_hover_color="#ccffee",
        )
        self._switch.pack(side="left")
        if initial:
            self._switch.select()
        else:
            self._switch.deselect()

    def _changed(self):
        if self._on_change:
            self._on_change(self._switch.get() == 1)

    def get(self) -> bool:
        return self._switch.get() == 1
