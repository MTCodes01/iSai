"""
bot.py — iSai Bot Entry Point
================================
Initialises and runs the iSai Discord music bot.

Responsibilities:
  1. Create the discord.py Bot with the required intents.
  2. Scan the music library on startup.
  3. Register the MusicCog containing all slash commands.
  4. Sync slash commands with Discord's API.
  5. Handle top-level errors gracefully.

Run with:
    python -m bot.bot
or simply:
    python bot/bot.py
"""

import asyncio
import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import TOKEN
from bot.music_library import MusicLibrary
from bot.player import PlayerManager
from bot.commands import MusicCog
from bot.utils import setup_logging
from bot.ipc import IPCServer

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = setup_logging()


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

# discord.py 2.x requires explicit intents.
# We only need a subset — voice state access and guild membership.
intents = discord.Intents.default()
intents.voice_states = True     # Needed to detect which VC the user is in
intents.guilds = True           # Needed for guild/channel access
intents.message_content = False # We use slash commands; no message reading needed


class IsSaiBot(commands.Bot):
    """
    Subclass of commands.Bot so we can override setup_hook for
    async initialisation tasks (library scan, command sync).
    """

    def __init__(self) -> None:
        super().__init__(
            command_prefix="!",   # Prefix commands are unused but required by the API
            intents=intents,
            help_command=None,    # We have a custom /help slash command
        )
        self.library: MusicLibrary = MusicLibrary()
        self.player_manager: PlayerManager = PlayerManager()
        self.ipc_server = IPCServer(self)

    async def setup_hook(self) -> None:
        """
        Called once by discord.py after login, before the bot connects.

        We use this hook to:
          1. Scan the music library (runs in a thread pool to avoid blocking).
          2. Register the MusicCog and sync slash commands with Discord.
          3. Start the IPC Server if configured.
        """
        log.info("Running setup_hook…")

        # Scan library in an executor to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        count = await loop.run_in_executor(None, self.library.scan)
        log.info("Music library ready: %d songs indexed.", count)

        # Register the Cog containing all slash commands
        await self.add_cog(MusicCog(self, self.library, self.player_manager))
        log.info("MusicCog registered.")

        from bot.config import IS_MASTER
        if IS_MASTER:
            # Sync slash commands with Discord
            # Using None syncs globally (takes up to 1 hour to propagate).
            synced = await self.tree.sync()
            log.info("Master bot synced %d slash command(s) with Discord.", len(synced))
        else:
            # Clear commands so worker bots don't show up in the menu
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            log.info("Worker bot cleared slash commands.")
        
        # Start IPC Server
        await self.ipc_server.start()

    async def close(self) -> None:
        await self.ipc_server.stop()
        await super().close()

    async def on_ready(self) -> None:
        """Called when the bot has fully connected and is ready to receive events."""
        assert self.user is not None
        log.info("━" * 50)
        log.info("iSai Bot is online!")
        log.info("Logged in as: %s (ID: %d)", self.user.name, self.user.id)
        log.info("Guilds: %d", len(self.guilds))
        log.info("Music library: %d songs", self.library.count)
        log.info("━" * 50)

        # Set a rich presence status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name=f"{self.library.count} songs  •  /play",
            )
        )

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """
        Automatically disconnect when the bot is left alone in a voice channel.

        This prevents the bot from staying in an empty channel indefinitely.
        """
        if member.id == self.user.id:
            return  # Ignore the bot's own state changes

        guild = member.guild
        player = self.player_manager.get(guild.id)

        if not player.voice_client or not player.voice_client.is_connected():
            return

        # Check if the channel the bot is in is now empty (excluding the bot itself)
        bot_channel = player.voice_client.channel
        human_members = [m for m in bot_channel.members if not m.bot]

        if not human_members:
            log.info(
                "[Guild %d] All humans left the voice channel. Disconnecting.", guild.id
            )
            await player.disconnect()

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Global handler for unhandled slash command errors."""
        from bot.commands import WrongVoiceChannelError
        
        if isinstance(error, WrongVoiceChannelError):
            msg = str(error)
        else:
            log.error("Unhandled app command error: %s", error, exc_info=True)
            msg = "An unexpected error occurred. Please try again."

        # Try to respond if we haven't already
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=discord.Embed(title="❌ Error", description=msg, color=discord.Color.red()),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    embed=discord.Embed(title="❌ Error", description=msg, color=discord.Color.red()),
                    ephemeral=True,
                )
        except Exception:
            pass  # If we can't reply, silently move on


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Validate configuration and start the bot."""
    if not TOKEN:
        log.critical(
            "DISCORD_TOKEN is not set. "
            "Create a .env file with DISCORD_TOKEN=<your token> and try again."
        )
        sys.exit(1)

    bot = IsSaiBot()

    try:
        log.info("Starting iSai Bot…")
        bot.run(TOKEN, log_handler=None)  # log_handler=None → we manage logging ourselves
    except discord.LoginFailure:
        log.critical("Invalid Discord token. Check your .env file.")
        sys.exit(1)
    except KeyboardInterrupt:
        log.info("Shutdown requested. Goodbye!")


if __name__ == "__main__":
    main()
