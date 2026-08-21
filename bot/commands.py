"""
commands.py — iSai Bot Slash Commands
=======================================
Defines all user-facing slash commands as a discord.py Cog.

In the Master-Worker architecture, the Master bot registers these commands.
It processes local queries (search, playlist) and proxies playback commands
(play, skip, pause) to the Central Manager API.
"""

import logging
from typing import Optional
import aiohttp

import discord
from discord import app_commands
from discord.ext import commands

from bot.music_library import MusicLibrary, Song
from bot.player import PlayerManager
from bot.config import TOP_SEARCH_RESULTS, MANAGER_PORT
from bot.utils import (
    make_embed,
    error_embed,
    success_embed,
    format_duration,
    EMBED_COLOR,
)

log = logging.getLogger("iSai.commands")


def _get_voice_channel(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    """Return the voice channel the interaction author is currently in."""
    member = interaction.user
    if isinstance(member, discord.Member) and member.voice and member.voice.channel:
        channel = member.voice.channel
        if isinstance(channel, discord.VoiceChannel):
            return channel
    return None


async def _send_to_manager(command: str, payload: dict) -> dict:
    url = f"http://127.0.0.1:{MANAGER_PORT}/api/command"
    payload['command'] = command
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                return await resp.json(), resp.status
    except Exception as e:
        return {'error': str(e)}, 500


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


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot, library: MusicLibrary, manager: PlayerManager) -> None:
        self.bot = bot
        self.library = library
        self.manager = manager

    @app_commands.command(name="play", description="Search and play a song from the local library.")
    @app_commands.describe(song="Song name, artist, or any search term.")
    async def play(self, interaction: discord.Interaction, song: str) -> None:
        await interaction.response.defer()

        vc = _get_voice_channel(interaction)
        if vc is None:
            await interaction.followup.send(embed=error_embed("You must be in a voice channel to use this command."))
            return

        payload = {
            "guild_id": interaction.guild_id,
            "vc_id": vc.id,
            "song": song
        }
        
        data, status = await _send_to_manager("play", payload)
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Unknown error occurred')))
            return
            
        bot_assigned = data.get('assigned_bot', 'Unknown Bot')
        
        if data.get('status') == 'enqueued':
            embed = make_embed(
                title="➕ Added to Queue",
                description=f"**{data.get('song')}**",
                fields=[
                    ("Artist", data.get('artist', 'Unknown'), True),
                    ("Position in Queue", str(data.get('position', '?')), True),
                ],
            )
        else:
            embed = make_embed(
                title="▶️ Now Playing",
                description=f"**{data.get('song')}**",
                fields=[
                    ("Artist", data.get('artist', 'Unknown'), True),
                ],
            )
            
        embed.set_footer(text=f"Handled by {bot_assigned}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="random", description="Play a random song from the library.")
    async def random(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        vc = _get_voice_channel(interaction)
        if vc is None:
            await interaction.followup.send(embed=error_embed("You must be in a voice channel to use this command."))
            return

        # Passing "random" as query, since the IPC falls back to random if not found
        payload = {
            "guild_id": interaction.guild_id,
            "vc_id": vc.id,
            "song": "random_fallback_query_12345" 
        }
        
        data, status = await _send_to_manager("play", payload)
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Unknown error occurred')))
            return
            
        bot_assigned = data.get('assigned_bot', 'Unknown Bot')
        
        embed = make_embed(
            title="🎲 Random Song",
            description=f"**{data.get('song')}**",
            fields=[
                ("Artist", data.get('artist', 'Unknown'), True),
            ],
        )
        embed.set_footer(text=f"Handled by {bot_assigned}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="skip", description="Skip the current song.")
    async def skip(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("skip", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Nothing is playing.')))
            return
            
        embed = success_embed(
            title="⏭️ Skipped",
            description=f"**{data.get('song')}** — *{data.get('artist')}*" if data.get('song') else "Track skipped.",
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="pause", description="Pause playback.")
    async def pause(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("pause", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Failed to pause.')))
            return
            
        await interaction.followup.send(embed=success_embed("⏸️ Paused", "Use `/resume` to continue."))

    @app_commands.command(name="resume", description="Resume paused playback.")
    async def resume(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("resume", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Failed to resume.')))
            return
            
        await interaction.followup.send(embed=success_embed("▶️ Resumed"))

    @app_commands.command(name="stop", description="Stop playback and clear the queue.")
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("stop", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Failed to stop.')))
            return
            
        await interaction.followup.send(embed=success_embed("⏹️ Stopped", "Queue cleared."))

    @app_commands.command(name="disconnect", description="Disconnect the bot from the voice channel.")
    async def disconnect(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("disconnect", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Failed to disconnect.')))
            return
            
        await interaction.followup.send(embed=success_embed("👋 Disconnected", "Goodbye!"))

    @app_commands.command(name="queue", description="Show the current queue.")
    async def queue(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("queue", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'The queue is empty.')))
            return
            
        lines = []
        if data.get('current'):
            curr = data['current']
            status_icon = "🔂" if data.get('loop_song') else "▶️"
            lines.append(f"{status_icon} **Now Playing:** {curr.get('title')} — *{curr.get('artist')}* `[{format_duration(curr.get('duration'))}]`")
            
        if data.get('queue'):
            lines.append("")
            lines.append("**Up Next:**")
            for i, q in enumerate(data['queue'], start=1):
                lines.append(f"`{i}.` **{q.get('title')}** — *{q.get('artist')}* `[{format_duration(q.get('duration'))}]`")
            if data.get('queue_length', 0) > 20:
                lines.append(f"*… and {data['queue_length'] - 20} more songs*")
                
        footer_parts = []
        if data.get('loop_song'): footer_parts.append("🔂 Looping current song")
        if data.get('loop_queue'): footer_parts.append("🔁 Looping queue")
        if data.get('autoplay'): footer_parts.append("📻 Autoplay enabled")
        
        embed = make_embed(
            title=f"📋 Queue — {data.get('queue_length', 0)} song(s) pending",
            description="\n".join(lines),
        )
        if footer_parts:
            embed.set_footer(text="  •  ".join(footer_parts))
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="nowplaying", description="Show information about the current song.")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("nowplaying", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None})
        
        if status != 200:
            await interaction.followup.send(embed=make_embed(title="🎵 Now Playing", description="Nothing is playing right now. Use `/play` to start!"))
            return
            
        fields = [
            ("Artist", data.get('artist', 'Unknown'), True),
            ("Duration", format_duration(data.get('duration', 0)), True),
        ]
        if data.get('album') and data.get('album') != 'Unknown Album':
            fields.append(("Album", data.get('album'), True))
            
        loop_state = []
        if data.get('loop_song'): loop_state.append("🔂 Song Loop")
        if data.get('loop_queue'): loop_state.append("🔁 Queue Loop")
        if data.get('autoplay'): loop_state.append("📻 Autoplay")
        if loop_state: fields.append(("Features", "  •  ".join(loop_state), False))
        
        vc_status = "▶️ Playing" if data.get('is_playing') else "⏸️ Paused"
        fields.append(("Status", vc_status, True))
        fields.append(("Queue", f"{data.get('queue_length', 0)} song(s) pending", True))
        
        embed = make_embed(
            title="🎵 Now Playing",
            description=f"**{data.get('song')}**",
            fields=fields,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="shuffle", description="Shuffle the current queue.")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("shuffle", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'The queue is empty.')))
            return
            
        await interaction.followup.send(embed=success_embed("🔀 Queue Shuffled", f"{data.get('count')} songs reordered."))

    @app_commands.command(name="loop", description="Toggle looping of the current song.")
    async def loop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("loop", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Failed to toggle loop.')))
            return
            
        state = "enabled 🔂" if data.get('enabled') else "disabled"
        await interaction.followup.send(embed=success_embed(f"🔂 Song Loop {state.title()}", f"The current song will {'repeat indefinitely' if data.get('enabled') else 'not repeat'}."))

    @app_commands.command(name="loopqueue", description="Toggle looping of the entire queue.")
    async def loopqueue(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("loopqueue", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Failed to toggle loop.')))
            return
            
        state = "enabled 🔁" if data.get('enabled') else "disabled"
        await interaction.followup.send(embed=success_embed(f"🔁 Queue Loop {state.title()}", f"Songs will {'be re-added to the queue after playing' if data.get('enabled') else 'not repeat'}."))

    @app_commands.command(name="autoplay", description="Toggle autoplay to automatically play similar songs when the queue ends.")
    async def autoplay(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("autoplay", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Failed to toggle autoplay.')))
            return
            
        state = "enabled 📻" if data.get('enabled') else "disabled"
        await interaction.followup.send(embed=success_embed(f"📻 Autoplay {state.title()}", f"The bot will {'now play similar songs when the queue ends' if data.get('enabled') else 'stop playing when the queue ends'}."))

    @app_commands.command(name="volume", description="Set the playback volume.")
    @app_commands.describe(level="Volume level from 1 to 100")
    async def volume(self, interaction: discord.Interaction, level: int) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        data, status = await _send_to_manager("volume", {"guild_id": interaction.guild_id, "vc_id": vc.id if vc else None, "level": level})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Volume must be between 1 and 100.')))
            return
            
        await interaction.followup.send(embed=success_embed("🔊 Volume Changed", f"Volume set to **{level}%**."))

    @app_commands.command(name="get", description="Search for and play any song from the internet.")
    @app_commands.describe(song_name="Song name to search for (e.g. on YouTube).")
    async def get_song(self, interaction: discord.Interaction, song_name: str) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        if vc is None:
            await interaction.followup.send(embed=error_embed("You must be in a voice channel to use this command."))
            return

        payload = {
            "guild_id": interaction.guild_id,
            "vc_id": vc.id,
            "song": song_name
        }
        
        data, status = await _send_to_manager("get", payload)
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Unknown error occurred')))
            return
            
        bot_assigned = data.get('assigned_bot', 'Unknown Bot')
        
        if data.get('status') == 'enqueued':
            embed = make_embed(
                title="➕ Added to Queue (Internet)",
                description=f"**{data.get('song')}**",
                fields=[
                    ("Artist", data.get('artist', 'Unknown'), True),
                    ("Position in Queue", str(data.get('position', '?')), True),
                ],
            )
        else:
            embed = make_embed(
                title="▶️ Now Playing (Internet)",
                description=f"**{data.get('song')}**",
                fields=[
                    ("Artist", data.get('artist', 'Unknown'), True),
                ],
            )
            
        embed.set_footer(text=f"Handled by {bot_assigned}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="getplaylist", description="Add all songs from a YouTube playlist to the queue.")
    @app_commands.describe(playlist_url="YouTube playlist link.")
    async def getplaylist(self, interaction: discord.Interaction, playlist_url: str) -> None:
        await interaction.response.defer()
        vc = _get_voice_channel(interaction)
        if vc is None:
            await interaction.followup.send(embed=error_embed("You must be in a voice channel to use this command."))
            return

        payload = {
            "guild_id": interaction.guild_id,
            "vc_id": vc.id,
            "playlist_url": playlist_url
        }
        
        data, status = await _send_to_manager("getplaylist", payload)
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Unknown error occurred')))
            return
            
        bot_assigned = data.get('assigned_bot', 'Unknown Bot')
        
        if data.get('status') == 'enqueued_playlist':
            embed = make_embed(
                title="➕ Added Playlist to Queue",
                description=f"Added **{data.get('count')}** songs.",
                fields=[
                    ("First Song", data.get('first_song', 'Unknown'), True),
                ],
            )
        else:
            embed = make_embed(
                title="▶️ Playing Playlist",
                description=f"Added **{data.get('count')}** songs.",
                fields=[
                    ("Now Playing", data.get('first_song', 'Unknown'), True),
                ],
            )
            
        embed.set_footer(text=f"Handled by {bot_assigned}")
        await interaction.followup.send(embed=embed)

    # Local querying commands that do not need routing
    @app_commands.command(name="search", description="Search the library and show the top matches.")
    @app_commands.describe(query="Search term (song title, artist, or both).")
    async def search(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer()

        results = self.library.search(query, limit=TOP_SEARCH_RESULTS)
        if not results:
            await interaction.followup.send(embed=error_embed(f"No results found for **{query}**."))
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

    @app_commands.command(name="playlist", description="View all available songs in the library.")
    async def playlist(self, interaction: discord.Interaction) -> None:
        songs = self.library._songs
        if not songs:
            await interaction.response.send_message(embed=error_embed("No songs available in the library."))
            return
            
        view = PlaylistPagination(songs)
        await interaction.response.send_message(embed=view.format_page(), view=view)

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
            ("📻 /autoplay", "Toggle playing similar songs when the queue ends"),
            ("🎶 /playlist", "View all available songs in the library"),
            ("🔊 /volume", "Control the playback volume"),
            ("🌐 /get `<song>`", "Search and play any song from the internet"),
            ("🎶 /getplaylist `<url>`", "Add all songs from a YouTube playlist to the queue"),
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
