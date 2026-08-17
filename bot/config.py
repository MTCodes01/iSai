"""
config.py — iSai Bot Configuration
====================================
All tunable settings live here. Sensitive values (TOKEN) are loaded from a
.env file so they are never hard-coded into source control.

Usage:
    from bot.config import TOKEN, MUSIC_FOLDER, FFMPEG_PATH, DEFAULT_VOLUME
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load environment variables from .env (if it exists)
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# Discord & Multi-Instance Configuration
# ---------------------------------------------------------------------------
TOKEN: str = os.getenv("DISCORD_TOKEN", "")
"""Your Discord bot token. Set via DISCORD_TOKEN in your .env file."""

BOT_INSTANCE_ID: str = os.getenv("BOT_INSTANCE_ID", "")
"""Identifier for this bot instance, e.g., 'BOT-1'."""

ASSIGNED_VC_ID: str = os.getenv("ASSIGNED_VC_ID", "")
"""Optional voice channel ID that this bot instance is restricted to."""

IPC_PORT: int = int(os.getenv("IPC_PORT", "0"))
"""The port on which this instance's IPC HTTP server will listen. 0 means disabled."""

# ---------------------------------------------------------------------------
# File-system paths
# ---------------------------------------------------------------------------
# Root of the project (one level above bot/)
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

MUSIC_FOLDER: Path = Path(os.getenv("MUSIC_FOLDER", str(_PROJECT_ROOT / "music")))
"""Absolute path to the local music library root directory."""

FFMPEG_PATH: str = os.getenv("FFMPEG_PATH", "ffmpeg")
"""
Path to the ffmpeg executable.
  Windows system-wide install: 'ffmpeg'
  Custom install: r'C:\\ffmpeg\\bin\\ffmpeg.exe'
"""

# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------
DEFAULT_VOLUME: float = float(os.getenv("DEFAULT_VOLUME", "0.5"))
"""Default playback volume (0.0 – 1.0)."""

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
CACHE_FILE: Path = _PROJECT_ROOT / ".music_cache.json"
"""JSON file used to persist the scanned music index across restarts."""

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
FUZZY_SCORE_THRESHOLD: int = int(os.getenv("FUZZY_SCORE_THRESHOLD", "55"))
"""Minimum rapidfuzz similarity score (0-100) to accept a match."""

TOP_SEARCH_RESULTS: int = 10
"""Maximum number of results returned by /search."""

# ---------------------------------------------------------------------------
# Supported audio extensions (lower-case, without leading dot)
# ---------------------------------------------------------------------------
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {"mp3", "flac", "wav", "m4a", "ogg"}
)
