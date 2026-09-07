"""
DODO — skills/music_player.py
Alexa-style music playback: yt-dlp finds the audio stream, plays it locally.
No browser needed — real audio playback via Windows built-in player or VLC.
"""

import subprocess
import threading
import os
import logging

logger = logging.getLogger("dodo.music")

# Track the currently playing process so we can stop it
_player_process: subprocess.Popen | None = None
_player_lock = threading.Lock()


def _find_player() -> str | None:
    """Find an available audio player on the system."""
    # Check for VLC (best option)
    vlc_paths = [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
    ]
    for p in vlc_paths:
        if os.path.exists(p):
            return p

    # Check for Windows Media Player
    wmp = r"C:\Program Files\Windows Media Player\wmplayer.exe"
    if os.path.exists(wmp):
        return wmp

    # Fallback: Windows built-in media player (start command)
    return None


def play_song(query: str) -> str:
    """
    Search YouTube for `query` and play the first audio result locally.
    Uses yt-dlp to extract the best audio stream URL, then plays via VLC/WMP.
    Returns a status message.
    """
    global _player_process

    try:
        import yt_dlp
    except ImportError:
        return "yt-dlp not installed. Run: pip install yt-dlp"

    try:
        # Stop any currently playing song first
        stop_music()

        # yt-dlp options: search YouTube, extract best audio URL, no download
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio/best",
            "noplaylist": True,
            "extract_flat": False,
            "default_search": "ytsearch1",  # search YouTube, take top result
        }

        logger.info("Searching for: %s", query)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if not info or "entries" not in info or not info["entries"]:
                return f"Couldn't find '{query}' on YouTube."
            entry = info["entries"][0]
            title = entry.get("title", query)
            stream_url = entry.get("url") or entry.get("webpage_url")

        if not stream_url:
            return f"Found '{title}' but couldn't get stream URL."

        player = _find_player()

        with _player_lock:
            if player and "vlc" in player.lower():
                # VLC: play audio only, no video window, minimal UI
                _player_process = subprocess.Popen(
                    [player, "--intf", "dummy", "--play-and-exit",
                     "--no-video", stream_url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            elif player:
                # Windows Media Player
                _player_process = subprocess.Popen(
                    [player, stream_url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                # No player found — open in browser as fallback
                import skills.browser_control as bc
                bc.open_url(stream_url)
                return f"Playing '{title}' in browser (install VLC for better experience)."

        return f"Now playing: {title} 🎵"

    except Exception as e:
        logger.error("Music playback error: %s", e)
        return f"Music error: {str(e)[:100]}"


def stop_music() -> str:
    """Stop currently playing music."""
    global _player_process
    with _player_lock:
        if _player_process and _player_process.poll() is None:
            _player_process.terminate()
            _player_process = None
            return "Music stopped."
    return "Nothing is playing."


def is_playing() -> bool:
    """Return True if music is currently playing."""
    with _player_lock:
        return _player_process is not None and _player_process.poll() is None
