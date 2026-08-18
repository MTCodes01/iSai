"""
yt_stream.py — iSai YouTube/Internet Stream Search
===================================================
Provides asynchronous search capabilities using yt-dlp.
"""

import asyncio
import logging
from typing import Optional, Dict, Any
import yt_dlp

log = logging.getLogger("iSai.yt_stream")

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": "True",
    "default_search": "auto",
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",  # bind to ipv4 to prevent issues
    "extractor_args": {
        "youtube": {
            "player_client": ["web_embedded", "default"]
        }
    }
}

def _extract_info_sync(query: str) -> Optional[Dict[str, Any]]:
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        try:
            info = ydl.extract_info(f"ytsearch:{query}", download=False)
            if info and "entries" in info and len(info["entries"]) > 0:
                return info["entries"][0]
            return None
        except Exception as exc:
            log.error("yt-dlp extraction failed for query '%s': %s", query, exc)
            return None

async def search_song(query: str) -> Optional[Dict[str, Any]]:
    """
    Search for a song using yt-dlp asynchronously.
    Returns the first search result's metadata, or None if failed.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _extract_info_sync, query)

def _extract_playlist_sync(url: str) -> Optional[list[Dict[str, Any]]]:
    options = dict(YDL_OPTIONS)
    options["extract_flat"] = "in_playlist"
    options["noplaylist"] = "False"
    with yt_dlp.YoutubeDL(options) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if info and "entries" in info:
                entries = []
                for e in info["entries"]:
                    if e:
                        entries.append(e)
                return entries
            return None
        except Exception as exc:
            log.error("yt-dlp extraction failed for playlist '%s': %s", url, exc)
            return None

async def search_playlist(url: str) -> Optional[list[Dict[str, Any]]]:
    """
    Search for a playlist using yt-dlp asynchronously.
    Returns the playlist entries, or None if failed.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _extract_playlist_sync, url)
