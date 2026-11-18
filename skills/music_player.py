"""
DODO — skills/music_player.py
Smart Music Player with Vibe-Aware Auto-Queue.

BUG FIXES:
- Wrong song playing: was using direct stream URLs (expire, resolve to wrong video).
  Now uses webpage_url (youtube.com/watch?v=...) and plays via yt-dlp piped to VLC.
  VLC gets exact audio stream on-demand — never the wrong song.
- Search results incomplete: extract_flat=True for search (fast), then resolve
  only the selected track's URL at play time (accurate + fast).
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

TARGET_QUEUE_SIZE = 5
MAX_HISTORY_SIZE  = 50
SEARCH_TIMEOUT    = 20
VIBE_SEARCH_BATCH = 6


# ── Player detection ───────────────────────────────────────────────────────────

def _find_player() -> Optional[str]:
    """Return path to VLC or None."""
    vlc_paths = [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
    ]
    for p in vlc_paths:
        if os.path.exists(p):
            return p
    return None


def _find_ytdlp() -> Optional[str]:
    """Return path/command for yt-dlp."""
    import shutil
    return shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")


# ── Search: fast, flat, returns webpage URLs ──────────────────────────────────

def _ydl_search(query: str, max_results: int = 1) -> list[dict]:
    """
    Search YouTube using yt-dlp and return track metadata.
    
    Uses extract_flat=True for SPEED — only gets titles + YouTube URLs, 
    NOT direct stream URLs (which expire and cause wrong-song bugs).
    
    The actual stream URL is resolved at playback time by yt-dlp/VLC.
    """
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp not installed: pip install yt-dlp")
        return []

    ydl_opts = {
        "quiet":        True,
        "no_warnings":  True,
        "noplaylist":   True,
        "extract_flat": True,          # FAST: metadata only, no stream URL extraction
        "socket_timeout": SEARCH_TIMEOUT,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            if not info or "entries" not in info or not info["entries"]:
                return []

            results = []
            for entry in info["entries"]:
                if not entry:
                    continue
                vid_id  = entry.get("id", "")
                webpage = entry.get("url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
                if not webpage:
                    continue
                # Make sure it's a full YouTube URL
                if not webpage.startswith("http"):
                    webpage = f"https://www.youtube.com/watch?v={webpage}"

                results.append({
                    "title":       entry.get("title", query),
                    "url":         webpage,          # YouTube page URL — resolved at play time
                    "webpage_url": webpage,
                    "duration":    entry.get("duration", 0),
                    "channel":     entry.get("channel") or entry.get("uploader", ""),
                    "seed":        query,
                })
            return results

    except Exception as e:
        logger.warning("yt-dlp search failed for '%s': %s", query, e)
        return []


# ── Vibe query generator ───────────────────────────────────────────────────────

def _vibe_queries(seed_title: str, seed_channel: str) -> list[str]:
    """Generate vibe-search queries from a seed track."""
    artist = seed_channel or seed_title.split(" - ")[0].strip()
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


# ── MusicQueue singleton ───────────────────────────────────────────────────────

class MusicQueue:
    """
    Central state manager for DODO music playback.

    Playback method (in priority order):
      1. yt-dlp piped into VLC (best quality, exact song, audio only)
      2. VLC opening the YouTube page URL directly
      3. Browser fallback
    """

    def __init__(self):
        self._queue:      deque[dict]    = deque()
        self._history:    deque[dict]    = deque(maxlen=MAX_HISTORY_SIZE)
        self._current:    Optional[dict] = None
        self._process:    Optional[subprocess.Popen] = None
        self._lock        = threading.Lock()
        self._refill_lock = threading.Lock()
        self._stop_event  = threading.Event()
        self._watcher:    Optional[threading.Thread] = None
        self._player      = _find_player()
        self._ytdlp       = _find_ytdlp()
        logger.info("MusicQueue init — VLC: %s | yt-dlp: %s", self._player, self._ytdlp)

    # ── Playback core ──────────────────────────────────────────────────────

    def _launch_process(self, track: dict) -> Optional[subprocess.Popen]:
        """
        Spawn a player process for the track.

        Strategy:
          A. yt-dlp --get-url | VLC stdin  → exact audio stream, correct song guaranteed
          B. VLC with YouTube URL          → VLC's YouTube plugin resolves it
          C. Browser fallback
        """
        url    = track["webpage_url"] or track["url"]
        player = self._player
        ytdlp  = self._ytdlp

        try:
            # Strategy A: yt-dlp extracts best audio URL → VLC plays it
            # This guarantees the EXACT song because we resolve the stream ourselves.
            if player and ytdlp and "vlc" in player.lower():
                stream_url = self._resolve_stream_url(url)
                if stream_url:
                    logger.info("Playing via yt-dlp stream URL: %s", stream_url[:80])
                    return subprocess.Popen(
                        [player, "--intf", "dummy", "--play-and-exit",
                         "--no-video", stream_url],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )

            # Strategy B: VLC opens YouTube URL (uses VLC's built-in YouTube resolver)
            if player and "vlc" in player.lower():
                logger.info("Playing via VLC+YouTube URL: %s", url)
                return subprocess.Popen(
                    [player, "--intf", "dummy", "--play-and-exit",
                     "--no-video", url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )

            # Non-VLC player (WMP etc.) — open URL directly
            if player:
                return subprocess.Popen(
                    [player, url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )

            return None

        except Exception as e:
            logger.error("Failed to launch player: %s", e)
            return None

    def _resolve_stream_url(self, webpage_url: str, timeout: int = 15) -> Optional[str]:
        """
        Run yt-dlp to get the best audio stream URL for a YouTube page.
        Returns the direct stream URL, or None on failure.

        This is the KEY FIX for wrong-song bug:
        We explicitly resolve the exact video URL ourselves rather than
        letting VLC/WMP do its own search.
        """
        ytdlp = self._ytdlp
        if not ytdlp:
            try:
                import yt_dlp
                # Use yt_dlp library directly
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "format": "bestaudio[ext=m4a]/bestaudio/best",
                    "noplaylist": True,
                    "socket_timeout": timeout,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(webpage_url, download=False)
                    if info:
                        # Get the actual stream URL
                        url = info.get("url")
                        if not url and info.get("formats"):
                            # Pick best audio-only format
                            audio_formats = [
                                f for f in info["formats"]
                                if f.get("acodec") != "none" and f.get("vcodec") == "none"
                            ]
                            if not audio_formats:
                                audio_formats = info["formats"]
                            best = max(audio_formats, key=lambda f: f.get("abr") or f.get("tbr") or 0)
                            url = best.get("url")
                        return url
            except Exception as e:
                logger.warning("yt_dlp library stream resolve failed: %s", e)
            return None

        # Use yt-dlp CLI
        try:
            result = subprocess.run(
                [ytdlp, "--get-url", "--format", "bestaudio[ext=m4a]/bestaudio/best",
                 "--no-playlist", "--quiet", webpage_url],
                capture_output=True, text=True, timeout=timeout
            )
            url = result.stdout.strip().split("\n")[0]
            if url and url.startswith("http"):
                return url
        except Exception as e:
            logger.warning("yt-dlp CLI stream resolve failed: %s", e)

        return None

    def _start_track(self, track: dict) -> str:
        """
        Start playing `track`. Stops whatever is currently playing.
        Returns a user-facing status string.
        """
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()
                self._process = None

            if self._current:
                self._history.appendleft(self._current)

            self._current = track
            self._stop_event.clear()
            proc = self._launch_process(track)

            if proc is None:
                # Browser fallback
                try:
                    import skills.browser_control as bc
                    bc.open_url(track.get("webpage_url") or track["url"])
                    return f"Playing in browser: {track['title']}"
                except Exception:
                    return "No media player found. Install VLC."

            self._process = proc

        # Watcher thread
        self._stop_event.clear()
        t = threading.Thread(
            target=self._watcher_loop, args=(proc, track),
            name="MusicWatcher", daemon=True
        )
        t.start()
        self._watcher = t

        return f"Now playing: {track['title']}"

    def _watcher_loop(self, proc: subprocess.Popen, track: dict):
        """Wait for proc to finish, then advance queue."""
        while not self._stop_event.is_set():
            if proc.poll() is not None:
                break
            time.sleep(0.5)

        if self._stop_event.is_set():
            return

        logger.info("Track finished: %s", track["title"])
        self._advance_queue()

    def _advance_queue(self):
        """Pop and play the next song; kick off a refill."""
        if self._queue:
            next_track = self._queue.popleft()
            self._start_track(next_track)
            threading.Thread(
                target=self._refill_queue,
                name="QueueRefill", daemon=True
            ).start()
        else:
            with self._lock:
                self._current = None

    # ── Queue management ───────────────────────────────────────────────────

    def _seen_titles(self) -> set[str]:
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
        Top up the queue to TARGET_QUEUE_SIZE using vibe searches.
        Only one refill runs at a time.
        """
        if not self._refill_lock.acquire(blocking=False):
            return

        try:
            needed = TARGET_QUEUE_SIZE - len(self._queue)
            if needed <= 0:
                return

            seed = self._current or (self._history[0] if self._history else None)
            if seed is None:
                return

            queries = _vibe_queries(seed["title"], seed.get("channel", ""))
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
                        logger.info("Queue refill added: %s", r["title"])

            logger.info("Queue size: %d", len(self._queue))

        except Exception as e:
            logger.error("Refill error: %s", e)
        finally:
            self._refill_lock.release()

    # ── Public API ─────────────────────────────────────────────────────────

    def play(self, query: str) -> str:
        results = _ydl_search(query, 3)  # Get 3 results, pick most relevant
        if not results:
            return f"Couldn't find '{query}' on YouTube."

        # Pick the best match: prefer exact title match, else first result
        query_lower = query.lower()
        best = results[0]
        for r in results:
            if query_lower in r["title"].lower():
                best = r
                break

        track = best
        status = self._start_track(track)

        threading.Thread(
            target=self._refill_queue,
            name="InitialQueueFill", daemon=True
        ).start()

        return status

    def add_to_queue(self, query: str) -> str:
        results = _ydl_search(query, 1)
        if not results:
            return f"Couldn't find '{query}' on YouTube."
        track = results[0]
        self._queue.append(track)
        return f"Added to queue (#{len(self._queue)}): {track['title']}"

    def skip(self) -> str:
        if not self._current:
            return "Nothing is playing right now."
        with self._lock:
            self._stop_event.set()
            if self._process and self._process.poll() is None:
                self._process.terminate()
                self._process = None

        if self._queue:
            if self._current:
                self._history.appendleft(self._current)
            next_track = self._queue.popleft()
            status = self._start_track(next_track)
            threading.Thread(target=self._refill_queue, daemon=True).start()
            return f"Skipped. {status}"
        else:
            self._current = None
            return "Skipped. Queue is empty."

    def stop(self) -> str:
        self._stop_event.set()
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()
                self._process = None
            self._current = None
        return "Music stopped."

    def clear_queue(self) -> str:
        count = len(self._queue)
        self._queue.clear()
        return f"Cleared {count} songs from the queue."

    def show_queue(self) -> str:
        lines = []
        if self._current:
            lines.append(f"Now playing: {self._current['title']}")
        else:
            lines.append("Nothing playing right now.")

        if self._queue:
            lines.append(f"\nUp next ({len(self._queue)} songs):")
            for i, t in enumerate(self._queue, 1):
                dur = t.get("duration", 0)
                dur_str = f"  [{int(dur)//60}:{int(dur)%60:02d}]" if dur else ""
                lines.append(f"  {i}. {t['title']}{dur_str}")
        else:
            lines.append("\nQueue is empty.")

        if self._history:
            lines.append("\nRecently played:")
            for t in list(self._history)[:3]:
                lines.append(f"  - {t['title']}")

        return "\n".join(lines)

    def now_playing(self) -> str:
        if self._current and self._process and self._process.poll() is None:
            return f"Now playing: {self._current['title']}"
        return "Nothing is playing."

    def is_playing(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None


# ── Singleton ──────────────────────────────────────────────────────────────────

_mq = MusicQueue()


# ── Module-level API ───────────────────────────────────────────────────────────

def play_song(query: str) -> str:
    return _mq.play(query)

def stop_music() -> str:
    return _mq.stop()

def queue_song(query: str) -> str:
    return _mq.add_to_queue(query)

def skip_song() -> str:
    return _mq.skip()

def show_queue() -> str:
    return _mq.show_queue()

def clear_queue() -> str:
    return _mq.clear_queue()

def now_playing() -> str:
    return _mq.now_playing()

def is_playing() -> bool:
    return _mq.is_playing()
