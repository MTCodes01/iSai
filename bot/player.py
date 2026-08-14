"""
player.py — iSai Music Player
================================
Manages per-guild audio playback state including:
  - An async FIFO queue of Song objects
  - FFmpeg-based audio streaming via discord.py's VoiceClient
  - Looping modes: single-song and entire-queue
  - Graceful error handling and automatic playback of the next track

Architecture notes:
  • One GuildPlayer instance is created per Discord guild (server) and stored
    in a dict keyed by guild_id.
  • The _play_next() coroutine is invoked automatically after each track ends
    via the FFmpeg 'after' callback, which runs in a thread-pool executor.
  • asyncio.Event is used to signal that playback of the current song has
    finished so that the next one can begin.
"""

import asyncio
import logging
import random
from collections import deque
from typing import Optional

import discord
from discord.ext import commands

from bot.config import FFMPEG_PATH, DEFAULT_VOLUME
from bot.music_library import Song

log = logging.getLogger("iSai.player")


# ---------------------------------------------------------------------------
# FFmpeg options
# ---------------------------------------------------------------------------

# 'reconnect' options help when streaming large files on slower disks.
FFMPEG_OPTIONS: dict[str, str] = {
    "options": "-vn",  # disable video streams (audio only)
}

FFMPEG_BEFORE_OPTIONS: str = (
    "-reconnect 1 "
    "-reconnect_streamed 1 "
    "-reconnect_delay_max 5"
)


# ---------------------------------------------------------------------------
# Guild Player
# ---------------------------------------------------------------------------

