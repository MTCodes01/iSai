"""
manager.py — iSai Bot Manager
===============================
Manages multiple instances of the iSai Discord bot.
Reads configuration for multiple bots from .env, launches them
as subprocesses, assigns them unique IPC ports, and restarts them
if they crash.

Usage:
    python manager.py
"""

import os
import sys
import time
import socket
import logging
import argparse
import subprocess
import threading
import urllib.request
import urllib.error
import json
from typing import Dict, Optional
from dotenv import load_dotenv

# Setup manager logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | MANAGER  — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Manager")


class BotInstance:
    def __init__(self, bot_id: str, token: str, assigned_vc: Optional[str] = None):
        self.bot_id = bot_id
        self.token = token
        self.assigned_vc = assigned_vc
        self.ipc_port = self._get_free_port()
        self.process: Optional[subprocess.Popen] = None
        self.stopping = False

    def _get_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def start(self):
        self.stopping = False
        env = os.environ.copy()
        env["DISCORD_TOKEN"] = self.token
        env["BOT_INSTANCE_ID"] = self.bot_id
        env["IPC_PORT"] = str(self.ipc_port)
        if self.assigned_vc:
            env["ASSIGNED_VC_ID"] = self.assigned_vc
            
        log.info("Starting bot %s (IPC Port: %d)", self.bot_id, self.ipc_port)
        # Using sys.executable to ensure we use the same Python interpreter
        self.process = subprocess.Popen(
            [sys.executable, "-m", "bot.bot"],
            env=env
        )

    def stop(self):
        self.stopping = True
        if self.process:
            log.info("Stopping bot %s...", self.bot_id)
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.warning("Bot %s did not terminate cleanly. Killing.", self.bot_id)
                self.process.kill()
            self.process = None

    def poll(self):
        if self.process:
            return self.process.poll()
        return None

def monitor_instances(instances: list[BotInstance]):
    log.info("Starting instance monitor...")
    try:
        while True:
            for instance in instances:
                if instance.stopping:
                    continue
                    
                status = instance.poll()
                if status is not None:
                    log.error("Bot %s crashed with exit code %d. Restarting...", instance.bot_id, status)
                    time.sleep(2)  # Prevent tight restart loop
                    instance.start()
            time.sleep(2)
    except KeyboardInterrupt:
        log.info("Received shutdown signal. Stopping all bots...")
        for instance in instances:
            instance.stop()

def send_ipc_command(port: int, endpoint: str, data: Optional[dict] = None):
    url = f"http://127.0.0.1:{port}/{endpoint}"
    req = urllib.request.Request(url, method="POST" if data or endpoint != "status" else "GET")
    if data:
        req.add_header('Content-Type', 'application/json')
        req.data = json.dumps(data).encode('utf-8')
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except urllib.error.URLError as e:
        log.error("Failed to connect to IPC server on port %d: %s", port, e)
        return None

def main():
    parser = argparse.ArgumentParser(description="iSai Bot Manager")
    parser.add_argument("--send", help="Send an IPC command to a specific port. Format: <port>:<endpoint>", type=str)
    parser.add_argument("--data", help="JSON data for IPC command", type=str)
    args = parser.parse_args()

    if args.send:
        try:
            port_str, endpoint = args.send.split(":", 1)
            port = int(port_str)
        except ValueError:
            print("Invalid format for --send. Use <port>:<endpoint>")
            sys.exit(1)
            
        data = None
        if args.data:
            try:
                data = json.loads(args.data)
            except json.JSONDecodeError:
                print("Invalid JSON data.")
                sys.exit(1)
                
        res = send_ipc_command(port, endpoint, data)
        print(json.dumps(res, indent=2))
        sys.exit(0)

    load_dotenv()
    
    instances: list[BotInstance] = []
    
    # Discover configured bots
    for key, value in os.environ.items():
        if key.startswith("BOT_TOKEN_") and value:
            suffix = key[len("BOT_TOKEN_"):]
            assigned_vc = os.environ.get(f"ASSIGNED_VC_{suffix}")
            bot_id = f"BOT-{suffix}"
            instances.append(BotInstance(bot_id, value, assigned_vc))

    if not instances:
        log.critical("No bot tokens found. Set BOT_TOKEN_1, BOT_TOKEN_2, etc. in .env")
        sys.exit(1)

    log.info("Found %d bot configurations.", len(instances))

    for instance in instances:
        instance.start()

    # Run monitor in the main thread (blocks until Ctrl+C)
    monitor_instances(instances)

if __name__ == "__main__":
    main()
