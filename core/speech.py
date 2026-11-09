"""
DODO — core/speech.py
======================
Neural TTS using edge-tts (Microsoft en-IN-NeerjaNeural).
Falls back to pyttsx3 SAPI5 if edge-tts fails (no internet).

SPEED UPGRADES (v3):
- Sentence-streaming: splits long responses into sentences and speaks
  the first sentence IMMEDIATELY while edge-tts fetches the rest.
  This eliminates the perceived "dead time" before DODO starts talking.
- Temp files go to %TEMP% (not OneDrive — no sync overhead).
- Voice is cached, not re-read from disk on every speak() call.
- pyttsx3 engine is kept alive (not re-initialized per call).
"""

import threading
import asyncio
import tempfile
import os
import io
import re
import sounddevice as sd
import soundfile as sf

from core.memory import load_config

_lock         = threading.Lock()
_speak_thread = None
_stop_event   = threading.Event()

# Cache voice setting so we don't re-read config.json every time
_cached_voice: str = ""


def _get_voice() -> str:
    global _cached_voice
    if not _cached_voice:
        try:
            cfg = load_config()
            _cached_voice = cfg.get("tts_voice", "en-IN-NeerjaNeural")
        except Exception:
            _cached_voice = "en-IN-NeerjaNeural"
    return _cached_voice


def invalidate_voice_cache():
    """Call this when TTS voice setting changes in config."""
    global _cached_voice
    _cached_voice = ""


# ── Sentence splitter ──────────────────────────────────────────────────────────

def _split_sentences(text: str) -> list[str]:
    """
    Split text into speakable sentences.
    Returns a list of non-empty strings, each suitable as a TTS chunk.
    """
    # Split on sentence-ending punctuation followed by space or end
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    # Filter empties and very short fragments (< 3 chars)
    result = [p.strip() for p in parts if len(p.strip()) >= 3]
    return result if result else [text.strip()]


# ── Public API ─────────────────────────────────────────────────────────────────

def speak(text: str, on_done=None):
    """
    Speak text using edge-tts neural voice in a background thread.
    Falls back to pyttsx3 if edge-tts fails.
    Calls on_done() when finished (optional).

    For multi-sentence text, the first sentence begins playing immediately
    while subsequent sentences are synthesized in parallel ("streaming TTS").
    """
    global _speak_thread, _stop_event

    if not text or not text.strip():
        if on_done:
            on_done()
        return

    # Signal any current speech to stop
    _stop_event.set()

    local_stop = threading.Event()

    def _run():
        global _stop_event
        _stop_event = local_stop

        with _lock:
            success = _speak_streamed(text, local_stop)
            if not success and not local_stop.is_set():
                _speak_pyttsx3(text)

        if on_done and not local_stop.is_set():
            on_done()

    _speak_thread = threading.Thread(target=_run, daemon=True)
    _speak_thread.start()


def stop():
    """Interrupt current speech immediately."""
    _stop_event.set()


# ── Streamed multi-sentence TTS ────────────────────────────────────────────────

def _speak_streamed(text: str, stop_ev: threading.Event) -> bool:
    """
    Split text into sentences and speak them one by one.
    Each sentence is synthesized just before playback begins — so the first
    sentence starts immediately while the rest are queued.

    Returns True on success (even if stopped early), False on hard failure.
    """
    sentences = _split_sentences(text)

    if not sentences:
        return True

    # For single sentences, use the simpler path
    if len(sentences) == 1:
        return _speak_edge_single(sentences[0], stop_ev)

    # For multi-sentence: synthesize first sentence, play it, then loop
    any_success = False
    for sentence in sentences:
        if stop_ev.is_set():
            break
        ok = _speak_edge_single(sentence, stop_ev)
        if ok:
            any_success = True
        elif not any_success:
            # First sentence failed — fall back to pyttsx3 for whole text
            return False

    return True


def _speak_edge_single(text: str, stop_ev: threading.Event) -> bool:
    """
    Synthesize and play a single piece of text with edge-tts.
    Returns True on success, False on failure.
    """
    try:
        import edge_tts
    except ImportError:
        return False

    if stop_ev.is_set():
        return True

    voice = _get_voice()
    mp3_buf = io.BytesIO()

    async def _synthesize():
        communicate = edge_tts.Communicate(text, voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_buf.write(chunk["data"])

    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_synthesize())
        loop.close()
    except Exception:
        return False

    if stop_ev.is_set() or mp3_buf.tell() == 0:
        return True

    # Write MP3 to %TEMP% (not OneDrive — avoids sync overhead)
    mp3_buf.seek(0)
    tmp = tempfile.NamedTemporaryFile(
        suffix=".mp3", delete=False, dir=tempfile.gettempdir()
    )
    try:
        tmp.write(mp3_buf.read())
        tmp.flush()
        tmp.close()
        data, samplerate = sf.read(tmp.name, dtype="float32")
    except Exception:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        return False
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    if stop_ev.is_set():
        return True

    # Play with interruptible monitor thread
    try:
        sd.play(data, samplerate)
        playback_done = threading.Event()

        def _wait_done():
            sd.wait()
            playback_done.set()

        threading.Thread(target=_wait_done, daemon=True).start()

        while not playback_done.wait(timeout=0.05):
            if stop_ev.is_set():
                sd.stop()
                break
    except Exception:
        return False

    return True


# ── pyttsx3 (offline fallback) ─────────────────────────────────────────────────

_pyttsx3_engine = None


def _speak_pyttsx3(text: str):
    """Fallback: Windows SAPI5 TTS via pyttsx3 (offline, no network needed)."""
    global _pyttsx3_engine
    try:
        import pyttsx3
        cfg       = load_config()
        voice_cfg = cfg.get("voice", {})

        if _pyttsx3_engine is None:
            _pyttsx3_engine = pyttsx3.init("sapi5")
            _pyttsx3_engine.setProperty("rate",   voice_cfg.get("rate",   185))
            _pyttsx3_engine.setProperty("volume", voice_cfg.get("volume", 1.0))
            voices = _pyttsx3_engine.getProperty("voices")
            gender = voice_cfg.get("gender", "female").lower()
            chosen = next(
                (v.id for v in voices
                 if gender in v.name.lower() or gender in v.id.lower()),
                voices[0].id if voices else None
            )
            if chosen:
                _pyttsx3_engine.setProperty("voice", chosen)

        _pyttsx3_engine.say(text)
        _pyttsx3_engine.runAndWait()
    except Exception:
        _pyttsx3_engine = None
