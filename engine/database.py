"""
database.py - SQLite database management for claudemons

Supports concurrent access (multiple claudemon instances in parallel)
using WAL mode and automatic retries.
"""

import os
import random
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.claudemon/claudemon.db"))

EVOLUTION_STAGES = [1, 20, 40, 60, 80, 100]
HATCH_THRESHOLD = 3  # Number of catches to hatch an egg
MAX_TEAM_SIZE = 6

# Concurrency settings
DB_TIMEOUT = 30.0  # Timeout for acquiring lock (seconds)
MAX_RETRIES = 5    # Max retries on lock errors


_db_initialized = False
_db_init_lock = threading.Lock()


def _raw_connection():
    """Return a raw DB connection (no init guard — used by init_db itself)."""
    os.makedirs(DB_PATH.parent, exist_ok=True)
    conn = sqlite3.connect(
        DB_PATH,
        detect_types=sqlite3.PARSE_DECLTYPES,
        timeout=DB_TIMEOUT,
        isolation_level="DEFERRED"
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _ensure_initialized():
    """Lazy init_db() — called once on first get_connection()."""
    global _db_initialized
    if _db_initialized:
        return
    with _db_init_lock:
        if not _db_initialized:
            init_db()
            _db_initialized = True


def get_connection():
    """Return a DB connection with concurrency support."""
    _ensure_initialized()
    return _raw_connection()


def with_retry(func):
    """Decorator to retry on database locked errors."""
    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower():
                    last_error = e
                    base_delay = 0.05 * (2 ** attempt)
                    time.sleep(base_delay * (0.5 + random.random()))
                else:
                    raise
        raise last_error
    return wrapper


def init_db():
    """Initialize the database."""
    with _raw_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claudemons (
                word TEXT PRIMARY KEY,
                level INTEGER DEFAULT 1,
                times_caught INTEGER DEFAULT 1,
                evolution_stage INTEGER DEFAULT 1,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                in_team INTEGER DEFAULT 0,
                team_position INTEGER DEFAULT NULL
            )
        """)

        # Notifications table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT,
                word TEXT,
                level INTEGER,
                timestamp REAL NOT NULL,
                read INTEGER DEFAULT 0
            )
        """)
        conn.commit()

        # Migration: add columns if they don't exist
        try:
            conn.execute("ALTER TABLE claudemons ADD COLUMN in_team INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE claudemons ADD COLUMN team_position INTEGER DEFAULT NULL")
        except sqlite3.OperationalError:
            pass
        conn.commit()


def get_evolution_stage(level):
    """Return the evolution stage for a given level."""
    for stage in reversed(EVOLUTION_STAGES):
        if level >= stage:
            return stage
    return 1


@with_retry
def catch_word(word):
    """
    Record a captured word.
    Returns dict with: is_new, old_level, new_level, evolved, just_hatched

    Thread-safe: uses retry logic for concurrent access.
    """
    now = datetime.now()

    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT level, evolution_stage, times_caught FROM claudemons WHERE word = ?",
            (word,)
        )
        row = cursor.fetchone()

        if row is None:
            # New egg!
            conn.execute(
                """INSERT INTO claudemons
                   (word, level, times_caught, evolution_stage, first_seen, last_seen, in_team)
                   VALUES (?, 1, 1, 1, ?, ?, 0)""",
                (word, now, now)
            )
            conn.commit()
            return {
                "is_new": True,
                "old_level": 0,
                "new_level": 1,
                "evolved": False,
                "just_hatched": False,
                "is_egg": True
            }
        else:
            old_level, old_stage, old_catches = row
            new_level = old_level + 1
            new_catches = old_catches + 1
            new_stage = get_evolution_stage(new_level)
            evolved = new_stage > old_stage

            # Check if egg just hatched
            was_egg = old_catches < HATCH_THRESHOLD
            is_egg = new_catches < HATCH_THRESHOLD
            just_hatched = was_egg and not is_egg

            conn.execute(
                """UPDATE claudemons
                   SET level = ?, times_caught = ?,
                       evolution_stage = ?, last_seen = ?
                   WHERE word = ?""",
                (new_level, new_catches, new_stage, now, word)
            )
            conn.commit()

            return {
                "is_new": False,
                "old_level": old_level,
                "new_level": new_level,
                "evolved": evolved,
                "just_hatched": just_hatched,
                "is_egg": is_egg
            }


