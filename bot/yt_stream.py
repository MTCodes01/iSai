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
