"""
DODO — skills/music_player.py
Smart Music Player with Vibe-Aware Auto-Queue.

Architecture:
  - MusicQueue singleton manages state: current song, upcoming queue, history
  - Watcher thread monitors playback completion, auto-advances queue
  - Refill thread keeps queue at TARGET_QUEUE_SIZE using yt-dlp vibe search
  - All public functions are module-level wrappers for backward compatibility
"""

import subprocess
import threading
import os
import time
import logging
import re
from collections import deque
from typing import Optional

logger = logging.getLogger("dodo.music")

TARGET_QUEUE_SIZE = 5          # how many songs ahead to keep ready
MAX_HISTORY_SIZE  = 50         # remember last N played songs
SEARCH_TIMEOUT    = 15         # seconds per yt-dlp search call
VIBE_SEARCH_BATCH = 6          # fetch slightly more than needed (deduplicate)


# ──────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _find_player() -> Optional[str]:
    """Return path to VLC, WMP, or None."""
    vlc_paths = [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
    ]
    for p in vlc_paths:
        if os.path.exists(p):
            return p
    wmp = r"C:\Program Files\Windows Media Player\wmplayer.exe"
    if os.path.exists(wmp):
        return wmp
    return None


def _ydl_search(query: str, max_results: int = 1) -> list[dict]:
    """
    Use yt-dlp to search YouTube and return a list of track dicts.
    Each dict: {title, url, webpage_url, duration, channel}
    """
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp not installed")
        return []

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "noplaylist": True,
        "extract_flat": False,
        "socket_timeout": SEARCH_TIMEOUT,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_query = f"ytsearch{max_results}:{query}"
            info = ydl.extract_info(search_query, download=False)
            if not info or "entries" not in info or not info["entries"]:
                return []
            results = []
            for entry in info["entries"]:
                if not entry:
                    continue
                stream_url = entry.get("url") or entry.get("webpage_url")
                if stream_url:
                    results.append({
                        "title":       entry.get("title", query),
                        "url":         stream_url,
                        "webpage_url": entry.get("webpage_url", ""),
                        "duration":    entry.get("duration", 0),
                        "channel":     entry.get("channel") or entry.get("uploader", ""),
                        "seed":        query,   # which search produced this
                    })
            return results
    except Exception as e:
        logger.warning("yt-dlp search failed for '%s': %s", query, e)
        return []


def _vibe_queries(seed_title: str, seed_channel: str) -> list[str]:
    """
    Generate multiple vibe-search queries from a seed track.
    More variety → better recommendations.
    """
    artist = seed_channel or seed_title.split(" - ")[0].strip()
    # Clean up common suffixes
    clean_title = re.sub(
        r"(official|video|lyrics|audio|hd|4k|mv|\(.*?\)|\[.*?\])",
        "", seed_title, flags=re.IGNORECASE
    ).strip()

    return [
        f"songs like {clean_title}",
        f"{artist} similar songs mix",
        f"{clean_title} playlist",
        f"music similar to {artist}",
        f"{artist} top songs",
    ]


# ──────────────────────────────────────────────────────────────────────────────
#  MusicQueue  (singleton)
# ──────────────────────────────────────────────────────────────────────────────

