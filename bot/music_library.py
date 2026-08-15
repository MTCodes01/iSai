"""
music_library.py — iSai Music Library
========================================
Responsible for:
  1. Recursively scanning the local music folder on startup.
  2. Reading audio metadata (title, artist, album, duration) via mutagen.
  3. Building a searchable in-memory index of Song objects.
  4. Persisting the index to a JSON cache file so rescanning is fast on restart.
  5. Performing fuzzy search using rapidfuzz.

Directory layout expected:
    music/
        Artist Name/
            Song Title.mp3
            Another Song.flac
        Another Artist/
            ...

The artist name is inferred from the immediate parent directory of the file.
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from mutagen import File as MutagenFile
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE
from rapidfuzz import fuzz, process

from bot.config import (
    MUSIC_FOLDER,
    CACHE_FILE,
    SUPPORTED_EXTENSIONS,
    FUZZY_SCORE_THRESHOLD,
    TOP_SEARCH_RESULTS,
)
from bot.utils import normalise

log = logging.getLogger("iSai.library")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Song:
    """
    Represents a single audio track in the library.

    All string fields are guaranteed to be non-empty (falling back to
    sensible defaults derived from the file name / directory structure).
    """

    # Resolved absolute path to the audio file
    path: str

    # Display-friendly fields (may come from metadata or inferred from path)
    title: str
    artist: str
    album: str

    # Duration in seconds (None if mutagen cannot read it)
    duration: Optional[float]

    # Lower-cased, punctuation-stripped composite search key used by fuzzy search
    search_key: str

    # Whether this song is a remote internet stream (e.g., from yt-dlp)
    is_stream: bool = False

    def to_dict(self) -> dict:
        """Serialise to a plain dict (for JSON caching)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Song":
        """Deserialise from a plain dict (loaded from JSON cache)."""
        return cls(**data)


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

def _read_metadata(file_path: Path) -> tuple[str, str, str, Optional[float]]:
    """
    Read ID3 / Vorbis / MP4 / RIFF metadata from an audio file.

    Returns (title, artist, album, duration_seconds).
    Falls back to file/directory names when a tag is absent.

    Mutagen is used because it is pure-Python and supports all required
    formats without needing external libraries.
    """
    stem = file_path.stem            # file name without extension
    parent = file_path.parent.name   # immediate parent directory = artist folder

    title: str = stem
    artist: str = parent
    album: str = ""
    duration: Optional[float] = None

    try:
        audio = MutagenFile(str(file_path), easy=True)
        if audio is None:
            return title, artist, album, duration

        # Attempt to read common tag fields
        title = str(audio.get("title", [stem])[0]) or stem
        artist = str(audio.get("artist", [parent])[0]) or parent
        album = str(audio.get("album", [""])[0])

        # Duration: try the mutagen 'info' attribute
        if hasattr(audio, "info") and hasattr(audio.info, "length"):
            duration = float(audio.info.length)

    except Exception as exc:
        # Metadata reading failures are non-fatal — we just use fallbacks.
        log.debug("Could not read metadata for %s: %s", file_path.name, exc)

    return title, artist, album, duration


# ---------------------------------------------------------------------------
# Library scanner
# ---------------------------------------------------------------------------

