"""
player.py — iSai Music Player
================================
Manages per-guild audio playback state including:
  - An async FIFO queue of Song objects backed by SQLite
  - FFmpeg-based audio streaming via discord.py's VoiceClient
  - Looping modes: single-song and entire-queue
  - Graceful error handling and automatic playback of the next track

Architecture notes:
  • One GuildPlayer instance is created per Discord guild (server) and stored
    in a dict keyed by guild_id.
  • The _play_next() coroutine is invoked automatically after each track ends
    via the FFmpeg 'after' callback, which runs in a thread-pool executor.
"""

import asyncio
import logging
import random
from typing import Optional, List

import discord
from discord.ext import commands

from bot.config import FFMPEG_PATH, DEFAULT_VOLUME
from bot.music_library import Song, MusicLibrary
import bot.db as db

log = logging.getLogger("iSai.player")

# ---------------------------------------------------------------------------
# FFmpeg options
# ---------------------------------------------------------------------------

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
    def __init__(self, guild_id: int, library: Optional[MusicLibrary] = None) -> None:
        self.guild_id: int = guild_id
        self.library: Optional[MusicLibrary] = library
        self.voice_client: Optional[discord.VoiceClient] = None

        self._playing: bool = False
        self._is_manual_stop: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Load transient state (if any)
        # We read from DB to initialize
        state = db.get_player_state(self.guild_id)
        # current is now stored in DB
        
    # --- Properties mapping to DB ---
    
    @property
    def current(self) -> Optional[Song]:
        return db.get_player_state(self.guild_id).get('current_song')
        
    @current.setter
    def current(self, value: Optional[Song]):
        db.update_player_state(self.guild_id, current_song_json=value)
        
    @property
    def queue(self) -> List[Song]:
        return db.get_queue(self.guild_id)
        
    @property
    def queue_length(self) -> int:
        return db.get_queue_length(self.guild_id)
        
    @property
    def history(self) -> List[Song]:
        return db.get_history(self.guild_id)
        
    @property
    def loop_song(self) -> bool:
        return db.get_player_state(self.guild_id).get('loop_song', False)
        
    @loop_song.setter
    def loop_song(self, value: bool):
        db.update_player_state(self.guild_id, loop_song=value)
        
    @property
    def loop_queue(self) -> bool:
        return db.get_player_state(self.guild_id).get('loop_queue', False)
        
    @loop_queue.setter
    def loop_queue(self, value: bool):
        db.update_player_state(self.guild_id, loop_queue=value)
        
    @property
    def autoplay(self) -> bool:
        return db.get_player_state(self.guild_id).get('autoplay', False)
        
    @autoplay.setter
    def autoplay(self, value: bool):
        db.update_player_state(self.guild_id, autoplay=value)
        
    @property
    def text_channel_id(self) -> Optional[int]:
        return db.get_player_state(self.guild_id).get('text_channel_id')
        
    @text_channel_id.setter
    def text_channel_id(self, value: Optional[int]):
        db.update_player_state(self.guild_id, text_channel_id=value)
        
    @property
    def volume(self) -> float:
        return db.get_player_state(self.guild_id).get('volume', DEFAULT_VOLUME)

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        if self.voice_client and self.voice_client.is_connected():
            if self.voice_client.channel.id != channel.id:
                await self.voice_client.move_to(channel)
        else:
            self.voice_client = await channel.connect()
        self._loop = self.voice_client.client.loop
        return self.voice_client

    async def disconnect(self) -> None:
        db.clear_queue(self.guild_id)
        self.current = None
        self.loop_song = False
        self.loop_queue = False
        self.autoplay = False
        db.clear_history(self.guild_id)
        self._playing = False

        if self.voice_client:
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self._is_manual_stop = True
            self.voice_client.stop()
            await self.voice_client.disconnect()
            self.voice_client = None

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def enqueue(self, song: Song) -> int:
        return db.enqueue(self.guild_id, song)

    def clear_queue(self) -> None:
        db.clear_queue(self.guild_id)

    def shuffle_queue(self) -> None:
        db.shuffle_queue(self.guild_id)

    def set_volume(self, volume: float) -> None:
        vol = max(0.0, min(1.0, volume))
        db.update_player_state(self.guild_id, volume=vol)
        if self.voice_client and self.voice_client.source:
            if getattr(self.voice_client.source, "volume", None) is not None:
                self.voice_client.source.volume = vol

    # ------------------------------------------------------------------
    # Playback control
    # ------------------------------------------------------------------

    async def play(self, song: Song, text_channel: discord.TextChannel = None) -> None:
        self.current = song
        await self._stream(song, text_channel)

    async def start(self, text_channel: discord.TextChannel = None) -> None:
        if not self._playing:
            await self._play_next(text_channel)

    async def skip(self, text_channel: discord.TextChannel = None) -> Optional[Song]:
        skipped = self.current
        was_looping = self.loop_song
        self.loop_song = False

        if self.voice_client and self.voice_client.is_playing():
            self._is_manual_stop = True
            self.voice_client.stop()
        else:
            await self._play_next(text_channel)

        self.loop_song = was_looping
        return skipped

    async def prev(self, text_channel: discord.TextChannel = None) -> Optional[Song]:
        prev_song = db.pop_history(self.guild_id)
        if not prev_song:
            return None
            
        was_looping = self.loop_song
        self.loop_song = False
        
        curr = self.current
        if curr:
            db.push_front(self.guild_id, curr)
            self.current = None
            
        db.push_front(self.guild_id, prev_song)
        
        if self.voice_client and self.voice_client.is_playing():
            self._is_manual_stop = True
            self.voice_client.stop()
        else:
            await self._play_next(text_channel)
            
        self.loop_song = was_looping
        return prev_song

    async def stop(self) -> None:
        db.clear_queue(self.guild_id)
        self.current = None
        self.loop_song = False
        self._playing = False

        if self.voice_client:
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self._is_manual_stop = True
            self.voice_client.stop()

    def pause(self) -> bool:
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            return True
        return False

    def resume(self) -> bool:
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            return True
        return False

    # ------------------------------------------------------------------
    # Internal playback engine
    # ------------------------------------------------------------------

    async def _play_next(self, text_channel: discord.TextChannel = None, auto_next: bool = False) -> None:
        if not self.voice_client or not self.voice_client.is_connected():
            self._playing = False
            return

        if text_channel:
            self.text_channel_id = text_channel.id
        elif self.text_channel_id:
            text_channel = self.voice_client.client.get_channel(self.text_channel_id)

        curr = self.current
        next_song = None

        if self.loop_song and curr:
            next_song = curr
        else:
            if curr:
                if self.loop_queue:
                    db.enqueue(self.guild_id, curr)
                db.push_history(self.guild_id, curr)
                
            next_song = db.pop_next(self.guild_id)
            
            if not next_song and self.autoplay and self.library:
                next_song = self._get_autoplay_song()
                
        if not next_song:
            self._playing = False
            self.current = None
            log.info("[Guild %d] Queue finished.", self.guild_id)
            return

        self.current = next_song
        self._playing = True
        
        if auto_next and text_channel:
            try:
                from bot.commands import PlayerControls
                from bot.utils import make_embed, format_duration
                
                fields = [
                    ("Artist", next_song.artist, True),
                    ("Duration", format_duration(next_song.duration) if next_song.duration else "Unknown", True),
                ]
                if next_song.album and next_song.album != 'Unknown Album':
                    fields.append(("Album", next_song.album, True))
                
                embed = make_embed(
                    title="▶️ Now Playing",
                    description=f"**{next_song.title}**",
                    fields=fields,
                )
                
                asyncio.run_coroutine_threadsafe(
                    text_channel.send(embed=embed, view=PlayerControls(self.guild_id, self.voice_client.channel.id if self.voice_client else None)),
                    self._loop
                )
            except Exception as e:
                log.error("Failed to send Now Playing message: %s", e)

        await self._stream(next_song, text_channel)

    def _get_autoplay_song(self) -> Optional[Song]:
        if not self.library:
            return None

        history = self.history
        recent_artists = list({song.artist for song in history if not getattr(song, 'is_stream', False)})
        history_paths = {song.path for song in history}

        if recent_artists:
            chosen_artist = random.choice(recent_artists)
            results = self.library.search(chosen_artist, limit=100, threshold=50)
            valid_songs = [s for s, score in results if s.path not in history_paths]
            if valid_songs:
                return random.choice(valid_songs)
        
        all_valid = [s for s in self.library.songs if s.path not in history_paths]
        if all_valid:
            return random.choice(all_valid)
            
        return self.library.get_random()

    async def _stream(self, song: Song, text_channel: discord.TextChannel = None) -> None:
        if not self.voice_client:
            return

        try:
            if getattr(song, "is_unresolved", False):
                from bot.yt_stream import search_song
                log.info("Resolving stream URL for '%s'...", song.title)
                info = await search_song(song.path)
                if info and "url" in info:
                    song.path = info["url"]
                    song.stream_headers = info.get("http_headers")
                    song.duration = info.get("duration", song.duration)
                    song.is_unresolved = False
                    self.current = song # Save resolved metadata
                else:
                    raise Exception(f"Could not resolve stream URL for {song.path}")

            options = dict(FFMPEG_OPTIONS)
            if song.is_stream:
                import shlex
                before_opts = FFMPEG_BEFORE_OPTIONS
                if getattr(song, "stream_headers", None):
                    headers_str = "".join(f"{k}: {v}\r\n" for k, v in song.stream_headers.items())
                    before_opts = f"-headers {shlex.quote(headers_str)} " + before_opts
                options["before_options"] = before_opts

            source = discord.FFmpegPCMAudio(
                song.path,
                executable=FFMPEG_PATH,
                **options,
            )
            source = discord.PCMVolumeTransformer(source, volume=self.volume)
        except Exception as exc:
            log.error("Failed to create audio source for '%s': %s", song.title, exc)
            asyncio.get_event_loop().create_task(self._play_next(text_channel))
            return

        def after_callback(error: Optional[Exception]) -> None:
            if error:
                log.error("Playback error for '%s': %s", song.title, error)

            if self._loop:
                is_manual = getattr(self, '_is_manual_stop', False)
                self._is_manual_stop = False
                coro = self._play_next(text_channel, auto_next=not is_manual)
                asyncio.run_coroutine_threadsafe(coro, self._loop)

        self.voice_client.play(source, after=after_callback)
        log.info("[Guild %d] Now playing: %s — %s", self.guild_id, song.artist, song.title)


# ---------------------------------------------------------------------------
# Player Registry
# ---------------------------------------------------------------------------

class PlayerManager:
    def __init__(self, library: Optional[MusicLibrary] = None) -> None:
        self.library = library
        self._players: dict[int, GuildPlayer] = {}
        # We can restore state for any players that have active voice clients
        # but since voice clients reset on restart, we only initialize players on demand.

    def get(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self._players:
            self._players[guild_id] = GuildPlayer(guild_id, self.library)
            log.debug("Created new GuildPlayer for guild %d", guild_id)
        return self._players[guild_id]

    def remove(self, guild_id: int) -> None:
        self._players.pop(guild_id, None)
