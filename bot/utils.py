"""
utils.py — iSai Bot Utility Functions
=======================================
Shared helper functions used across the bot modules.

Responsibilities:
  - Formatting durations (seconds → mm:ss / h:mm:ss)
  - Building Discord embeds with a consistent colour and footer
  - Normalising strings for fuzzy search (strip punctuation, lower-case)
  - Logging setup
"""

import re
import logging
import string
from typing import Optional

import discord


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """
    Configure and return the root logger for the iSai bot.

    Sets up a StreamHandler with a readable format including timestamps,
    module name, and log level.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("iSai")


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------

def format_duration(seconds: Optional[float]) -> str:
    """
    Convert a duration in seconds to a human-readable string.

    Examples:
        format_duration(65)    → '1:05'
        format_duration(3661)  → '1:01:01'
        format_duration(None)  → 'Unknown'
    """
    if seconds is None:
        return "Unknown"

    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# ---------------------------------------------------------------------------
# String normalisation (for fuzzy search)
# ---------------------------------------------------------------------------

# Pre-compile the punctuation-stripping regex for performance.
_PUNCTUATION_RE = re.compile(f"[{re.escape(string.punctuation)}]")


def normalise(text: str) -> str:
    """
    Normalise a string for fuzzy comparison:
      1. Convert to lower-case.
      2. Remove all punctuation characters.
      3. Collapse multiple whitespace characters into a single space.
      4. Strip leading/trailing whitespace.

    This ensures that 'Don't Stop Me Now' and 'dont stop me now'
    will compare as identical when running similarity checks.
    """
    text = text.lower()
    text = _PUNCTUATION_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Discord embed builder
# ---------------------------------------------------------------------------

EMBED_COLOR = discord.Color.from_rgb(88, 101, 242)  # "Blurple" variant
BOT_NAME = "iSai"
BOT_ICON = "🎵"


def make_embed(
    title: str,
    description: str = "",
    color: discord.Color = EMBED_COLOR,
    fields: Optional[list[tuple[str, str, bool]]] = None,
) -> discord.Embed:
    """
    Create a stylised Discord embed with a consistent look.

    Parameters
    ----------
    title : str
        The embed title (shown in bold at the top).
    description : str
        Optional body text below the title.
    color : discord.Color
        Left-bar accent colour (defaults to iSai blurple).
    fields : list of (name, value, inline) tuples
        Optional fields to add to the embed.

    Returns
    -------
    discord.Embed
        A fully configured embed ready to send.
    """
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=f"{BOT_ICON} iSai Music Bot")

    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)

    return embed


def error_embed(message: str) -> discord.Embed:
    """Return a red-tinted error embed."""
    return make_embed(
        title="❌ Error",
        description=message,
        color=discord.Color.from_rgb(237, 66, 69),
    )


def success_embed(title: str, description: str = "") -> discord.Embed:
    """Return a green-tinted success embed."""
    return make_embed(
        title=title,
        description=description,
        color=discord.Color.from_rgb(87, 242, 135),
    )