class GuildPlayer:
    """
    Manages audio playback state for a single Discord guild.

    Attributes
    ----------
    guild_id : int
        The Discord guild this player belongs to.
    voice_client : discord.VoiceClient or None
        Active voice connection; None when disconnected.
    queue : deque[Song]
        Ordered queue of upcoming songs.
    current : Song or None
        The song currently being played (or None if idle).
    loop_song : bool
        If True, the current song will repeat indefinitely.
    loop_queue : bool
        If True, songs are re-added to the end of the queue after playing.
    volume : float
        Playback volume (0.0 – 1.0).
    """

    def __init__(self, guild_id: int) -> None:
        self.guild_id: int = guild_id
        self.voice_client: Optional[discord.VoiceClient] = None

        # Use a deque for O(1) popleft (next song) and append (queue song)
        self.queue: deque[Song] = deque()

        self.current: Optional[Song] = None
        self.loop_song: bool = False
        self.loop_queue: bool = False
        self.volume: float = DEFAULT_VOLUME

        # Internal flag: prevents two concurrent _play_next tasks
        self._playing: bool = False

        # asyncio loop reference — set when first needed
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        """
        Connect to a voice channel, or move to it if already connected
        to a different channel in the same guild.
        """
        if self.voice_client and self.voice_client.is_connected():
            if self.voice_client.channel.id != channel.id:
                await self.voice_client.move_to(channel)
        else:
            self.voice_client = await channel.connect()
        return self.voice_client

    async def disconnect(self) -> None:
        """Stop playback, clear the queue, and disconnect from voice."""
        self.queue.clear()
        self.current = None
        self.loop_song = False
        self.loop_queue = False
        self._playing = False

        if self.voice_client:
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
            await self.voice_client.disconnect()
            self.voice_client = None

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def enqueue(self, song: Song) -> int:
        """
        Add a song to the end of the queue.

        Returns the song's position in the queue (1-indexed), so callers
        can tell the user "Added to queue at position N".
        """
        self.queue.append(song)
        return len(self.queue)

    def clear_queue(self) -> None:
        """Remove all pending songs from the queue."""
        self.queue.clear()

    def shuffle_queue(self) -> None:
        """Randomly reorder the pending queue."""
        songs = list(self.queue)
        random.shuffle(songs)
        self.queue = deque(songs)

    # ------------------------------------------------------------------
    # Playback control
    # ------------------------------------------------------------------

    async def play(self, song: Song, text_channel: discord.TextChannel) -> None:
        """
        Immediately start playing *song*, interrupting any current track.
        Use enqueue() + start() for regular queued playback.
        """
        self.current = song
        await self._stream(song, text_channel)

    async def start(self, text_channel: discord.TextChannel) -> None:
        """
        Begin processing the queue if not already playing.
        Safe to call when already playing — acts as a no-op.
        """
        if not self._playing:
            await self._play_next(text_channel)

    async def skip(self, text_channel: discord.TextChannel) -> Optional[Song]:
        """
        Skip the currently playing song and immediately start the next one.

        Returns the song that was skipped, or None if nothing was playing.
        """
        skipped = self.current

        # Temporarily disable song-loop so skip actually advances the queue
        was_looping = self.loop_song
        self.loop_song = False

        if self.voice_client and self.voice_client.is_playing():
            # stop() will trigger the 'after' callback which calls _play_next
            self.voice_client.stop()
        else:
            await self._play_next(text_channel)

        self.loop_song = was_looping
        return skipped

    async def stop(self) -> None:
        """Stop playback and clear the queue (but stay connected)."""
        self.queue.clear()
        self.current = None
        self.loop_song = False
        self._playing = False

        if self.voice_client:
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()

    def pause(self) -> bool:
        """
        Pause playback.

        Returns True if paused successfully, False if not playing.
        """
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            return True
        return False

    def resume(self) -> bool:
        """
        Resume a paused track.

        Returns True if resumed successfully, False if not paused.
        """
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            return True
        return False

    # ------------------------------------------------------------------
    # Internal playback engine
    # ------------------------------------------------------------------

    async def _play_next(self, text_channel: discord.TextChannel) -> None:
        """
        Attempt to play the next song in the queue.

        Called:
          • directly when playback starts for the first time, OR
          • from the FFmpeg 'after' callback when a song finishes.

        Loop behaviour:
          • loop_song=True  → replay self.current without popping the queue
          • loop_queue=True → re-add finished song to end of queue
        """
        if not self.voice_client or not self.voice_client.is_connected():
            self._playing = False
            return

        # --- Determine the next song to play ---
        if self.loop_song and self.current:
            # Replay the same song
            next_song = self.current
        elif self.queue:
            # Advance to next song; optionally re-queue the finished song
            if self.loop_queue and self.current:
                self.queue.append(self.current)
            next_song = self.queue.popleft()
        else:
            # Queue exhausted
            self._playing = False
            self.current = None
            log.info("[Guild %d] Queue finished.", self.guild_id)
            return

        self.current = next_song
        self._playing = True
        await self._stream(next_song, text_channel)

    async def _stream(self, song: Song, text_channel: discord.TextChannel) -> None:
        """
        Create an FFmpegPCMAudio source for *song* and start streaming it.

        The 'after' callback schedules _play_next on the event loop so that
        the next track begins automatically when this one ends.
        """
        if not self.voice_client:
            return

        try:
            source = discord.FFmpegPCMAudio(
                song.path,
                executable=FFMPEG_PATH,
                **FFMPEG_OPTIONS,
            )
            # Wrap with PCMVolumeTransformer to allow runtime volume changes
            source = discord.PCMVolumeTransformer(source, volume=self.volume)
        except Exception as exc:
            log.error("Failed to create audio source for '%s': %s", song.title, exc)
            # Try to recover by playing the next song
            asyncio.get_event_loop().create_task(self._play_next(text_channel))
            return

        def after_callback(error: Optional[Exception]) -> None:
            """
            Called by discord.py in a thread-pool thread when a song finishes.
            We schedule _play_next on the event loop from here.
            """
            if error:
                log.error("Playback error for '%s': %s", song.title, error)

            # Use call_soon_threadsafe because this runs in a worker thread
            loop = asyncio.get_event_loop()
            coro = self._play_next(text_channel)
            asyncio.run_coroutine_threadsafe(coro, loop)

        self.voice_client.play(source, after=after_callback)
        log.info("[Guild %d] Now playing: %s — %s", self.guild_id, song.artist, song.title)


# ---------------------------------------------------------------------------
# Player Registry
# ---------------------------------------------------------------------------

class PlayerManager:
    """
    Global registry of GuildPlayer instances, one per guild.

    Usage:
        manager = PlayerManager()
        player = manager.get(guild_id)
    """

    def __init__(self) -> None:
        self._players: dict[int, GuildPlayer] = {}

    def get(self, guild_id: int) -> GuildPlayer:
        """Return the GuildPlayer for *guild_id*, creating one if needed."""
        if guild_id not in self._players:
            self._players[guild_id] = GuildPlayer(guild_id)
            log.debug("Created new GuildPlayer for guild %d", guild_id)
        return self._players[guild_id]

    def remove(self, guild_id: int) -> None:
        """Remove the GuildPlayer for *guild_id* from the registry."""
        self._players.pop(guild_id, None)
