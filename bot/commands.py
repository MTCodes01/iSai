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
        data, status = await _send_to_manager("skip", {"guild_id": interaction.guild_id})
        
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
        data, status = await _send_to_manager("pause", {"guild_id": interaction.guild_id})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Failed to pause.')))
            return
            
        await interaction.followup.send(embed=success_embed("⏸️ Paused", "Use `/resume` to continue."))

    @app_commands.command(name="resume", description="Resume paused playback.")
    async def resume(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        data, status = await _send_to_manager("resume", {"guild_id": interaction.guild_id})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Failed to resume.')))
            return
            
        await interaction.followup.send(embed=success_embed("▶️ Resumed"))

    @app_commands.command(name="stop", description="Stop playback and clear the queue.")
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        data, status = await _send_to_manager("stop", {"guild_id": interaction.guild_id})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Failed to stop.')))
            return
            
        await interaction.followup.send(embed=success_embed("⏹️ Stopped", "Queue cleared."))

    @app_commands.command(name="disconnect", description="Disconnect the bot from the voice channel.")
    async def disconnect(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        data, status = await _send_to_manager("disconnect", {"guild_id": interaction.guild_id})
        
        if status != 200:
            await interaction.followup.send(embed=error_embed(data.get('error', 'Failed to disconnect.')))
            return
            
        await interaction.followup.send(embed=success_embed("👋 Disconnected", "Goodbye!"))

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
            ("▶️ /play `<song>`", "Search and play a song"),
            ("🔍 /search `<query>`", "Show top 10 matching songs"),
            ("🎲 /random", "Play a random song"),
            ("⏭️ /skip", "Skip the current song"),
            ("⏸️ /pause", "Pause playback"),
            ("▶️ /resume", "Resume paused playback"),
            ("⏹️ /stop", "Stop playback and clear queue"),
            ("👋 /disconnect", "Disconnect from voice channel"),
            ("🎶 /playlist", "View all available songs in the library"),
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
