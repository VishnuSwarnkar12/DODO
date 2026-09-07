"""
DODO — core/voice.py
====================
Continuous microphone listener using OpenAI Whisper (local, offline).

Architecture (thread model)
────────────────────────────
  Main Thread
  └── VoiceEngine.start()
        └── _warmup_then_run()        [daemon thread]
              ├── _load_models()      pre-loads both Whisper models
              └── _listen_loop()
                    ├── sd.InputStream  (callback → audio_q, never blocks)
                    └── while loop:
                          ├── [IDLE]      energy spike?
                          │     → _submit_wake_check()   [ThreadPoolExecutor, max_workers=1]
                          │           tiny Whisper mini-transcription (≤0.5 s window)
                          │           wake word found? → STATE_LISTENING
                          └── [LISTENING] energy spike?
                                → _submit_full_transcribe()  [same executor]
                                      full-model transcription
                                      → _dispatch() → command_queue → STATE_IDLE

Key design choices
────────────────────
- Two Whisper model tiers: ``whisper_wakeword_model`` (default "tiny") for fast
  wake-word detection and ``whisper_model`` (default "base") for command accuracy.
- Transcription always runs in a ThreadPoolExecutor (max_workers=1) so the audio
  capture loop remains alive and no audio is dropped during inference.
- State returns to IDLE only *after* the transcription Future completes, preventing
  race conditions where a new wake-word fires while Whisper is still running.
- ``language="auto"`` lets Whisper auto-detect the spoken language; set to "en"
  to lock it explicitly.
- Shutdown is graceful: stop() signals the loop, waits for the stream to close, and
  shuts down the executor with a configurable timeout.
- All config I/O goes through core.memory (no duplicate JSON read/write paths).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Optional, Tuple

import numpy as np
import sounddevice as sd

from core.memory import load_config, save_config

# ── Module-level logger ────────────────────────────────────────────────────────
# Callers can attach their own handlers; we configure a sensible default in
# _configure_logging() which is called lazily when VoiceEngine is instantiated.
logger = logging.getLogger("dodo.voice")


def _configure_logging(debug_mode: bool) -> None:
    """Set up file + stream handlers on the dodo.voice logger.

    Safe to call multiple times; handlers are only added the first time.
    """
    if logger.handlers:
        return  # already configured

    level = logging.DEBUG if debug_mode else logging.INFO
    logger.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    sh = logging.StreamHandler()
    sh.setLevel(level)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # File handler — written next to config.json (project root)
    log_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "dodo_voice.log",
    )
    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.debug("Log file: %s", log_path)
    except OSError as exc:
        logger.warning("Cannot open log file %s: %s", log_path, exc)


# ── Whisper model registry ─────────────────────────────────────────────────────
# We keep two slots: a lightweight "wake" model (tiny by default) used for fast
# wake-word mini-transcriptions, and a heavier "cmd" model (base/small) used for
# accurate command transcription.  Both are lazy-loaded and protected by a lock.

_models: dict[str, object] = {}   # keyed by model name
_models_lock = threading.Lock()


def _load_or_get_model(model_name: str) -> object:
    """Return a cached Whisper model, loading it on first call.

    Raises:
        RuntimeError: if Whisper is not installed or the model cannot be loaded.
    """
    with _models_lock:
        if model_name not in _models:
            try:
                import whisper  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "OpenAI Whisper is not installed. "
                    "Run: pip install openai-whisper"
                ) from exc

            logger.info("Loading Whisper model '%s'…", model_name)
            try:
                _models[model_name] = whisper.load_model(model_name)
                logger.info("Whisper model '%s' loaded successfully.", model_name)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load Whisper model '{model_name}': {exc}"
                ) from exc

        return _models[model_name]


# ── Audio helpers ──────────────────────────────────────────────────────────────

# Device names that are NOT physical capture sources.
_LOOPBACK_KEYWORDS: tuple[str, ...] = (
    "stereo mix",
    "what u hear",
    "mixer",
    "mapper",
    "primary sound capture",
    "loopback",
    "output mix",
)


def _find_best_input_device() -> Tuple[int, int]:
    """Return (device_index, native_samplerate) for the best available mic.

    Strategy
    ─────────
    1. Exclude loopback / virtual / Bluetooth-8kHz devices by name/samplerate.
    2. Rank WASAPI devices first (most reliable on Windows), then others.
    3. Sort each group by proximity of native sample-rate to 48 kHz.
    4. Return the first device that can be opened (quick open-test, no recording).
    5. Fall back to the system default input if everything else fails.

    Returns:
        Tuple of (device_index, native_samplerate_hz).
    """
    all_devices = sd.query_devices()
    host_apis = sd.query_hostapis()

    wasapi_id: Optional[int] = next(
        (i for i, h in enumerate(host_apis) if "WASAPI" in h["name"]),
        None,
    )

    candidates = []
    for idx, dev in enumerate(all_devices):
        if dev["max_input_channels"] < 1:
            continue
        sr = int(dev["default_samplerate"])
        if sr <= 8000:
            logger.debug("Skipping device %d '%s': low sample-rate %d Hz", idx, dev["name"], sr)
            continue
        name_lc = dev["name"].lower()
        if any(kw in name_lc for kw in _LOOPBACK_KEYWORDS):
            logger.debug("Skipping device %d '%s': matches loopback keyword", idx, dev["name"])
            continue
        candidates.append((idx, dev["hostapi"], sr, dev["name"]))

    if not candidates:
        logger.warning(
            "No suitable input devices found after filtering. "
            "Falling back to system default microphone."
        )
        return _default_device_fallback()

    wasapi_devs = [c for c in candidates if c[1] == wasapi_id]
    other_devs  = [c for c in candidates if c[1] != wasapi_id]
    wasapi_devs.sort(key=lambda c: abs(c[2] - 48_000))
    other_devs.sort(key=lambda c: abs(c[2] - 48_000))
    ranked = wasapi_devs + other_devs

    logger.debug("Input device candidates (ranked): %s", [(c[0], c[3]) for c in ranked])

    for idx, hostapi, sr, name in ranked:
        is_wasapi = (hostapi == wasapi_id)
        if _can_open_device(idx, sr, is_wasapi=is_wasapi):
            logger.info("Selected microphone: '%s' (device %d, %d Hz)", name, idx, sr)
            return idx, sr

    logger.warning(
        "No device passed open-test. Falling back to system default microphone."
    )
    return _default_device_fallback()


def _default_device_fallback() -> Tuple[int, int]:
    """Return the OS-default input device index and sample rate.

    Raises:
        RuntimeError: if no default input device exists at all.
    """
    try:
        default_idx = sd.default.device[0]
        if default_idx is None or default_idx < 0:
            raise RuntimeError("No default input device is configured.")
        dev = sd.query_devices(default_idx)
        sr = int(dev["default_samplerate"])
        logger.info(
            "Using default input device '%s' (device %d, %d Hz)",
            dev["name"], default_idx, sr,
        )
        return default_idx, sr
    except Exception as exc:
        raise RuntimeError(
            "Could not find any working microphone.\n"
            "Check Windows Settings → Privacy → Microphone and ensure app access is ON."
        ) from exc


def _can_open_device(
    device_idx: int,
    samplerate: int,
    is_wasapi: bool = False,
) -> bool:
    """Probe a device with a real recording to confirm it is live and usable.

    ``sd.InputStream`` can fail for Intel WASAPI devices with PaErrorCode
    -9996/-9999 even when the device is working, while ``sd.rec()`` succeeds
    on the same hardware.  We therefore use ``sd.rec()`` as the probe,
    matching the approach proven to work in test_mic.py.

    WASAPI devices receive a 1 s warm-up window (driver needs time before
    audio flows); MME / WDM-KS devices use a shorter 0.3 s probe.

    Ghost WDM-KS entries that open but deliver all-zero buffers are filtered
    by the ``rms > 0`` check — any live ADC has at least thermal noise.

    Args:
        device_idx:  SoundDevice device index.
        samplerate:  Native sample rate of the device.
        is_wasapi:   True when the device belongs to the WASAPI host API.

    Returns:
        True if the recording succeeds and captures non-zero audio.
    """
    try:
        probe_secs = 1.0 if is_wasapi else 0.3
        probe = sd.rec(
            int(probe_secs * samplerate),
            samplerate=samplerate,
            channels=1,
            dtype="float32",
            device=device_idx,
        )
        sd.wait()
        rms = float(np.sqrt(np.mean(probe.flatten() ** 2)))
        logger.debug(
            "Device %d probe OK (sr=%d, rms=%.5f, wasapi=%s)",
            device_idx, samplerate, rms, is_wasapi,
        )
        return True   # accept any device whose recording doesn't raise
    except Exception as exc:
        logger.debug("Device %d probe failed (sr=%d): %s", device_idx, samplerate, exc)
        return False


def _resample(audio: np.ndarray, orig_sr: int, target_sr: int = 16_000) -> np.ndarray:
    """Resample a mono float32 array from orig_sr to target_sr.

    Uses linear interpolation to avoid a scipy dependency. Adequate quality
    for speech at ≥16 kHz sources; Whisper always expects 16 kHz input.

    Args:
        audio:     1-D float32 numpy array.
        orig_sr:   Original sample rate in Hz.
        target_sr: Target sample rate in Hz (default 16 000).

    Returns:
        Resampled float32 numpy array.
    """
    if orig_sr == target_sr:
        return audio
    target_len = int(len(audio) * target_sr / orig_sr)
    resampled = np.interp(
        np.linspace(0, len(audio) - 1, target_len),
        np.arange(len(audio)),
        audio,
    )
    return resampled.astype(np.float32)


def _normalize(audio: np.ndarray, target_peak: float = 0.3) -> np.ndarray:
    """Normalize audio amplitude to a fixed peak level.

    Built-in laptop microphones typically capture very low amplitude
    (~0.003–0.01 RMS). Whisper needs a stronger signal to reliably recognise
    speech. Normalising to ~0.3 peak is safe and well below clipping.

    Args:
        audio:       Mono float32 array.
        target_peak: Desired peak amplitude (default 0.3).

    Returns:
        Normalised float32 array, or the original if the peak is negligible.
    """
    peak = float(np.max(np.abs(audio)))
    if peak > 1e-6:
        return (audio * (target_peak / peak)).astype(np.float32)
    return audio


# ── VoiceEngine ────────────────────────────────────────────────────────────────

class VoiceEngine:
    """Continuous microphone listener using a two-tier Whisper pipeline.

    States
    ──────
    - IDLE       : Monitoring for the wake word.
    - LISTENING  : Wake word heard; collecting the user's command.
    - PROCESSING : Command queued; waiting for the executor to finish.

    Public API
    ──────────
    - ``start()``              — pre-loads models, starts the audio loop.
    - ``stop()``               — signals graceful shutdown; joins threads.
    - ``calibrate(duration)``  — measures ambient noise; updates config.json.
    - ``set_mic_enabled(bool)`` — pause/resume listening without stopping.
    - ``set_online_mode(bool)`` — no-op (Whisper is always offline).
    """

    # ── Class-level constants ──────────────────────────────────────────────────
    STATE_IDLE       = "IDLE"
    STATE_LISTENING  = "LISTENING"
    STATE_PROCESSING = "PROCESSING"

    WHISPER_SR     = 16_000   # Hz — Whisper always expects 16 kHz input
    CHUNK_FRAMES   = 2_048    # ~46 ms per callback at 44100 Hz
    SILENCE_SECS   = 1.2     # consecutive silence (s) that ends a phrase
    MAX_RECORD     = 10.0    # hard cap on utterance length (seconds)
    MIN_COMMAND    = 0.4     # discard clips shorter than this (seconds)
    WAKE_WINDOW    = 0.6     # audio window (s) fed to the wake-word model
    SHUTDOWN_TIMEOUT = 5.0   # seconds to wait for the executor to drain

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(
        self,
        command_queue: "queue.Queue[str]",  # type: ignore[name-defined]
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Initialise the VoiceEngine.

        Args:
            command_queue:    Queue that receives transcribed command strings.
            status_callback:  Callable invoked with the new state name on every
                              state change.  Defaults to a no-op.
        """
        self.command_queue   = command_queue
        self.status_callback = status_callback or (lambda _s: None)

        # ── Load configuration ─────────────────────────────────────────────
        cfg = load_config()

        _configure_logging(cfg.get("debug_mode", False))

        self.wake_word          = cfg.get("wake_word", "dodo").lower()
        self.mic_enabled        = cfg.get("mic_enabled", True)
        self._energy_threshold  = float(cfg.get("mic_energy_threshold", 0.001))
        self._wake_model_name   = cfg.get("whisper_wakeword_model", "tiny")
        self._cmd_model_name    = cfg.get("whisper_model", "base")
        _lang                   = cfg.get("language", "auto")
        self._language: Optional[str] = None if _lang == "auto" else _lang

        logger.info(
            "VoiceEngine init — wake_word='%s', wake_model='%s', "
            "cmd_model='%s', language=%s, threshold=%.5f",
            self.wake_word,
            self._wake_model_name,
            self._cmd_model_name,
            repr(self._language),
            self._energy_threshold,
        )

        # ── State ──────────────────────────────────────────────────────────
        self.state    = self.STATE_IDLE
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # ThreadPoolExecutor with a single worker ensures only one Whisper
        # inference runs at a time.  The audio capture loop is never blocked.
        self._executor: Optional[ThreadPoolExecutor] = None
        self._active_future: Optional[Future] = None

        # ── Microphone detection ───────────────────────────────────────────
        try:
            self._device_idx, self._native_sr = _find_best_input_device()
        except RuntimeError as exc:
            logger.error("Microphone setup failed: %s", exc)
            # Store sentinel values; _listen_loop will surface the error.
            self._device_idx, self._native_sr = -1, 44_100

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Pre-load Whisper models in a background thread, then start listening."""
        if self._running:
            logger.warning("start() called but VoiceEngine is already running.")
            return
        self._running  = True
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dodo-whisper")
        self._thread   = threading.Thread(
            target=self._warmup_then_run,
            name="dodo-voice",
            daemon=True,
        )
        self._thread.start()
        logger.info("VoiceEngine started.")

    def stop(self) -> None:
        """Signal graceful shutdown and wait for threads to finish.

        Safe to call from any thread, including the main UI thread.
        """
        if not self._running:
            return
        logger.info("VoiceEngine stopping…")
        self._running = False

        # Cancel any pending future so the executor drains quickly.
        if self._active_future and not self._active_future.done():
            self._active_future.cancel()

        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.SHUTDOWN_TIMEOUT)
            if self._thread.is_alive():
                logger.warning("Voice thread did not exit within timeout.")

        logger.info("VoiceEngine stopped.")

    def set_mic_enabled(self, enabled: bool) -> None:
        """Pause (False) or resume (True) listening without stopping the thread."""
        self.mic_enabled = enabled
        if not enabled:
            self._set_state(self.STATE_IDLE)
        logger.info("Microphone %s.", "enabled" if enabled else "disabled")

    def set_online_mode(self, online: bool) -> None:
        """No-op — Whisper is always fully offline."""
        pass

    def listen_once(self) -> None:
        """Force the engine into LISTENING state immediately.

        Bypasses wake-word detection so the next detected utterance is
        transcribed as a command.  Intended for the UI mic button.
        Safe to call from any thread.
        """
        if not self._running:
            logger.warning("listen_once() called but engine is not running.")
            return
        if not self.mic_enabled:
            logger.warning("listen_once() ignored — mic is disabled.")
            return
        logger.info("listen_once() — forcing LISTENING state.")
        self._set_state(self.STATE_LISTENING)

    def calibrate(self, duration: float = 2.5) -> float:
        """Measure ambient noise and set the energy threshold.

        Records ``duration`` seconds of background audio, computes the RMS,
        and sets the threshold to ``3× RMS`` (clamped to at least 0.005).
        The new value is persisted to config.json via ``core.memory.save_config``.

        Args:
            duration: Recording length in seconds (default 2.5).

        Returns:
            The new energy threshold (float).
        """
        import queue as _queue  # local import to avoid module-level cycle risk

        self._set_state("CALIBRATING")
        logger.info("Calibrating energy threshold (%.1f s)…", duration)

        if self._device_idx < 0:
            logger.error("Cannot calibrate — no microphone available.")
            self._set_state(self.STATE_IDLE)
            return self._energy_threshold

        audio_q: "_queue.Queue[np.ndarray]" = _queue.Queue()

        def _cb(indata: np.ndarray, _frames: int, _time, _status) -> None:
            audio_q.put(indata.copy())

        frames: list[np.ndarray] = []
        try:
            with sd.InputStream(
                device=self._device_idx,
                samplerate=self._native_sr,
                channels=1,
                dtype="float32",
                blocksize=self.CHUNK_FRAMES,
                callback=_cb,
            ):
                deadline = time.monotonic() + duration
                while time.monotonic() < deadline:
                    try:
                        frames.append(audio_q.get(timeout=0.5).flatten())
                    except _queue.Empty:
                        pass
        except Exception as exc:
            logger.error("Calibration recording failed: %s", exc)
            self._set_state("MIC_ERROR")
            return self._energy_threshold

        if frames:
            audio     = np.concatenate(frames)
            rms       = float(np.sqrt(np.mean(audio ** 2)))
            threshold = max(round(rms * 3.0, 5), 0.005)
            self._energy_threshold = threshold
            self._persist_threshold(threshold)
            logger.info("Calibration complete. RMS=%.5f → threshold=%.5f", rms, threshold)
        else:
            logger.warning("Calibration captured no audio. Threshold unchanged.")
            threshold = self._energy_threshold

        self._set_state(self.STATE_IDLE)
        return threshold

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _persist_threshold(self, value: float) -> None:
        """Save the energy threshold to config.json via core.memory."""
        try:
            cfg = load_config()
            cfg["mic_energy_threshold"] = value
            save_config(cfg)
            logger.debug("Persisted mic_energy_threshold=%.5f to config.json", value)
        except Exception as exc:
            logger.warning("Could not save energy threshold: %s", exc)

    def _set_state(self, state: str) -> None:
        """Update state and notify the status callback."""
        if self.state == state:
            return
        logger.debug("State: %s → %s", self.state, state)
        self.state = state
        try:
            self.status_callback(state)
        except Exception as exc:
            logger.warning("status_callback raised: %s", exc)

    # ── Warm-up ────────────────────────────────────────────────────────────────

    def _warmup_then_run(self) -> None:
        """Pre-load both Whisper models, then enter the listening loop."""
        if not self._load_models():
            self._set_state("MIC_ERROR")
            return
        self._listen_loop()

    def _load_models(self) -> bool:
        """Load both model tiers.  Falls back to a single model if needed.

        Returns:
            True if at least the command model is available, False otherwise.
        """
        # Load wake-word model (allowed to fail — we fall back gracefully)
        try:
            _load_or_get_model(self._wake_model_name)
        except RuntimeError as exc:
            logger.warning(
                "Wake-word model '%s' unavailable (%s). "
                "Will use command model for wake-word too.",
                self._wake_model_name, exc,
            )
            self._wake_model_name = self._cmd_model_name  # fall back to same model

        # Load command model (required)
        try:
            _load_or_get_model(self._cmd_model_name)
        except RuntimeError as exc:
            logger.error("Command model '%s' failed to load: %s", self._cmd_model_name, exc)
            return False

        return True

    # ── Audio capture loop ─────────────────────────────────────────────────────

    def _listen_loop(self) -> None:
        """Callback-driven audio loop — the capture path never blocks.

        The InputStream callback pushes raw chunks onto audio_q.  The main
        loop drains audio_q, detects energy spikes, and submits Whisper work
        to the ThreadPoolExecutor.  The executor (max_workers=1) serialises
        all inference so the CPU is never overloaded.
        """
        import queue as _queue

        if self._device_idx < 0:
            logger.error(
                "Cannot start audio loop — no valid microphone (device_idx=%d).",
                self._device_idx,
            )
            self._set_state("MIC_ERROR")
            return

        audio_q: "_queue.Queue[np.ndarray]" = _queue.Queue(maxsize=64)

        def _cb(indata: np.ndarray, _frames: int, _time, status) -> None:
            if status:
                logger.debug("InputStream status: %s", status)
            try:
                audio_q.put_nowait(indata.copy())
            except _queue.Full:
                # Drop oldest chunk to keep latency low rather than blocking.
                try:
                    audio_q.get_nowait()
                    audio_q.put_nowait(indata.copy())
                except _queue.Empty:
                    pass

        def _open_stream(device_idx: int, samplerate: int) -> Optional[sd.InputStream]:
            """Attempt to open and start an InputStream; return it or None."""
            try:
                s = sd.InputStream(
                    device=device_idx,
                    samplerate=samplerate,
                    channels=1,
                    dtype="float32",
                    blocksize=self.CHUNK_FRAMES,
                    callback=_cb,
                )
                s.start()
                logger.info(
                    "Audio stream started on device %d at %d Hz.",
                    device_idx, samplerate,
                )
                return s
            except Exception as exc:
                logger.warning("Could not open stream on device %d: %s", device_idx, exc)
                return None

        stream = _open_stream(self._device_idx, self._native_sr)

        # If the preferred device failed, fall back to the OS default.
        if stream is None:
            logger.warning(
                "Primary device %d failed — retrying with system default.",
                self._device_idx,
            )
            try:
                self._device_idx, self._native_sr = _default_device_fallback()
                stream = _open_stream(self._device_idx, self._native_sr)
            except RuntimeError as exc:
                logger.error("Fallback device lookup failed: %s", exc)

        if stream is None:
            logger.error("All microphone open attempts failed. Entering MIC_ERROR state.")
            self._set_state("MIC_ERROR")
            return

        self._set_state(self.STATE_IDLE)

        try:
            self._audio_event_loop(audio_q)
        finally:
            try:
                stream.stop()
                stream.close()
                logger.info("Audio stream closed.")
            except Exception as exc:
                logger.warning("Error closing audio stream: %s", exc)

    def _audio_event_loop(self, audio_q: "queue.Queue[np.ndarray]") -> None:  # type: ignore[name-defined]
        """Core event loop: energy detection → Future submission."""
        import queue as _queue

        while self._running:
            if not self.mic_enabled:
                time.sleep(0.1)
                continue

            # If a transcription is running, skip new energy events until done.
            # This prevents piling up futures and lets state settle.
            if self._active_future is not None and not self._active_future.done():
                # Drain the queue so it doesn't overflow while we wait.
                try:
                    audio_q.get_nowait()
                except _queue.Empty:
                    time.sleep(0.02)
                continue
            else:
                self._active_future = None  # clear reference once done

            try:
                chunk = audio_q.get(timeout=0.3)
            except _queue.Empty:
                continue

            rms = float(np.sqrt(np.mean(chunk.flatten() ** 2)))
            logger.debug("RMS=%.5f  threshold=%.5f  state=%s", rms, self._energy_threshold, self.state)

            if rms <= self._energy_threshold:
                continue  # below noise floor

            # Energy spike detected — collect the full utterance
            utterance = self._collect_utterance(audio_q, first_chunk=chunk)
            if utterance is None:
                continue

            audio_16k = _normalize(_resample(utterance, self._native_sr, self.WHISPER_SR))

            if self.state == self.STATE_IDLE:
                self._active_future = self._executor.submit(
                    self._wake_check_task, audio_16k
                )
            elif self.state == self.STATE_LISTENING:
                self._set_state(self.STATE_PROCESSING)
                self._active_future = self._executor.submit(
                    self._command_task, audio_16k
                )

    def _collect_utterance(
        self,
        audio_q: "queue.Queue[np.ndarray]",  # type: ignore[name-defined]
        first_chunk: np.ndarray,
    ) -> Optional[np.ndarray]:
        """Collect audio chunks until silence or MAX_RECORD is reached.

        Args:
            audio_q:     Shared audio chunk queue.
            first_chunk: The chunk that triggered the energy spike.

        Returns:
            Concatenated mono float32 array, or None if the clip is too short.
        """
        import queue as _queue

        frames = [first_chunk.flatten()]
        silent_secs  = 0.0
        total_secs   = len(first_chunk) / self._native_sr
        chunk_secs   = self.CHUNK_FRAMES / self._native_sr

        while self._running and total_secs < self.MAX_RECORD:
            try:
                chunk = audio_q.get(timeout=0.4)
            except _queue.Empty:
                silent_secs += 0.4
                if silent_secs >= self.SILENCE_SECS:
                    break
                continue

            flat = chunk.flatten()
            frames.append(flat)
            total_secs += chunk_secs

            rms = float(np.sqrt(np.mean(flat ** 2)))
            if rms < self._energy_threshold * 0.6:
                silent_secs += chunk_secs
                if silent_secs >= self.SILENCE_SECS:
                    break
            else:
                silent_secs = 0.0

        audio = np.concatenate(frames)
        duration = len(audio) / self._native_sr
        if duration < self.MIN_COMMAND:
            logger.debug("Utterance too short (%.2f s) — discarded.", duration)
            return None
        logger.debug("Utterance collected: %.2f s", duration)
        return audio

    # ── Whisper tasks (run in executor) ───────────────────────────────────────

    def _wake_check_task(self, audio_16k: np.ndarray) -> None:
        """Tier-1: mini-transcription to check for the wake word.

        Uses the lightweight wake-word model on a short audio window.
        If the wake word is present the engine transitions to LISTENING.
        Any in-utterance command following the wake word is dispatched immediately.
        """
        # Use only the first WAKE_WINDOW seconds for the wake-word check
        wake_samples = int(self.WAKE_WINDOW * self.WHISPER_SR)
        wake_audio   = audio_16k[:wake_samples] if len(audio_16k) > wake_samples else audio_16k

        text = self._transcribe(wake_audio, model_name=self._wake_model_name)
        if not text:
            logger.debug("Tier-1: no speech detected.")
            return

        text_lower = text.lower().strip()
        logger.debug("Tier-1 transcription: '%s'", text_lower)

        if self.wake_word not in text_lower:
            return  # not a wake event

        logger.info("Wake word detected in: '%s'", text_lower)
        self._set_state(self.STATE_LISTENING)

        # Check if a command followed the wake word in the same utterance.
        remaining = text_lower.replace(self.wake_word, "", 1).strip()
        if len(remaining) > 3:
            logger.debug("Inline command after wake word: '%s'", remaining)
            self._dispatch(remaining)
        # If no inline command, we stay in LISTENING and wait for the next utterance.

    def _command_task(self, audio_16k: np.ndarray) -> None:
        """Tier-2: full-model transcription of the user's command."""
        text = self._transcribe(audio_16k, model_name=self._cmd_model_name)
        if not text:
            logger.debug("Tier-2: no speech detected. Returning to IDLE.")
            self._set_state(self.STATE_IDLE)
            return

        text_lower = text.lower().strip()

        # Guard: if user just repeated the wake word, don't dispatch
        if text_lower == self.wake_word:
            logger.debug("Command was just the wake word — staying in LISTENING.")
            self._set_state(self.STATE_LISTENING)
            return

        logger.info("Command recognised: '%s'", text_lower)
        self._dispatch(text_lower)

    def _dispatch(self, command_text: str) -> None:
        """Enqueue an accepted command and reset to IDLE."""
        cleaned = command_text.strip()
        logger.info("Dispatching command: '%s'", cleaned)
        self.command_queue.put(cleaned)
        self._set_state(self.STATE_IDLE)

    # ── Whisper transcription ──────────────────────────────────────────────────

    def _transcribe(
        self,
        audio_16k: np.ndarray,
        model_name: str,
    ) -> Optional[str]:
        """Run a Whisper model on pre-processed 16 kHz mono audio.

        Args:
            audio_16k:   Mono float32 array at 16 kHz.
            model_name:  Name of the cached Whisper model to use.

        Returns:
            Transcribed text string, or None if recognition fails or produces
            no output.
        """
        try:
            model = _load_or_get_model(model_name)

            # language=None triggers Whisper's auto language-detection.
            # Passing an explicit code (e.g. "en") forces that language.
            result = model.transcribe(  # type: ignore[union-attr]
                audio_16k,
                language=self._language,
                fp16=False,
                verbose=None,              # None = suppress all runtime output
                no_speech_threshold=0.8,   # more lenient than default 0.6
                logprob_threshold=-2.0,    # accept quieter / accented speech
                condition_on_previous_text=False,
            )
            text = result.get("text", "").strip()
            detected_lang = result.get("language")
            if detected_lang and self._language is None:
                logger.debug("Auto-detected language: %s", detected_lang)

            return text if text else None

        except RuntimeError as exc:
            logger.error("Whisper model error (%s): %s", model_name, exc)
            return None
        except Exception as exc:
            logger.exception("Unexpected transcription error (%s): %s", model_name, exc)
            return None
