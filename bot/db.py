import sqlite3
import json
import logging
from typing import Optional, List, Dict
from bot.config import DB_FILE, DEFAULT_VOLUME
from bot.music_library import Song

log = logging.getLogger("iSai.db")

def _get_connection():
    return sqlite3.connect(DB_FILE)

def init_db():
    """Initializes the database schema."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        
        # Player state table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS player_state (
                guild_id INTEGER PRIMARY KEY,
                volume REAL,
                loop_song BOOLEAN,
                loop_queue BOOLEAN,
                autoplay BOOLEAN,
                text_channel_id INTEGER,
                last_message_id INTEGER,
                current_song_json TEXT
            )
        ''')
        
        # Queue table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                song_json TEXT,
                position INTEGER
            )
        ''')
        
        # History table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                song_json TEXT,
                position INTEGER
            )
        ''')
        
        conn.commit()
        log.info(f"Database initialized at {DB_FILE}")

def get_player_state(guild_id: int) -> dict:
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT volume, loop_song, loop_queue, autoplay, text_channel_id, last_message_id, current_song_json FROM player_state WHERE guild_id = ?', (guild_id,))
        row = cursor.fetchone()
        
        if row:
            state = {
                'volume': row[0],
                'loop_song': bool(row[1]),
                'loop_queue': bool(row[2]),
                'autoplay': bool(row[3]),
                'text_channel_id': row[4],
                'last_message_id': row[5],
                'current_song': Song.from_dict(json.loads(row[6])) if row[6] else None
            }
        else:
            state = {
                'volume': DEFAULT_VOLUME,
                'loop_song': False,
                'loop_queue': False,
                'autoplay': False,
                'text_channel_id': None,
                'last_message_id': None,
                'current_song': None
            }
            # Insert default state
            cursor.execute('''
                INSERT INTO player_state (guild_id, volume, loop_song, loop_queue, autoplay, text_channel_id, last_message_id, current_song_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (guild_id, state['volume'], state['loop_song'], state['loop_queue'], state['autoplay'], state['text_channel_id'], None, None))
            conn.commit()
            
        return state

def update_player_state(guild_id: int, **kwargs):
    valid_keys = {'volume', 'loop_song', 'loop_queue', 'autoplay', 'text_channel_id', 'last_message_id', 'current_song_json'}
    updates = []
    values = []
    
    for k, v in kwargs.items():
        if k in valid_keys:
            updates.append(f"{k} = ?")
            if isinstance(v, bool):
                values.append(int(v))
            elif k == 'current_song_json':
                values.append(json.dumps(v.to_dict()) if v else None)
            else:
                values.append(v)
                
    if not updates:
        return
        
    values.append(guild_id)
    query = f"UPDATE player_state SET {', '.join(updates)} WHERE guild_id = ?"
    
    with _get_connection() as conn:
        cursor = conn.cursor()
        # Ensure row exists
        cursor.execute('SELECT 1 FROM player_state WHERE guild_id = ?', (guild_id,))
        if not cursor.fetchone():
            get_player_state(guild_id) # Creates default row
        cursor.execute(query, tuple(values))
        conn.commit()

# --- Queue Methods ---
def enqueue(guild_id: int, song: Song) -> int:
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(position) FROM queue WHERE guild_id = ?', (guild_id,))
        max_pos = cursor.fetchone()[0]
        pos = (max_pos or 0) + 1
        
        cursor.execute('INSERT INTO queue (guild_id, song_json, position) VALUES (?, ?, ?)', 
                       (guild_id, json.dumps(song.to_dict()), pos))
        conn.commit()
        
        cursor.execute('SELECT COUNT(*) FROM queue WHERE guild_id = ?', (guild_id,))
        return cursor.fetchone()[0]

def get_queue(guild_id: int) -> List[Song]:
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT song_json FROM queue WHERE guild_id = ? ORDER BY position ASC', (guild_id,))
        rows = cursor.fetchall()
        return [Song.from_dict(json.loads(row[0])) for row in rows]
        
def get_queue_length(guild_id: int) -> int:
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM queue WHERE guild_id = ?', (guild_id,))
        return cursor.fetchone()[0]

def pop_next(guild_id: int) -> Optional[Song]:
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, song_json FROM queue WHERE guild_id = ? ORDER BY position ASC LIMIT 1', (guild_id,))
        row = cursor.fetchone()
        
        if row:
            cursor.execute('DELETE FROM queue WHERE id = ?', (row[0],))
            conn.commit()
            return Song.from_dict(json.loads(row[1]))
        return None

def clear_queue(guild_id: int):
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM queue WHERE guild_id = ?', (guild_id,))
        conn.commit()

def shuffle_queue(guild_id: int):
    import random
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM queue WHERE guild_id = ?', (guild_id,))
        ids = [row[0] for row in cursor.fetchall()]
        
        if not ids:
            return
            
        random.shuffle(ids)
        for i, doc_id in enumerate(ids):
            cursor.execute('UPDATE queue SET position = ? WHERE id = ?', (i, doc_id))
        conn.commit()

def push_front(guild_id: int, song: Song):
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT MIN(position) FROM queue WHERE guild_id = ?', (guild_id,))
        min_pos = cursor.fetchone()[0]
        pos = (min_pos or 0) - 1
        
        cursor.execute('INSERT INTO queue (guild_id, song_json, position) VALUES (?, ?, ?)', 
                       (guild_id, json.dumps(song.to_dict()), pos))
        conn.commit()

# --- History Methods ---
def push_history(guild_id: int, song: Song, max_len: int = 10):
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(position) FROM history WHERE guild_id = ?', (guild_id,))
        max_pos = cursor.fetchone()[0]
        pos = (max_pos or 0) + 1
        
        cursor.execute('INSERT INTO history (guild_id, song_json, position) VALUES (?, ?, ?)', 
                       (guild_id, json.dumps(song.to_dict()), pos))
        
        # Enforce max length
        cursor.execute('SELECT COUNT(*) FROM history WHERE guild_id = ?', (guild_id,))
        count = cursor.fetchone()[0]
        
        if count > max_len:
            limit = count - max_len
            cursor.execute('DELETE FROM history WHERE id IN (SELECT id FROM history WHERE guild_id = ? ORDER BY position ASC LIMIT ?)', (guild_id, limit))
            
        conn.commit()

def pop_history(guild_id: int) -> Optional[Song]:
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, song_json FROM history WHERE guild_id = ? ORDER BY position DESC LIMIT 1', (guild_id,))
        row = cursor.fetchone()
        
        if row:
            cursor.execute('DELETE FROM history WHERE id = ?', (row[0],))
            conn.commit()
            return Song.from_dict(json.loads(row[1]))
        return None

def get_history(guild_id: int) -> List[Song]:
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT song_json FROM history WHERE guild_id = ? ORDER BY position ASC', (guild_id,))
        rows = cursor.fetchall()
        return [Song.from_dict(json.loads(row[0])) for row in rows]
        
def clear_history(guild_id: int):
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM history WHERE guild_id = ?', (guild_id,))
        conn.commit()
