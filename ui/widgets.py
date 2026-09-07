"""
DODO — ui/widgets.py
Custom reusable UI components for the control panel.
"""

import customtkinter as ctk
import tkinter as tk
import math
import time


# ── Pulsing Orb ───────────────────────────────────────────────────────────────

class PulsingOrb(tk.Canvas):
    """Animated orb that pulses to reflect DODO's state."""

    COLORS = {
        "IDLE":        ("#1a1a3e", "#2a2a6e", "#4f9fff"),
        "LISTENING":   ("#002244", "#004488", "#00aaff"),
        "PROCESSING":  ("#1a0044", "#3300aa", "#9b4fff"),
        "SPEAKING":    ("#1a0033", "#440066", "#cc44ff"),
        "CALIBRATING": ("#2a1a00", "#664400", "#ffaa00"),
        "MIC_ERROR":   ("#330000", "#660000", "#ff4444"),
    }

    def __init__(self, parent, size=180, **kwargs):
        super().__init__(parent, width=size, height=size,
                         bg="#0d0d1a", highlightthickness=0, **kwargs)
        self.size       = size
        self.cx         = size // 2
        self.cy         = size // 2
        self.state_name = "IDLE"
        self._rings     = []
        self._tick      = 0
        self._animating  = True
        self._draw_frame()
        self._animate()

    def set_state(self, state: str):
        self.state_name = state
        self._tick      = 0  # reset animation phase

    def _animate(self):
        if not self._animating:
            return
        self._tick  += 1
        self._draw_frame()
        self.after(40, self._animate)   # ~25 fps

    def _draw_frame(self):
        self.delete("all")
        colors = self.COLORS.get(self.state_name, self.COLORS["IDLE"])
        t      = self._tick
        mode   = self.state_name

        # Speed of pulse per state
        speeds = {"IDLE": 0.03, "LISTENING": 0.12, "PROCESSING": 0.10,
                  "SPEAKING": 0.08, "MIC_ERROR": 0.15}
        speed  = speeds.get(mode, 0.04)
        phase  = math.sin(t * speed)

        # Background glow rings (ripple outward)
        n_rings = 3 if mode in ("LISTENING", "PROCESSING") else 2
        for i in range(n_rings):
            ring_phase = math.sin(t * speed + i * 1.5)
            r = self.cx * (0.55 + 0.18 * i + 0.05 * ring_phase)
            alpha_factor = max(0, 0.5 - i * 0.15 + 0.1 * ring_phase)
            color = self._blend(colors[0], colors[1], alpha_factor)
            self._draw_circle(self.cx, self.cy, r, fill=color, outline="")

        # Core orb
        core_r = self.cx * (0.42 + 0.04 * phase)
        self._draw_circle(self.cx, self.cy, core_r, fill=colors[1], outline="")

        # Inner bright spot
        spot_r = self.cx * (0.28 + 0.02 * phase)
        self._draw_circle(self.cx, self.cy, spot_r, fill=colors[2], outline="")

        # Highlight reflection (subtle light spot — no alpha in tkinter)
        self._draw_circle(
            self.cx - core_r * 0.3, self.cy - core_r * 0.3,
            core_r * 0.18, fill="#aaccff", outline=""
        )

    def _draw_circle(self, x, y, r, **kwargs):
        self.create_oval(x - r, y - r, x + r, y + r, **kwargs)

    @staticmethod
    def _blend(c1: str, c2: str, t: float) -> str:
        """Blend two hex colours by factor t ∈ [0,1]."""
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
    """A single message in the chat log."""

    def __init__(self, parent, text: str, sender: str = "dodo", **kwargs):
        # sender: 'dodo' | 'user'
        is_dodo = sender == "dodo"
        bg      = "#1e1e3a" if is_dodo else "#162032"
        super().__init__(parent, fg_color=bg, corner_radius=12, **kwargs)

        prefix_color = "#4f9fff" if is_dodo else "#00e5a0"
        prefix_text  = "⬡ DODO" if is_dodo else "👤 You"

        ctk.CTkLabel(
            self, text=prefix_text,
            font=ctk.CTkFont("Consolas", 11, "bold"),
            text_color=prefix_color
        ).pack(anchor="w", padx=12, pady=(8, 0))

        ctk.CTkLabel(
            self, text=text, wraplength=340,
            font=ctk.CTkFont("Segoe UI", 13),
            text_color="#d0d8f0", justify="left"
        ).pack(anchor="w", padx=12, pady=(2, 10))


# ── Log Entry ──────────────────────────────────────────────────────────────────

class LogEntry(ctk.CTkFrame):
    """One line in the action log."""

    ICON = {"system_command": "*", "internet_action": "@",
             "file_operation": "F", "device_control": "D",
             "small_talk": ">", "ollama_chat": ">", "unknown": "?"}

    def __init__(self, parent, action_type: str, text: str, **kwargs):
        super().__init__(parent, fg_color="#111128", corner_radius=6, **kwargs)
        icon = self.ICON.get(action_type, "▸")
        ts   = time.strftime("%H:%M:%S")

        ctk.CTkLabel(
            self,
            text=f"{icon}  {ts}  {text}",
            font=ctk.CTkFont("Consolas", 11),
            text_color="#7888bb",
            anchor="w"
        ).pack(side="left", padx=10, pady=4)


# ── Toggle Switch ──────────────────────────────────────────────────────────────

class ToggleSwitch(ctk.CTkFrame):
    """ON/OFF toggle with label."""

    def __init__(self, parent, label: str, initial: bool = True,
                 on_change=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._on_change = on_change

        ctk.CTkLabel(
            self, text=label,
            font=ctk.CTkFont("Segoe UI", 12),
            text_color="#8899cc"
        ).pack(side="left", padx=(0, 8))

        self._switch = ctk.CTkSwitch(
            self, text="",
            width=44, height=22,
            command=self._changed,
            fg_color="#2a2a4a",
            progress_color="#4f9fff",
            button_color="#ffffff",
            button_hover_color="#ccddff",
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
