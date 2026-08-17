"""
ipc.py — iSai Bot IPC Server
==============================
Provides a local HTTP API to control the bot instance from the manager.
"""

import logging
from aiohttp import web
import discord
from bot.config import IPC_PORT

log = logging.getLogger("iSai.IPC")

class IPCServer:
    def __init__(self, bot: discord.ext.commands.Bot):
        self.bot = bot
        self.app = web.Application()
        self.app.add_routes([
            web.post('/connect', self.handle_connect),
            web.post('/disconnect', self.handle_disconnect),
            web.post('/play', self.handle_play),
            web.post('/stop', self.handle_stop),
            web.get('/status', self.handle_status),
        ])
        self.runner = None

    async def start(self):
        if not IPC_PORT:
            return
            
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', IPC_PORT)
        await site.start()
        log.info(f"IPC Server running on http://127.0.0.1:{IPC_PORT}")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    async def handle_connect(self, request: web.Request):
        data = await request.json()
        vc_id = data.get('vc_id')
        if not vc_id:
            return web.json_response({'error': 'Missing vc_id'}, status=400)
            
        channel = self.bot.get_channel(int(vc_id))
        if not isinstance(channel, discord.VoiceChannel):
            return web.json_response({'error': 'Invalid Voice Channel ID'}, status=400)
            
        player = self.bot.player_manager.get(channel.guild.id)
        try:
            await player.connect(channel)
            return web.json_response({'status': 'connected', 'vc_id': str(channel.id)})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)

    async def handle_disconnect(self, request: web.Request):
        data = await request.json()
        guild_id = data.get('guild_id')
        if not guild_id:
            return web.json_response({'error': 'Missing guild_id'}, status=400)
            
        player = self.bot.player_manager.get(int(guild_id))
        if player.voice_client and player.voice_client.is_connected():
            await player.disconnect()
            return web.json_response({'status': 'disconnected'})
        return web.json_response({'status': 'not connected'})

    async def handle_play(self, request: web.Request):
        data = await request.json()
        guild_id = data.get('guild_id')
        song_query = data.get('song')
        
        if not guild_id or not song_query:
            return web.json_response({'error': 'Missing guild_id or song'}, status=400)
            
        player = self.bot.player_manager.get(int(guild_id))
        if not player.voice_client or not player.voice_client.is_connected():
            return web.json_response({'error': 'Not connected to a voice channel in this guild'}, status=400)
            
        results = self.bot.library.search(song_query)
        if not results:
            return web.json_response({'error': 'Song not found'}, status=404)
            
        best_song, _ = results[0]
        
        if player.voice_client.is_playing() or player.voice_client.is_paused():
            position = player.enqueue(best_song)
            return web.json_response({'status': 'enqueued', 'song': best_song.title, 'position': position})
        else:
            player.enqueue(best_song)
            # Find a text channel to send now playing (optional, maybe none)
            text_channel = None
            for channel in player.voice_client.guild.text_channels:
                if channel.permissions_for(player.voice_client.guild.me).send_messages:
                    text_channel = channel
                    break
            
            await player.start(text_channel)
            return web.json_response({'status': 'playing', 'song': best_song.title})

    async def handle_stop(self, request: web.Request):
        data = await request.json()
        guild_id = data.get('guild_id')
        if not guild_id:
            return web.json_response({'error': 'Missing guild_id'}, status=400)
            
        player = self.bot.player_manager.get(int(guild_id))
        await player.stop()
        return web.json_response({'status': 'stopped'})

    async def handle_status(self, request: web.Request):
        # Return status for all active players
        status_data = {}
        for guild_id, player in self.bot.player_manager._players.items():
            if player.voice_client and player.voice_client.is_connected():
                status_data[str(guild_id)] = {
                    'vc_id': str(player.voice_client.channel.id),
                    'playing': player.voice_client.is_playing(),
                    'paused': player.voice_client.is_paused(),
                    'current': player.current.title if player.current else None,
                    'queue_length': len(player.queue)
                }
        return web.json_response({'status': 'ok', 'players': status_data})
