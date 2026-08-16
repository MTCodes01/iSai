"""
commands.py — iSai Bot Slash Commands
=======================================
Defines all user-facing slash commands as a discord.py Cog.

Each command follows this pattern:
  1. Defer the interaction (so Discord shows "iSai is thinking…").
  2. Validate preconditions (user in voice channel, library not empty, etc.).
  3. Perform the action.
  4. Send a rich Discord embed response.

All commands are application commands using discord.app_commands so they
appear in Discord's slash-command menu with descriptions and type-safe args.
"""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.music_library import MusicLibrary, Song
from bot.player import PlayerManager, GuildPlayer
from bot.yt_stream import search_song
from bot.config import TOP_SEARCH_RESULTS
from bot.utils import (
    make_embed,
    error_embed,
    success_embed,
    format_duration,
    EMBED_COLOR,
)

log = logging.getLogger("iSai.commands")


# ---------------------------------------------------------------------------
# Helper — voice channel guard
# ---------------------------------------------------------------------------

def _get_voice_channel(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    """
    Return the voice channel the interaction author is currently in,
    or None if they are not in a voice channel.
    """
    member = interaction.user
    if isinstance(member, discord.Member) and member.voice and member.voice.channel:
        channel = member.voice.channel
        if isinstance(channel, discord.VoiceChannel):
            return channel
    return None


def _format_song_line(song: Song, idx: int) -> str:
    """Format a single song entry for use in queue / search result lists."""
    duration = format_duration(song.duration)
    return f"`{idx}.` **{song.title}** — *{song.artist}* `[{duration}]`"


# ---------------------------------------------------------------------------
# UI Components
# ---------------------------------------------------------------------------

class PlaylistPagination(discord.ui.View):
    def __init__(self, songs: list[Song]):
        super().__init__(timeout=180)
        self.songs = songs
        self.current_page = 0
        self.max_page = max(0, (len(songs) - 1) // 10)
        self.update_buttons()

    def format_page(self) -> discord.Embed:
        start = self.current_page * 10
        end = start + 10
        page_songs = self.songs[start:end]
        
        description = ""
        for i, song in enumerate(page_songs, start=start + 1):
            description += f"`{i}.` **{song.title}** — *{song.artist}*\n"
            
        embed = discord.Embed(
            title=f"🎵 Library Playlist ({len(self.songs)} songs)",
            description=description,
            color=EMBED_COLOR
        )
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.max_page + 1}")
        return embed

    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.max_page

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.format_page(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.format_page(), view=self)

# ---------------------------------------------------------------------------
# Cog definition
# ---------------------------------------------------------------------------

class MusicCog(commands.Cog):
    """
    All music-related slash commands.

    This Cog holds references to the shared MusicLibrary and PlayerManager
    so every command can access them without global state.
    """

    def __init__(self, bot: commands.Bot, library: MusicLibrary, manager: PlayerManager) -> None:
        self.bot = bot
        self.library = library
        self.manager = manager

    # -----------------------------------------------------------------------
    # /play
    # -----------------------------------------------------------------------

    @app_commands.command(name="play", description="Search and play a song from the local library.")
    @app_commands.describe(song="Song name, artist, or any search term.")
    async def play(self, interaction: discord.Interaction, song: str) -> None:
        """
        Search the library for *song* and play the best match.

        Behaviour:
          • If only one strong match exists → play immediately.
          • If multiple matches exist → display the top results and play
            the best one anyway (so the user doesn't have to wait).
          • If no matches → report an error.
        """
        await interaction.response.defer()

        # --- Voice channel check ---
        vc = _get_voice_channel(interaction)
        if vc is None:
            await interaction.followup.send(
                embed=error_embed("You must be in a voice channel to use this command.")
            )
            return

        # --- Search ---
        results = self.library.search(song)
        random_fallback = False
        if not results:
            best_song = self.library.get_random()
            if not best_song:
                await interaction.followup.send(
                    embed=error_embed(
                        f"No songs found matching **{song}**.\n"
                        "Try `/search` for broader results or `/rescan` if you added new files."
                    )
                )
                return
            best_score = 0.0
            random_fallback = True
        else:
            best_song, best_score = results[0]

        # --- Connect / move to voice channel ---
        player: GuildPlayer = self.manager.get(interaction.guild_id)
        try:
            await player.connect(vc)
        except Exception as exc:
            await interaction.followup.send(
                embed=error_embed(f"Could not connect to voice channel: {exc}")
            )
            return

        # --- Enqueue & start ---
        if player.voice_client.is_playing() or player.voice_client.is_paused():
            # Something is already playing → add to queue
            position = player.enqueue(best_song)
            embed = make_embed(
                title="➕ Added to Queue",
                description=f"**{best_song.title}**",
                fields=[
                    ("Artist", best_song.artist, True),
                    ("Duration", format_duration(best_song.duration), True),
                    ("Position in Queue", str(position), True),
                ],
            )
        else:
            # Nothing playing → start immediately
            player.enqueue(best_song)
            await player.start(interaction.channel)
            embed = make_embed(
                title="▶️ Now Playing",
                description=f"**{best_song.title}**",
                fields=[
                    ("Artist", best_song.artist, True),
                    ("Duration", format_duration(best_song.duration), True),
                    ("Match Score", f"{best_score:.0f}%", True),
                ],
            )
            if best_song.album:
                embed.add_field(name="Album", value=best_song.album, inline=True)

        # If multiple matches, append them as a secondary field
        if len(results) > 1:
            other_lines = "\n".join(
                f"`{i+1}.` {r.title} — *{r.artist}*" for i, (r, _) in enumerate(results[1:5])
            )
            embed.add_field(name="Other Matches", value=other_lines, inline=False)

        if random_fallback:
            embed.description = f"**{best_song.title}**\n\n*Nee ithu ketta mathi*"

        await interaction.followup.send(embed=embed)

    # -----------------------------------------------------------------------
    # /search
    # -----------------------------------------------------------------------

    @app_commands.command(name="search", description="Search the library and show the top matches.")
    @app_commands.describe(query="Search term (song title, artist, or both).")
    async def search(self, interaction: discord.Interaction, query: str) -> None:
        """Return the top matching songs without automatically playing them."""
        await interaction.response.defer()

        results = self.library.search(query, limit=TOP_SEARCH_RESULTS)
        if not results:
            await interaction.followup.send(
                embed=error_embed(f"No results found for **{query}**.")
            )
            return

        lines = [
            f"`{i+1}.` **{s.title}** — *{s.artist}* `[{format_duration(s.duration)}]` *(score: {sc:.0f})*"
            for i, (s, sc) in enumerate(results)
        ]
        embed = make_embed(
            title=f"🔍 Search Results for \"{query}\"",
            description="\n".join(lines),
        )
        embed.set_footer(text=f"🎵 iSai Music Bot  •  {len(results)} results found")
        await interaction.followup.send(embed=embed)

    # -----------------------------------------------------------------------
    # /random
    # -----------------------------------------------------------------------

    @app_commands.command(name="random", description="Play a random song from the library.")
    async def random(self, interaction: discord.Interaction) -> None:
        """Pick a random song and queue it up (or start playing immediately)."""
        await interaction.response.defer()

        vc = _get_voice_channel(interaction)
        if vc is None:
            await interaction.followup.send(
                embed=error_embed("You must be in a voice channel to use this command.")
            )
            return

        song = self.library.get_random()
        if song is None:
            await interaction.followup.send(
                embed=error_embed("The music library is empty. Add some songs first!")
            )
            return

        player: GuildPlayer = self.manager.get(interaction.guild_id)
        await player.connect(vc)

        if player.voice_client.is_playing() or player.voice_client.is_paused():
            position = player.enqueue(song)
            embed = make_embed(
                title="🎲 Random Song Added",
                description=f"**{song.title}**",
                fields=[
                    ("Artist", song.artist, True),
                    ("Duration", format_duration(song.duration), True),
                    ("Queue Position", str(position), True),
                ],
            )
        else:
            player.enqueue(song)
            await player.start(interaction.channel)
            embed = make_embed(
                title="🎲 Playing Random Song",
                description=f"**{song.title}**",
                fields=[
                    ("Artist", song.artist, True),
                    ("Duration", format_duration(song.duration), True),
                ],
            )

        await interaction.followup.send(embed=embed)

    # -----------------------------------------------------------------------
    # /queue
    # -----------------------------------------------------------------------

    @app_commands.command(name="queue", description="Show the current queue.")
    async def queue(self, interaction: discord.Interaction) -> None:
        """Display the current song and upcoming tracks."""
        await interaction.response.defer()

        player: GuildPlayer = self.manager.get(interaction.guild_id)

        if player.current is None and not player.queue:
            await interaction.followup.send(
                embed=make_embed(
                    title="📋 Queue",
                    description="The queue is empty. Use `/play` to add songs!",
                )
            )
            return

        lines: list[str] = []

        # Current song
        if player.current:
            status = "🔂" if player.loop_song else "▶️"
            lines.append(f"{status} **Now Playing:** {player.current.title} — *{player.current.artist}* `[{format_duration(player.current.duration)}]`")

        # Upcoming songs (show up to 20 to avoid embed size limits)
        if player.queue:
            lines.append("")
            lines.append("**Up Next:**")
            queue_list = list(player.queue)
            for i, s in enumerate(queue_list[:20], start=1):
                lines.append(_format_song_line(s, i))
            if len(queue_list) > 20:
                lines.append(f"*… and {len(queue_list) - 20} more songs*")

        # Loop status footer
        footer_parts = []
        if player.loop_song:
            footer_parts.append("🔂 Looping current song")
        if player.loop_queue:
            footer_parts.append("🔁 Looping queue")

        embed = make_embed(
            title=f"📋 Queue — {len(player.queue)} song(s) pending",
            description="\n".join(lines),
        )
        if footer_parts:
            embed.set_footer(text="  •  ".join(footer_parts))

        await interaction.followup.send(embed=embed)

    # -----------------------------------------------------------------------
    # /skip
    # -----------------------------------------------------------------------

    @app_commands.command(name="skip", description="Skip the current song.")
    async def skip(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: GuildPlayer = self.manager.get(interaction.guild_id)
        if player.current is None:
            await interaction.followup.send(
                embed=error_embed("Nothing is currently playing.")
            )
            return

        skipped = await player.skip(interaction.channel)
        embed = success_embed(
            title="⏭️ Skipped",
            description=f"**{skipped.title}** — *{skipped.artist}*" if skipped else "Track skipped.",
        )
        await interaction.followup.send(embed=embed)

    # -----------------------------------------------------------------------
    # /pause
    # -----------------------------------------------------------------------

    @app_commands.command(name="pause", description="Pause playback.")
    async def pause(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: GuildPlayer = self.manager.get(interaction.guild_id)
        if player.pause():
            await interaction.followup.send(
                embed=success_embed("⏸️ Paused", "Use `/resume` to continue.")
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Nothing is currently playing.")
            )

    # -----------------------------------------------------------------------
    # /resume
    # -----------------------------------------------------------------------

    @app_commands.command(name="resume", description="Resume paused playback.")
    async def resume(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: GuildPlayer = self.manager.get(interaction.guild_id)
        if player.resume():
            await interaction.followup.send(
                embed=success_embed("▶️ Resumed")
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Playback is not paused.")
            )

    # -----------------------------------------------------------------------
    # /stop
    # -----------------------------------------------------------------------

    @app_commands.command(name="stop", description="Stop playback and clear the queue.")
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: GuildPlayer = self.manager.get(interaction.guild_id)
        await player.stop()
        await interaction.followup.send(
            embed=success_embed("⏹️ Stopped", "Queue cleared. Use `/play` to start again.")
        )

    # -----------------------------------------------------------------------
    # /disconnect
    # -----------------------------------------------------------------------

    @app_commands.command(name="disconnect", description="Disconnect the bot from the voice channel.")
    async def disconnect(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: GuildPlayer = self.manager.get(interaction.guild_id)
        if player.voice_client is None or not player.voice_client.is_connected():
            await interaction.followup.send(
                embed=error_embed("I'm not connected to any voice channel.")
            )
            return

        await player.disconnect()
        await interaction.followup.send(
            embed=success_embed("👋 Disconnected", "Goodbye! Use `/play` to invite me back.")
        )

    # -----------------------------------------------------------------------
    # /nowplaying
    # -----------------------------------------------------------------------

    @app_commands.command(name="nowplaying", description="Show information about the current song.")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: GuildPlayer = self.manager.get(interaction.guild_id)
        song = player.current

        if song is None:
            await interaction.followup.send(
                embed=make_embed(
                    title="🎵 Now Playing",
                    description="Nothing is playing right now. Use `/play` to start!",
                )
            )
            return

        fields = [
            ("Artist", song.artist, True),
            ("Duration", format_duration(song.duration), True),
        ]
        if song.album:
            fields.append(("Album", song.album, True))

        # Show loop state
        loop_state = []
        if player.loop_song:
            loop_state.append("🔂 Song Loop")
        if player.loop_queue:
            loop_state.append("🔁 Queue Loop")
        if loop_state:
            fields.append(("Loop", "  •  ".join(loop_state), False))

        vc_status = "▶️ Playing" if (player.voice_client and player.voice_client.is_playing()) else "⏸️ Paused"
        fields.append(("Status", vc_status, True))
        fields.append(("Queue", f"{len(player.queue)} song(s) pending", True))

        embed = make_embed(
            title="🎵 Now Playing",
            description=f"**{song.title}**",
            fields=fields,
        )
        await interaction.followup.send(embed=embed)

    # -----------------------------------------------------------------------
    # /shuffle
    # -----------------------------------------------------------------------

    @app_commands.command(name="shuffle", description="Shuffle the current queue.")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: GuildPlayer = self.manager.get(interaction.guild_id)
        if not player.queue:
            await interaction.followup.send(
                embed=error_embed("The queue is empty — nothing to shuffle.")
            )
            return

        player.shuffle_queue()
        await interaction.followup.send(
            embed=success_embed(
                "🔀 Queue Shuffled",
                f"{len(player.queue)} songs reordered. Use `/queue` to see the new order.",
            )
        )

    # -----------------------------------------------------------------------
    # /loop
    # -----------------------------------------------------------------------

    @app_commands.command(name="loop", description="Toggle looping of the current song.")
    async def loop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: GuildPlayer = self.manager.get(interaction.guild_id)
        player.loop_song = not player.loop_song

        state = "enabled 🔂" if player.loop_song else "disabled"
        await interaction.followup.send(
            embed=success_embed(
                f"🔂 Song Loop {state.title()}",
                f"The current song will {'repeat indefinitely' if player.loop_song else 'not repeat'} after it finishes.",
            )
        )

    # -----------------------------------------------------------------------
    # /loopqueue
    # -----------------------------------------------------------------------

    @app_commands.command(name="loopqueue", description="Toggle looping of the entire queue.")
    async def loopqueue(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: GuildPlayer = self.manager.get(interaction.guild_id)
        player.loop_queue = not player.loop_queue

        state = "enabled 🔁" if player.loop_queue else "disabled"
        await interaction.followup.send(
            embed=success_embed(
                f"🔁 Queue Loop {state.title()}",
                f"Songs will {'be re-added to the queue after playing' if player.loop_queue else 'not repeat'}.",
            )
        )

    # -----------------------------------------------------------------------
    # /rescan
    # -----------------------------------------------------------------------

    @app_commands.command(name="rescan", description="Rescan the music library for new or removed files.")
    async def rescan(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        # Send a "working on it" message first since scanning can be slow
        await interaction.followup.send(
            embed=make_embed(
                title="🔄 Rescanning Library…",
                description="Please wait while I scan the music folder.",
            )
        )

        count = self.library.rescan()

        # Edit the previous message with the result
        embed = success_embed(
            "✅ Rescan Complete",
            f"Found **{count}** songs in the music library.",
        )
        await interaction.edit_original_response(embed=embed)

    # -----------------------------------------------------------------------
    # /help
    # -----------------------------------------------------------------------

    @app_commands.command(name="help", description="Show all available iSai commands.")
    async def help(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        commands_info = [
            ("▶️ /play `<song>`", "Search and play a song (fuzzy search)"),
            ("🔍 /search `<query>`", "Show top 10 matching songs"),
            ("🎲 /random", "Play a random song"),
            ("📋 /queue", "Show the current queue"),
            ("⏭️ /skip", "Skip the current song"),
            ("⏸️ /pause", "Pause playback"),
            ("▶️ /resume", "Resume paused playback"),
            ("⏹️ /stop", "Stop playback and clear queue"),
            ("👋 /disconnect", "Disconnect from voice channel"),
            ("🎵 /nowplaying", "Show current song details"),
            ("🔀 /shuffle", "Shuffle the queue"),
            ("🔂 /loop", "Toggle single-song loop"),
            ("🔁 /loopqueue", "Toggle full-queue loop"),
            ("🔄 /rescan", "Rescan the music library"),
            ("🎶 /playlist", "View all available songs in the library"),
            ("🔊 /volume", "Control the playback volume"),
            ("❓ /help", "Show this message"),
        ]

        lines = [f"**{cmd}** — {desc}" for cmd, desc in commands_info]

        embed = make_embed(
            title="❓ iSai Help — All Commands",
            description="\n".join(lines),
        )
        embed.add_field(
            name="📁 Library Stats",
            value=f"**{self.library.count}** songs indexed",
            inline=False,
        )
        await interaction.followup.send(embed=embed)

    # -----------------------------------------------------------------------
    # /playlist
    # -----------------------------------------------------------------------

    @app_commands.command(name="playlist", description="View all available songs in the library.")
    async def playlist(self, interaction: discord.Interaction) -> None:
        """View the available songs with pagination."""
        songs = self.library._songs
        if not songs:
            await interaction.response.send_message(
                embed=error_embed("No songs available in the library.")
            )
            return
            
        view = PlaylistPagination(songs)
        await interaction.response.send_message(embed=view.format_page(), view=view)

    # -----------------------------------------------------------------------
    # /volume
    # -----------------------------------------------------------------------

    @app_commands.command(name="volume", description="Set the playback volume.")
    @app_commands.describe(level="Volume level from 1 to 100")
    async def volume(self, interaction: discord.Interaction, level: int) -> None:
        """Control the volume of the bot in VC."""
        player: GuildPlayer = self.manager.get(interaction.guild_id)
        
        if level < 1 or level > 100:
            await interaction.response.send_message(
                embed=error_embed("Volume must be between 1 and 100.")
            )
            return
            
        player.set_volume(level / 100.0)
        
        embed = discord.Embed(
            title="🔊 Volume Changed",
            description=f"Volume set to **{level}%**.",
            color=EMBED_COLOR
        )
        await interaction.response.send_message(embed=embed)

    # -----------------------------------------------------------------------
    # /get
    # -----------------------------------------------------------------------

    @app_commands.command(name="get", description="Search for and play any song from the internet.")
    @app_commands.describe(song_name="Song name to search for (e.g. on YouTube).")
    async def get_song(self, interaction: discord.Interaction, song_name: str) -> None:
        """Search the internet for a song and play it."""
        await interaction.response.defer()

        # --- Voice channel check ---
        vc = _get_voice_channel(interaction)
        if vc is None:
            await interaction.followup.send(
                embed=error_embed("You must be in a voice channel to use this command.")
            )
            return

        # --- Search via yt-dlp ---
        info = await search_song(song_name)
        if not info or "url" not in info:
            await interaction.followup.send(
                embed=error_embed(f"Could not find any internet streams for **{song_name}**.")
            )
            return

        # --- Connect / move to voice channel ---
        player: GuildPlayer = self.manager.get(interaction.guild_id)
        try:
            await player.connect(vc)
        except Exception as exc:
            await interaction.followup.send(
                embed=error_embed(f"Could not connect to voice channel: {exc}")
            )
            return

        # --- Create Song instance ---
        song = Song(
            path=info["url"],
            title=info.get("title", song_name),
            artist=info.get("uploader", "Unknown Artist"),
            album="Internet Stream",
            duration=info.get("duration"),
            search_key="",
            is_stream=True,
            stream_headers=info.get("http_headers")
        )

        # --- Enqueue & start ---
        if player.voice_client.is_playing() or player.voice_client.is_paused():
            position = player.enqueue(song)
            embed = make_embed(
                title="➕ Added to Queue (Internet)",
                description=f"**{song.title}**",
                fields=[
                    ("Artist", song.artist, True),
                    ("Duration", format_duration(song.duration), True),
                    ("Position in Queue", str(position), True),
                ],
            )
        else:
            player.enqueue(song)
            await player.start(interaction.channel)
            embed = make_embed(
                title="▶️ Now Playing (Internet)",
                description=f"**{song.title}**",
                fields=[
                    ("Artist", song.artist, True),
                    ("Duration", format_duration(song.duration), True),
                ],
            )

        await interaction.followup.send(embed=embed)