class MusicLibrary:
    """
    Manages the in-memory index of all songs in the local music folder.

    Typical usage:
        library = MusicLibrary()
        await library.scan()          # or library.scan_sync()
        results = library.search("bohemian")
    """

    def __init__(self) -> None:
        self._songs: list[Song] = []
        self._last_scan: float = 0.0  # Unix timestamp of last successful scan

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def songs(self) -> list[Song]:
        """Read-only view of all indexed songs."""
        return self._songs

    @property
    def count(self) -> int:
        """Total number of songs in the library."""
        return len(self._songs)

    @property
    def last_scan_time(self) -> float:
        """Unix timestamp of the last successful library scan."""
        return self._last_scan

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan(self) -> int:
        """
        Synchronously scan MUSIC_FOLDER, build the song index, and persist
        the cache.

        Returns the number of songs found.

        Strategy:
          1. Try loading from the JSON cache first (fast path).
          2. If the cache is stale or missing, walk the directory tree.
          3. Write the updated index back to the cache.
        """
        if self._load_cache():
            log.info("Loaded %d songs from cache.", self.count)
            return self.count

        log.info("Scanning music folder: %s", MUSIC_FOLDER)
        return self._full_scan()

    def rescan(self) -> int:
        """
        Force a full directory scan, ignoring any cached data.
        Use this after adding or removing files from the music folder.
        """
        log.info("Forcing full rescan of music folder…")
        return self._full_scan()

    def _full_scan(self) -> int:
        """Walk the music folder and build the in-memory index."""
        songs: list[Song] = []

        if not MUSIC_FOLDER.exists():
            log.warning("Music folder not found: %s", MUSIC_FOLDER)
            MUSIC_FOLDER.mkdir(parents=True, exist_ok=True)
            self._songs = songs
            return 0

        for file_path in sorted(MUSIC_FOLDER.rglob("*")):
            # Only process files whose extension is in the supported set
            if not file_path.is_file():
                continue
            ext = file_path.suffix.lstrip(".").lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue

            title, artist, album, duration = _read_metadata(file_path)

            # Build the composite search key used by fuzzy matching.
            # Combining artist and title gives better results when the user
            # searches by artist name or a combination of both.
            search_key = normalise(f"{artist} {title}")

            song = Song(
                path=str(file_path),
                title=title,
                artist=artist,
                album=album,
                duration=duration,
                search_key=search_key,
            )
            songs.append(song)
            log.debug("Indexed: %s — %s", artist, title)

        self._songs = songs
        self._last_scan = time.time()
        self._save_cache()
        log.info("Scan complete. %d songs indexed.", self.count)
        return self.count

    # ------------------------------------------------------------------
    # Cache persistence
    # ------------------------------------------------------------------

    def _save_cache(self) -> None:
        """Write the current index to the JSON cache file."""
        try:
            data = {
                "timestamp": self._last_scan,
                "songs": [s.to_dict() for s in self._songs],
            }
            CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            log.debug("Cache saved to %s", CACHE_FILE)
        except Exception as exc:
            log.warning("Could not save cache: %s", exc)

    def _load_cache(self) -> bool:
        """
        Attempt to load the song index from the JSON cache.

        Returns True if the cache was loaded successfully.
        Returns False if the cache is missing, corrupt, or older than the
        music folder's modification time (so new files are picked up).
        """
        if not CACHE_FILE.exists():
            return False

        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            cache_time: float = data.get("timestamp", 0.0)

            # Invalidate cache if the music folder is newer
            if MUSIC_FOLDER.exists():
                folder_mtime = MUSIC_FOLDER.stat().st_mtime
                if folder_mtime > cache_time:
                    log.debug("Cache is older than music folder; rescanning.")
                    return False

            self._songs = [Song.from_dict(s) for s in data.get("songs", [])]
            self._last_scan = cache_time
            return True

        except Exception as exc:
            log.warning("Cache load failed (%s); will rescan.", exc)
            return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = TOP_SEARCH_RESULTS,
        threshold: int = FUZZY_SCORE_THRESHOLD,
    ) -> list[tuple[Song, float]]:
        """
        Fuzzy-search the library for songs matching *query*.

        Uses rapidfuzz's token_set_ratio scorer, which handles word-order
        differences well (e.g. 'Queen Bohemian' matches 'Bohemian Rhapsody
        by Queen').

        Parameters
        ----------
        query : str
            Raw search string (will be normalised internally).
        limit : int
            Maximum number of results to return.
        threshold : int
            Minimum score (0-100) for a result to be included.

        Returns
        -------
        list of (Song, score) tuples, sorted by score descending.
        """
        if not self._songs:
            return []

        normalised_query = normalise(query)

        # Build choices list
        choices = [song.search_key for song in self._songs]

        # rapidfuzz.process.extract returns (match, score, index) tuples
        matches = process.extract(
            normalised_query,
            choices,
            scorer=fuzz.token_set_ratio,
            limit=limit,
            score_cutoff=threshold,
        )

        results: list[tuple[Song, float]] = []
        for _matched_string, score, index in matches:
            results.append((self._songs[index], float(score)))

        # Sort by score descending (rapidfuzz usually returns them sorted,
        # but we enforce it explicitly for safety)
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def get_random(self) -> Optional[Song]:
        """Return a uniformly random song from the library, or None if empty."""
        if not self._songs:
            return None
        import random
        return random.choice(self._songs)