def serialize_date(dt):
    """Convert datetime to ISO string."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat() if hasattr(dt, 'isoformat') else str(dt)


def _row_to_claudemon(row):
    """Convert a row to claudemon dict."""
    times_caught = row[2]
    return {
        "word": row[0],
        "level": row[1],
        "times_caught": times_caught,
        "evolution_stage": row[3],
        "first_seen": serialize_date(row[4]),
        "last_seen": serialize_date(row[5]),
        "in_team": bool(row[6]) if len(row) > 6 else False,
        "team_position": row[7] if len(row) > 7 else None,
        "is_egg": times_caught < HATCH_THRESHOLD,
        "hatch_progress": min(times_caught / HATCH_THRESHOLD, 1.0)
    }


def get_claudemon(word):
    """Return info for a claudemon."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT word, level, times_caught, evolution_stage, first_seen, last_seen, in_team, team_position FROM claudemons WHERE word = ?",
            (word,)
        )
        row = cursor.fetchone()
        if row:
            return _row_to_claudemon(row)
        return None


def get_all_claudemons():
    """Return all claudemons."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT word, level, times_caught, evolution_stage, first_seen, last_seen, in_team, team_position FROM claudemons ORDER BY first_seen DESC"
        )
        return [_row_to_claudemon(row) for row in cursor.fetchall()]


def get_team():
    """Return team members."""
    with get_connection() as conn:
        cursor = conn.execute(
            """SELECT word, level, times_caught, evolution_stage, first_seen, last_seen, in_team, team_position
               FROM claudemons
               WHERE in_team = 1
               ORDER BY team_position ASC"""
        )
        return [_row_to_claudemon(row) for row in cursor.fetchall()]


@with_retry
def add_to_team(word):
    """Add a claudemon to the team."""
    with get_connection() as conn:
        # Check if team is not full
        count = conn.execute("SELECT COUNT(*) FROM claudemons WHERE in_team = 1").fetchone()[0]
        if count >= MAX_TEAM_SIZE:
            return False, "Team is full"

        # Check that claudemon exists and is not an egg
        cursor = conn.execute("SELECT times_caught FROM claudemons WHERE word = ?", (word,))
        row = cursor.fetchone()
        if not row:
            return False, "Claudemon not found"
        if row[0] < HATCH_THRESHOLD:
            return False, "Cannot add egg to team"

        # Find next available position
        next_pos = conn.execute("SELECT COALESCE(MAX(team_position), 0) + 1 FROM claudemons WHERE in_team = 1").fetchone()[0]

        conn.execute(
            "UPDATE claudemons SET in_team = 1, team_position = ? WHERE word = ?",
            (next_pos, word)
        )
        conn.commit()
        return True, "Added to team"


@with_retry
def remove_from_team(word):
    """Remove a claudemon from the team."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE claudemons SET in_team = 0, team_position = NULL WHERE word = ?",
            (word,)
        )
        conn.commit()
        return True


def get_stats():
    """Return global stats."""
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM claudemons").fetchone()[0]
        hatched = conn.execute(f"SELECT COUNT(*) FROM claudemons WHERE times_caught >= {HATCH_THRESHOLD}").fetchone()[0]
        eggs = total - hatched
        total_catches = conn.execute("SELECT SUM(times_caught) FROM claudemons").fetchone()[0] or 0
        max_level = conn.execute("SELECT MAX(level) FROM claudemons").fetchone()[0] or 0
        team_size = conn.execute("SELECT COUNT(*) FROM claudemons WHERE in_team = 1").fetchone()[0]

        return {
            "total_discovered": total,
            "total_hatched": hatched,
            "total_eggs": eggs,
            "total_catches": total_catches,
            "max_level": max_level,
            "team_size": team_size,
            "max_team_size": MAX_TEAM_SIZE,
            "hatch_threshold": HATCH_THRESHOLD
        }

