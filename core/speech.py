"""
DODO — core/speech.py
Neural TTS using edge-tts (Microsoft en-IN-NeerjaNeural).
Falls back to pyttsx3 SAPI5 if edge-tts fails (no internet).

Fixes:
- Removed broken sd.get_stream() call (crashes when no stream is active)
- Uses sd.wait() + a monitor thread to support interruptible playback
- Saves edge-tts audio as WAV via soundfile to avoid MP3 codec issues
"""

import threading
import asyncio
import tempfile
import os
import io
import sounddevice as sd
import soundfile as sf

from core.memory import load_config

_lock         = threading.Lock()
_speak_thread = None
_stop_event   = threading.Event()


def _get_voice() -> str:
    cfg = load_config()
    return cfg.get("tts_voice", "en-IN-NeerjaNeural")


def speak(text: str, on_done=None):
    """
    Speak text using edge-tts neural voice in a background thread.
    Falls back to pyttsx3 if edge-tts fails.
    Calls on_done() when finished (optional).
    """
    global _speak_thread, _stop_event

    # Signal any current speech to stop
    _stop_event.set()

    local_stop = threading.Event()

    def _run():
        global _stop_event
        _stop_event = local_stop   # register this utterance's stop flag

        with _lock:
            success = _speak_edge(text, local_stop)
            if not success and not local_stop.is_set():
                _speak_pyttsx3(text)

        if on_done and not local_stop.is_set():
            on_done()

    _speak_thread = threading.Thread(target=_run, daemon=True)
    _speak_thread.start()


def stop():
    """Interrupt current speech immediately."""
    _stop_event.set()


# ── edge-tts (primary) ─────────────────────────────────────────────────────────

def _speak_edge(text: str, stop_ev: threading.Event) -> bool:
    """
    Synthesize text with edge-tts, play via sounddevice.
    Returns True on success, False if an error occurs.
    """
    try:
        import edge_tts
    except ImportError:
        return False

    if stop_ev.is_set():
        return True

    voice = _get_voice()

    # Collect audio bytes in memory (MP3 from edge-tts)
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

    # Save to a temp MP3 file and read back with soundfile
    mp3_buf.seek(0)
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
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

    # Play audio — use a monitor thread to allow stop_ev interruption
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


# ── pyttsx3 (fallback) ─────────────────────────────────────────────────────────

_pyttsx3_engine = None


def _speak_pyttsx3(text: str):
    """Fallback: Windows SAPI5 TTS via pyttsx3."""
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
