# iSai 🎵 — Local Music Discord Bot

A lightweight, clean Discord music bot that plays audio files from your **local music library** — no YouTube, no Spotify, no APIs.

Built with **discord.py 2.x** (slash commands), **FFmpeg**, **mutagen** (metadata), and **rapidfuzz** (fuzzy search).

---

## ✨ Features

| Feature | Details |
|---|---|
| 🎵 Local playback | Plays mp3, flac, wav, m4a, ogg |
| 🔍 Fuzzy search | Find songs by partial name or artist |
| 📋 Queue system | Add, shuffle, skip, loop |
| 🔂 Song loop | Repeat the current track |
| 🔁 Queue loop | Repeat the entire queue |
| 🎲 Random song | Pick a random track |
| 📊 Rich embeds | All responses use Discord embeds |
| 💾 Library cache | Fast startup after first scan |
| 🔄 Hot rescan | Add new files and refresh without restart |
| 👋 Auto-disconnect | Leaves VC when all humans leave |

---

## 📁 Project Structure

```
MusicBot/
├── bot/
│   ├── __init__.py        # Package marker
│   ├── bot.py             # Entry point — starts the bot
│   ├── config.py          # All configuration (reads from .env)
│   ├── player.py          # Audio player engine (per-guild state, FFmpeg)
│   ├── music_library.py   # Library scanner, metadata, fuzzy search
│   ├── commands.py        # All slash commands (Cog)
│   └── utils.py           # Helpers: logging, embeds, formatting
├── music/
│   ├── Artist Name/
│   │   ├── Song Title.mp3
│   │   └── Another Song.flac
│   └── ...
├── .env                   # Your secrets (NOT committed to git)
├── .env.example           # Template for .env
├── requirements.txt
└── README.md
```

---

## 🚀 Quick Start

### 1. Prerequisites

- **Python 3.12+**
- **FFmpeg** — install and ensure it's on your `PATH`:
  - Windows: [Download](https://ffmpeg.org/download.html), extract, add `bin/` to PATH
  - Or set `FFMPEG_PATH` in `.env` to the full path

### 2. Clone / Download

```bash
git clone <repo-url>
cd MusicBot
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure

```bash
copy .env.example .env
```

Edit `.env` and fill in your **Discord bot token**:

```env
DISCORD_TOKEN=your_discord_bot_token_here
```

> Get your token at [discord.com/developers/applications](https://discord.com/developers/applications)
> Under **Bot** settings, make sure **Voice States** intent is enabled.

### 5. Add Music

Drop your audio files into the `music/` folder:

```
music/
    Queen/
        Bohemian Rhapsody.mp3
    The Beatles/
        Come Together.flac
```

- Sub-folder name = **Artist name**
- File name = **Song title** (if no metadata tag exists)

### 6. Run the Bot

```bash
python -m bot.bot
```

---

## 🎮 Slash Commands

| Command | Description |
|---|---|
| `/play <song>` | Search and play a song (fuzzy search) |
| `/search <query>` | Show top 10 matching songs |
| `/random` | Play a random song |
| `/queue` | Show the current queue |
| `/skip` | Skip the current song |
| `/pause` | Pause playback |
| `/resume` | Resume paused playback |
| `/stop` | Stop playback and clear queue |
| `/disconnect` | Disconnect from voice channel |
| `/nowplaying` | Show current song info |
| `/shuffle` | Shuffle the queue |
| `/loop` | Toggle single-song loop |
| `/loopqueue` | Toggle full-queue loop |
| `/rescan` | Rescan the music library |
| `/help` | Show all commands |

---

## ⚙️ Configuration Reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `DISCORD_TOKEN` | *(required)* | Your Discord bot token |
| `MUSIC_FOLDER` | `./music` | Path to your music library |
| `FFMPEG_PATH` | `ffmpeg` | Path to FFmpeg binary |
| `DEFAULT_VOLUME` | `0.5` | Volume (0.0 – 1.0) |
| `FUZZY_SCORE_THRESHOLD` | `55` | Min fuzzy match score (0-100) |

---

## 🔍 How Search Works

iSai uses **rapidfuzz** with `token_set_ratio` scoring, which means:

- Case-insensitive: `bohemian` matches `Bohemian Rhapsody`
- Punctuation-ignored: `dont stop` matches `Don't Stop Me Now`
- Order-flexible: `rhapsody queen` matches `Queen — Bohemian Rhapsody`
- Artist + title: searching `queen bohemian` works across both fields

Results are scored 0-100 and filtered by `FUZZY_SCORE_THRESHOLD`.

---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `discord.py[voice]` | Discord bot framework + voice support |
| `rapidfuzz` | Fuzzy string matching |
| `mutagen` | Audio metadata reading (title, artist, album, duration) |
| `python-dotenv` | `.env` file loading |
| FFmpeg (external) | Audio encoding/decoding |

---

## 🛠 Extending iSai

The codebase is intentionally modular:

- **Add a new command** → Add a method to the `MusicCog` class in `commands.py`
- **Change search algorithm** → Modify `MusicLibrary.search()` in `music_library.py`
- **Add metadata fields** → Extend the `Song` dataclass and `_read_metadata()` in `music_library.py`
- **Add volume control** → `GuildPlayer.volume` is already exposed; just add a `/volume` command

---

## 📝 License

MIT — use freely, modify liberally.
