"""
manager.py — iSai Bot Manager (Master-Worker Router)
======================================================
Manages multiple instances of the iSai Discord bot and routes
commands from the Master bot to free Worker bots.

Usage:
    python manager.py
"""

import os
import sys
import socket
import logging
import argparse
import asyncio
import aiohttp
from aiohttp import web
from typing import Dict, Optional, List
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | MANAGER  — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Manager")


class BotInstance:
    def __init__(self, bot_id: str, token: str, is_master: bool, assigned_vc: Optional[str] = None):
        self.bot_id = bot_id
        self.token = token
        self.is_master = is_master
        self.assigned_vc = assigned_vc
        self.ipc_port = self._get_free_port()
        self.process: Optional[asyncio.subprocess.Process] = None
        self.stopping = False

    def _get_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    async def start(self):
        self.stopping = False
        env = os.environ.copy()
        env["DISCORD_TOKEN"] = self.token
        env["BOT_INSTANCE_ID"] = self.bot_id
        env["IPC_PORT"] = str(self.ipc_port)
        env["IS_MASTER"] = "true" if self.is_master else "false"
        if self.assigned_vc:
            env["ASSIGNED_VC_ID"] = self.assigned_vc
            
        log.info("Starting bot %s (Master: %s, IPC Port: %d)", self.bot_id, self.is_master, self.ipc_port)
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "bot.bot",
            env=env
        )

    async def stop(self):
        self.stopping = True
        if self.process:
            log.info("Stopping bot %s...", self.bot_id)
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                log.warning("Bot %s did not terminate cleanly. Killing.", self.bot_id)
                self.process.kill()
            self.process = None


class ManagerRouter:
    def __init__(self, instances: List[BotInstance], port: int):
        self.instances = instances
        self.port = port
        self.app = web.Application()
        self.app.add_routes([
            web.post('/api/command', self.handle_command)
        ])
        
    async def get_all_statuses(self) -> Dict[str, dict]:
        statuses = {}
        async with aiohttp.ClientSession() as session:
            for inst in self.instances:
                if not inst.process:
                    continue
                try:
                    async with session.get(f"http://127.0.0.1:{inst.ipc_port}/status", timeout=2) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            statuses[inst.bot_id] = data.get('players', {})
                except Exception as e:
                    log.debug("Failed to get status from %s: %s", inst.bot_id, e)
        return statuses

    async def find_bot_for_vc(self, guild_id: str, vc_id: str, statuses: Dict[str, dict]) -> Optional[BotInstance]:
        """Find the bot that is currently in this specific VC."""
        for inst in self.instances:
            bot_status = statuses.get(inst.bot_id, {})
            guild_status = bot_status.get(str(guild_id))
            if guild_status and guild_status.get('vc_id') == str(vc_id):
                return inst
        return None

    async def find_any_bot_in_guild(self, guild_id: str, statuses: Dict[str, dict]) -> Optional[BotInstance]:
        """Find any bot in this guild (used as fallback)."""
        for inst in self.instances:
            if str(guild_id) in statuses.get(inst.bot_id, {}):
                return inst
        return None

    async def find_free_bot(self, statuses: Dict[str, dict]) -> Optional[BotInstance]:
        """Find a bot that is not handling ANY guilds."""
        for inst in self.instances:
            bot_status = statuses.get(inst.bot_id, {})
            if not bot_status:  # Empty means not playing anywhere
                return inst
        return None

    async def handle_command(self, request: web.Request):
        data = await request.json()
        command = data.get('command')
        guild_id = str(data.get('guild_id'))
        vc_id = str(data.get('vc_id')) if data.get('vc_id') else None
        
        statuses = await self.get_all_statuses()
        
        target_bot = None
        if vc_id:
            target_bot = await self.find_bot_for_vc(guild_id, vc_id, statuses)
        
        if command in ('play', 'connect', 'get'):
            if not target_bot:
                target_bot = await self.find_free_bot(statuses)
                if not target_bot:
                    return web.json_response({'error': 'No free instances available. Please mention <@863835017000386630> and ask me to add more bots for music!'}, status=503)
        else:
            if not target_bot:
                # If vc_id wasn't provided or bot not in this VC, try to find any bot in the guild
                target_bot = await self.find_any_bot_in_guild(guild_id, statuses)
        
        if not target_bot:
            return web.json_response({'error': 'Nothing is playing in this server.'}, status=404)
            
        # Forward the command to the target bot
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{target_bot.ipc_port}/{command}"
                async with session.post(url, json=data, timeout=10) as resp:
                    resp_data = await resp.json()
                    resp_data['assigned_bot'] = target_bot.bot_id
                    return web.json_response(resp_data, status=resp.status)
        except Exception as e:
            return web.json_response({'error': f"Failed to forward command: {str(e)}"}, status=500)

    async def run(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', self.port)
        await site.start()
        log.info("Manager API Router running on http://127.0.0.1:%d", self.port)


async def monitor_instances(instances: List[BotInstance]):
    log.info("Starting instance monitor...")
    while True:
        for instance in instances:
            if instance.stopping:
                continue
            if instance.process and instance.process.returncode is not None:
                status = instance.process.returncode
                log.error("Bot %s crashed with exit code %d. Restarting...", instance.bot_id, status)
                await asyncio.sleep(2)
                await instance.start()
        await asyncio.sleep(2)


async def async_main():
    load_dotenv()
    
    manager_port = int(os.getenv("MANAGER_PORT", "5000"))
    instances: List[BotInstance] = []
    
    # Discover configured bots
    for key, value in os.environ.items():
        if key.startswith("BOT_TOKEN_") and value:
            suffix = key[len("BOT_TOKEN_"):]
            assigned_vc = os.environ.get(f"ASSIGNED_VC_{suffix}")
            bot_id = f"BOT-{suffix}"
            is_master = (suffix == "1")  # Bot 1 is always the master
            instances.append(BotInstance(bot_id, value, is_master, assigned_vc))

    if not instances:
        log.critical("No bot tokens found. Set BOT_TOKEN_1, BOT_TOKEN_2, etc. in .env")
        sys.exit(1)

    # Make sure at least one master exists
    if not any(i.is_master for i in instances):
        instances[0].is_master = True

    log.info("Found %d bot configurations.", len(instances))

    for instance in instances:
        await instance.start()

    # Start Router
    router = ManagerRouter(instances, manager_port)
    await router.run()

    # Run monitor
    monitor_task = asyncio.create_task(monitor_instances(instances))
    
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Shutting down bots...")
        for instance in instances:
            await instance.stop()

def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        log.info("Shutdown requested.")

if __name__ == "__main__":
    main()