class MusicQueue:
    """
    Central state manager for DODO music playback.

    Attributes
    ----------
    _queue       : deque of track dicts (upcoming songs)
    _history     : deque of track dicts (recently played)
    _current     : dict | None  — track currently playing
    _process     : Popen | None — current player subprocess
    _lock        : threading.Lock protecting _process / _current
    _refill_lock : threading.Lock — prevents concurrent refill threads
    _playing     : threading.Event — set while playback is active
    """

    def __init__(self):
        self._queue:       deque[dict] = deque()
        self._history:     deque[dict] = deque(maxlen=MAX_HISTORY_SIZE)
        self._current:     Optional[dict] = None
        self._process:     Optional[subprocess.Popen] = None
        self._lock         = threading.Lock()
        self._refill_lock  = threading.Lock()
        self._stop_event   = threading.Event()   # signals watcher to quit
        self._watcher:     Optional[threading.Thread] = None
        self._player       = _find_player()
        logger.info("MusicQueue init — player: %s", self._player)

    # ── playback core ──────────────────────────────────────────────────────

    def _launch_process(self, track: dict) -> Optional[subprocess.Popen]:
        """Spawn the media player process for a resolved track URL."""
        url = track["url"]
        player = self._player
        try:
            if player and "vlc" in player.lower():
                proc = subprocess.Popen(
                    [player, "--intf", "dummy", "--play-and-exit",
                     "--no-video", url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            elif player:
                proc = subprocess.Popen(
                    [player, url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                return None
            return proc
        except Exception as e:
            logger.error("Failed to launch player: %s", e)
            return None

    def _start_track(self, track: dict) -> str:
        """
        Start playing `track`. Stop whatever is currently playing.
        Spawns a watcher thread to handle auto-advance.
        Returns a user-facing status string.
        """
        with self._lock:
            # Kill current playback
            if self._process and self._process.poll() is None:
                self._process.terminate()
                self._process = None

            # Archive current to history
            if self._current:
                self._history.appendleft(self._current)

            self._current = track
            self._stop_event.clear()
            proc = self._launch_process(track)

            if proc is None:
                # No local player — browser fallback
                try:
                    import skills.browser_control as bc
                    bc.open_url(track.get("webpage_url") or track["url"])
                    return f"🎵 Playing in browser: {track['title']}"
                except Exception:
                    return f"No media player found. Install VLC."
            self._process = proc

        # Kick off watcher thread
        self._stop_event.clear()
        t = threading.Thread(
            target=self._watcher_loop, args=(proc, track),
            name="MusicWatcher", daemon=True
        )
        t.start()
        self._watcher = t

        return f"🎵 Now playing: {track['title']}"

    def _watcher_loop(self, proc: subprocess.Popen, track: dict):
        """
        Wait for `proc` to finish, then:
          1. Pop next song from queue and start it
          2. Trigger a background refill to restore queue to TARGET_QUEUE_SIZE
        Exits early if _stop_event is set (user called stop_music).
        """
        logger.debug("Watcher started for: %s", track["title"])
        while not self._stop_event.is_set():
            ret = proc.poll()
            if ret is not None:
                break
            time.sleep(0.5)

        if self._stop_event.is_set():
            logger.debug("Watcher: stop event — not advancing queue")
            return

        logger.info("Track finished: %s — advancing queue", track["title"])
        self._advance_queue()

    def _advance_queue(self):
        """Pop and play the next song; kick off a refill."""
        if self._queue:
            next_track = self._queue.popleft()
            self._start_track(next_track)
            # Refill in background to keep queue at TARGET_QUEUE_SIZE
            threading.Thread(
                target=self._refill_queue,
                name="QueueRefill", daemon=True
            ).start()
        else:
            with self._lock:
                self._current = None
            logger.info("Queue empty — playback complete")

    # ── queue management ───────────────────────────────────────────────────

    def _seen_titles(self) -> set[str]:
        """Titles already in queue or history (for deduplication)."""
        seen = set()
        if self._current:
            seen.add(self._current["title"].lower())
        for t in self._queue:
            seen.add(t["title"].lower())
        for t in self._history:
            seen.add(t["title"].lower())
        return seen

    def _refill_queue(self):
        """
        Top up the queue to TARGET_QUEUE_SIZE using vibe searches
        based on the currently playing (or most recently played) track.
        Thread-safe; only one refill runs at a time.
        """
        if not self._refill_lock.acquire(blocking=False):
            return   # another refill is already running

        try:
            needed = TARGET_QUEUE_SIZE - len(self._queue)
            if needed <= 0:
                return

            seed_track = self._current or (self._history[0] if self._history else None)
            if seed_track is None:
                return

            queries = _vibe_queries(seed_track["title"], seed_track.get("channel", ""))
            seen    = self._seen_titles()
            added   = 0

            for q in queries:
                if added >= needed:
                    break
                results = _ydl_search(q, VIBE_SEARCH_BATCH)
                for r in results:
                    if added >= needed:
                        break
                    if r["title"].lower() not in seen:
                        self._queue.append(r)
                        seen.add(r["title"].lower())
                        added += 1
                        logger.info("Queue refill added: %s (via '%s')", r["title"], q)

            logger.info("Queue now has %d songs", len(self._queue))
        except Exception as e:
            logger.error("Refill error: %s", e)
        finally:
            self._refill_lock.release()

    # ── public API ─────────────────────────────────────────────────────────

    def play(self, query: str) -> str:
        """
        Play `query` immediately.
        Clears current playback, searches for the track,
        then starts vibe-queue building in background.
        """
        results = _ydl_search(query, 1)
        if not results:
            return f"❌ Couldn't find '{query}' on YouTube."

        track = results[0]
        status = self._start_track(track)

        # Kick off initial queue fill in background
        threading.Thread(
            target=self._refill_queue,
            name="InitialQueueFill", daemon=True
        ).start()

        return status

    def add_to_queue(self, query: str) -> str:
        """Manually add a song to the end of the queue."""
        results = _ydl_search(query, 1)
        if not results:
            return f"❌ Couldn't find '{query}' on YouTube."
        track = results[0]
        self._queue.append(track)
        pos = len(self._queue)
        return f"✅ Added to queue (#{pos}): {track['title']}"

    def skip(self) -> str:
        """Skip the current song and play the next one in queue."""
        if not self._current:
            return "Nothing is playing right now."
        with self._lock:
            self._stop_event.set()
            if self._process and self._process.poll() is None:
                self._process.terminate()
                self._process = None

        if self._queue:
            # Archive current
            if self._current:
                self._history.appendleft(self._current)
            next_track = self._queue.popleft()
            status = self._start_track(next_track)
            threading.Thread(
                target=self._refill_queue,
                name="SkipRefill", daemon=True
            ).start()
            return f"⏭️ Skipped. {status}"
        else:
            self._current = None
            return "⏭️ Skipped. Queue is empty."

    def stop(self) -> str:
        """Stop playback and clear the queue."""
        self._stop_event.set()
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()
                self._process = None
            self._current = None
        return "⏹️ Music stopped."

    def clear_queue(self) -> str:
        """Empty the upcoming queue (current song keeps playing)."""
        count = len(self._queue)
        self._queue.clear()
        return f"🗑️ Cleared {count} songs from the queue."

    def show_queue(self) -> str:
        """Return a formatted string of the upcoming queue."""
        lines = []

        if self._current:
            lines.append(f"▶️  Now playing: {self._current['title']}")
        else:
            lines.append("▶️  Nothing playing right now.")

        if self._queue:
            lines.append(f"\n🎵 Up next ({len(self._queue)} songs):")
            for i, t in enumerate(self._queue, 1):
                dur = t.get("duration", 0)
                dur_str = f"  [{int(dur)//60}:{int(dur)%60:02d}]" if dur else ""
                lines.append(f"  {i}. {t['title']}{dur_str}")
        else:
            lines.append("\n📭 Queue is empty — add songs or let auto-queue fill it.")

        if self._history:
            recent = list(self._history)[:3]
            lines.append(f"\n🕓 Recently played:")
            for t in recent:
                lines.append(f"  • {t['title']}")

        return "\n".join(lines)

    def now_playing(self) -> str:
        """Return what's currently playing."""
        if self._current and self._process and self._process.poll() is None:
            return f"▶️ {self._current['title']}"
        return "Nothing is playing."

    def is_playing(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None


# ──────────────────────────────────────────────────────────────────────────────
#  Singleton instance
# ──────────────────────────────────────────────────────────────────────────────

_mq = MusicQueue()


# ──────────────────────────────────────────────────────────────────────────────
#  Module-level API (used by agent.py / brain.py)
# ──────────────────────────────────────────────────────────────────────────────

def play_song(query: str) -> str:
    """Play a song immediately and start vibe-queue auto-fill."""
    return _mq.play(query)

def stop_music() -> str:
    """Stop all music playback."""
    return _mq.stop()

def queue_song(query: str) -> str:
    """Manually add a song to the queue."""
    return _mq.add_to_queue(query)

def skip_song() -> str:
    """Skip the current song."""
    return _mq.skip()

def show_queue() -> str:
    """Return a formatted view of the current queue."""
    return _mq.show_queue()

def clear_queue() -> str:
    """Empty the upcoming queue."""
    return _mq.clear_queue()

def now_playing() -> str:
    """Return the currently playing song."""
    return _mq.now_playing()

def is_playing() -> bool:
    """Return True if music is active."""
    return _mq.is_playing()
