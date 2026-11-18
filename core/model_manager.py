"""
DODO — core/model_manager.py
==============================
Auto-discovering, auto-switching AI model manager.

HOW IT WORKS:
  1. On first use (or when a model fails), queries each provider's /models endpoint
     to get the LIVE list of available models — no hardcoded dead model names.
  2. Tests candidates in priority order, picks the fastest working one.
  3. Caches the result in config.json so DODO doesn't probe on every startup.
  4. If the cached model fails at runtime, automatically switches to the next
     working model — DODO never shows "model not found" to the user.

PRIORITY ORDER (best quality & speed first):
  Groq  → gemini → nvidia  (all three tried, Groq preferred for speed)

Usage:
  from core.model_manager import get_best_client_and_model, on_model_failure
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional, Tuple

from openai import OpenAI

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

# Thread safety
_lock = threading.Lock()

# In-memory cache of (client, model, provider) — avoids re-testing on every call
_active_client: Optional[OpenAI] = None
_active_model:  str = ""
_active_provider: str = ""

# Models confirmed dead this session — never retry
_dead_models: set[str] = {
    # Gemini dead (confirmed 2026-11-18)
    "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro",
    "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.8-flash",
    "gemini-3.5-flash",
    # Groq dead
    "llama-3.3-70b-versatile", "llama-3.1-70b-versatile", "llama3-70b-8192",
    "llama3-8b-8192", "gemma2-9b-it", "mixtral-8x7b-32768", "llama-3.1-8b-instant",
    # NVIDIA dead (confirmed 2026-11-18 — all timed out or 404)
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "meta/llama-3.3-70b-instruct", "meta/llama-3.1-70b-instruct",
    "meta/llama-3.1-8b-instruct", "meta/llama-3.2-11b-vision-instruct",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "google/gemma-4-31b-it", "google/gemma-3-12b-it",
    "minimaxai/minimax-m3.1-flash", "z-ai/glm-5.3-flash",
    "mistralai/mistral-large-4-1t-a52b",
}

# ── Provider definitions ───────────────────────────────────────────────────────

PROVIDERS = {
    "groq": {
        "base_url":  "https://api.groq.com/openai/v1",
        "key_field": "groq_api_key",
        "model_field": "groq_model",
        # Priority filter: prefer capable chat models, skip whisper/guard/embed
        "prefer": ["gpt-oss-120b", "gpt-oss-20b", "qwen", "llama", "gemma", "mistral", "allam"],
        "skip":   ["whisper", "guard", "embed", "tts", "orpheus", "prompt-guard"],
    },
    "gemini": {
        "base_url":  "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_field": "gemini_api_key",
        "model_field": "gemini_model",
        "prefer": ["flash", "pro"],
        "skip":   ["tts", "transcribe", "image", "embed", "robotics", "computer-use", "nano"],
    },
    "nvidia": {
        "base_url":  "https://integrate.api.nvidia.com/v1",
        "key_field": "nvidia_api_key",
        "model_field": "nvidia_model",
        "prefer": ["nemotron", "gemma", "mistral", "flash", "glm"],
        "skip":   ["embed", "guard", "safety", "retriev", "translate", "riva", "vision"],
    },
}

# Default fallback order — SPEED FIRST (confirmed working 2026-11-18)
# Dead models are NEVER listed here: gemini-2.0-flash, gemini-1.5-*, llama-3.3-70b,
# meta/llama-*, nvidia/nemotron-3.5-*, google/gemma-*, minimaxai/*, z-ai/glm-*
_FALLBACK_MODELS = {
    # Groq: fastest first (gpt-oss-20b ~1.7s, allam ~1.6s, gpt-oss-120b ~13s)
    "groq":   ["openai/gpt-oss-20b", "allam-2-7b", "qwen/qwen3.8-27b", "openai/gpt-oss-120b"],
    # Gemini: only confirmed working model; skip 2.5-flash (404), gemini-flash-latest (empty)
    "gemini": ["gemini-flash-lite-latest", "gemini-flash-latest"],
    # NVIDIA: all tested models were dead/timeout — disable until re-verified
    "nvidia": [],
}


def _load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_config(cfg: dict) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _get_live_models(provider: str, client: OpenAI) -> list[str]:
    """
    Query the provider's /models endpoint and return a filtered, ranked list.
    Falls back to hardcoded list on failure.
    """
    pdef = PROVIDERS[provider]
    skip_kws  = pdef.get("skip", [])
    prefer_kws = pdef.get("prefer", [])

    try:
        all_models = [m.id for m in client.models.list().data]
    except Exception:
        return _FALLBACK_MODELS.get(provider, [])

    # Filter out clearly non-chat models
    filtered = [
        m for m in all_models
        if not any(kw in m.lower() for kw in skip_kws)
    ]

    # Sort: preferred keywords first
    def priority(m: str) -> int:
        for i, kw in enumerate(prefer_kws):
            if kw in m.lower():
                return i
        return len(prefer_kws)

    filtered.sort(key=priority)
    return filtered or _FALLBACK_MODELS.get(provider, [])


def _test_model(client: OpenAI, model: str, timeout: int = 8) -> Tuple[bool, int]:
    """
    Quick smoke-test: send a minimal prompt, return (ok, latency_ms).
    """
    if model in _dead_models:
        return False, 0
    try:
        t0 = time.perf_counter()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
            temperature=0,
            timeout=timeout,
        )
        ms = int((time.perf_counter() - t0) * 1000)
        # Make sure we got a real reply
        content = resp.choices[0].message.content if resp.choices else ""
        if content is not None:
            return True, ms
        return False, 0
    except Exception as e:
        err = str(e)
        # Permanent failures — mark dead
        if any(c in err for c in ["404", "410", "400", "not found", "gone", "no longer"]):
            _dead_models.add(model)
        return False, 0


def _find_working_model(provider: str, cfg: dict) -> Tuple[Optional[OpenAI], str]:
    """
    For a given provider, discover live models and return the first working one.
    Returns (client, model_name) or (None, "").
    """
    pdef    = PROVIDERS[provider]
    key     = cfg.get(pdef["key_field"], "")
    if not key:
        return None, ""

    base_url = pdef["base_url"]
    client   = OpenAI(api_key=key, base_url=base_url)

    # First try the user's saved model preference (fast path)
    saved_model = cfg.get(pdef["model_field"], "")
    if saved_model and saved_model not in _dead_models:
        ok, _ = _test_model(client, saved_model)
        if ok:
            return client, saved_model
        else:
            _dead_models.add(saved_model)

    # Discover live models and test them in order
    candidates = _get_live_models(provider, client)
    for model in candidates:
        if model in _dead_models:
            continue
        ok, ms = _test_model(client, model)
        if ok:
            # Save the working model back to config
            cfg[pdef["model_field"]] = model
            return client, model

    return None, ""


def auto_discover(save: bool = True) -> Tuple[Optional[OpenAI], str, str]:
    """
    Test all providers in priority order and return (client, model, provider)
    for the first working combination.

    Call this at startup or when all cached models fail.
    """
    global _active_client, _active_model, _active_provider

    cfg = _load_config()
    provider_order = ["groq", "gemini", "nvidia"]

    # Try user's preferred provider first
    preferred = cfg.get("ai_provider", "groq")
    if preferred in provider_order:
        provider_order = [preferred] + [p for p in provider_order if p != preferred]

    for provider in provider_order:
        client, model = _find_working_model(provider, cfg)
        if client and model:
            _active_client   = client
            _active_model    = model
            _active_provider = provider
            if save:
                cfg["ai_provider"] = provider
                _save_config(cfg)
            print(f"[ModelManager] Active: provider={provider}, model={model}")
            return client, model, provider

    return None, "", ""


def get_client_and_model() -> Tuple[Optional[OpenAI], str]:
    """
    Return (client, model) for immediate use.
    Uses cached values if available, otherwise runs auto_discover().
    Thread-safe.
    """
    global _active_client, _active_model

    with _lock:
        if _active_client and _active_model and _active_model not in _dead_models:
            return _active_client, _active_model
        # Cache miss or dead — rediscover
        client, model, _ = auto_discover()
        return client, model


def on_model_failure(model: str, error: str) -> Tuple[Optional[OpenAI], str]:
    """
    Call this when an LLM call fails at runtime.
    Marks the model as dead and finds the next working one.
    Returns (new_client, new_model) or (None, "") if nothing works.
    """
    global _active_client, _active_model, _active_provider

    with _lock:
        print(f"[ModelManager] Model failed: {model} — {error[:60]}")
        _dead_models.add(model)
        _active_client = None
        _active_model  = ""

    client, model, provider = auto_discover()
    return client, model


def run_model_check(print_results: bool = True) -> dict:
    """
    Test ALL available models across ALL providers.
    Returns a dict of {provider/model: {ok, latency_ms, error}}.
    Use this to generate a status report or debug model issues.
    """
    cfg = _load_config()
    results = {}

    for provider, pdef in PROVIDERS.items():
        key      = cfg.get(pdef["key_field"], "")
        base_url = pdef["base_url"]
        if not key:
            if print_results:
                print(f"\n[{provider.upper()}] No API key — skipping")
            continue

        client     = OpenAI(api_key=key, base_url=base_url)
        candidates = _get_live_models(provider, client)

        if print_results:
            print(f"\n[{provider.upper()}]  ({len(candidates)} candidates)")

        for model in candidates:
            ok, ms = _test_model(client, model)
            tag = f"OK  {ms}ms" if ok else "FAIL"
            if print_results:
                print(f"  {'OK' if ok else 'XX'}  {model:<50}  {tag}")
            results[f"{provider}/{model}"] = {"ok": ok, "latency_ms": ms}

    return results


if __name__ == "__main__":
    print("=" * 65)
    print("DODO Model Manager — full probe")
    print("=" * 65)
    results = run_model_check(print_results=True)

    working = [(k, v["latency_ms"]) for k, v in results.items() if v["ok"]]
    working.sort(key=lambda x: x[1])

    print(f"\n{'='*65}")
    print(f"Working models ({len(working)}):")
    for name, ms in working:
        print(f"  {ms:4d}ms  {name}")

    print("\nRunning auto_discover to pick best...")
    client, model, provider = auto_discover()
    if client:
        print(f"\nSelected: [{provider}] {model}")
    else:
        print("\nWARNING: No working model found!")
