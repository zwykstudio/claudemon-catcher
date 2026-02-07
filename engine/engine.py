#!/usr/bin/env python3
"""
engine.py - Claudemon game engine daemon.

Watches ~/.claudemon/catches.jsonl for new catches and processes them:
- Updates storage (local SQLite or cloud platform)
- Triggers notifications (new/hatched/evolved)

This runs as a background daemon (launchd on macOS, systemd on Linux).

Cloud mode is the default (requires CLAUDEMON_API_KEY).
Set CLAUDEMON_MODE=local for local-only operation.
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.storage import get_storage, ConfigError
from engine.notifications import notify_catch

# Configuration
CATCHES_FILE = os.path.expanduser("~/.claudemon/catches.jsonl")
STATUSLINE_FILE = os.path.expanduser("~/.claudemon/statusline.json")
POLL_INTERVAL = 0.5  # seconds


def watch_catches():
    """Tail -f style watching of the catches file."""
    os.makedirs(os.path.dirname(CATCHES_FILE), exist_ok=True)
    if not os.path.exists(CATCHES_FILE):
        Path(CATCHES_FILE).touch()

    try:
        storage = get_storage()
    except ConfigError as e:
        print(f"[engine] Configuration error:\n{e}", file=sys.stderr)
        sys.exit(1)

    mode = "cloud" if type(storage).__name__ == "CloudStorage" else "local"
    print(f"[engine] Mode: {mode}")
    print(f"[engine] Watching {CATCHES_FILE}")

    with open(CATCHES_FILE, "r") as f:
        f.seek(0, 2)  # Seek to end

        while True:
            line = f.readline()
            if line:
                try:
                    catch = json.loads(line.strip())
                    handle_catch(
                        storage, catch["word"],
                        ts=catch.get("ts"), proof=catch.get("proof"), sid=catch.get("sid"),
                        duration=catch.get("duration"),
                    )
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"[engine] Invalid catch line: {e}", file=sys.stderr)
            else:
                time.sleep(POLL_INTERVAL)


def update_statusline(word: str, result, session_catches: int):
    """Write last catch info to ~/.claudemon/statusline.json for the Claude Code statusline."""
    try:
        data = {
            "word": word,
            "is_new": result.is_new,
            "level": result.new_level,
            "evolved": result.evolved,
            "just_hatched": result.just_hatched,
            "is_egg": result.is_egg,
            "session_catches": session_catches,
            "ts": time.time(),
        }
        tmp = STATUSLINE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, STATUSLINE_FILE)
    except Exception as e:
        print(f"[engine] Statusline write error: {e}", file=sys.stderr)


# Session-level catch counter
_session_catches = 0


def handle_catch(storage, word: str, ts: float = None, proof: str = None, sid: str = None, duration: float = None):
    """Process a caught word: update storage and send notifications."""
    global _session_catches
    print(f"[engine] Catch: {word} (sid={sid}, duration={duration})")

    try:
        result = storage.catch(word, ts=ts, proof=proof, sid=sid, duration=duration)
        if result is None:
            print(f"[engine] Storage failed for {word}", file=sys.stderr)
            return

        _session_catches += 1
        creature = storage.get_creature(word)
        notify_catch(word, result.__dict__, creature)
        update_statusline(word, result, _session_catches)

    except Exception as e:
        print(f"[engine] Error processing {word}: {e}", file=sys.stderr)


def main():
    print("[engine] Starting...")
    try:
        watch_catches()
    except KeyboardInterrupt:
        print("\n[engine] Stopped.")
    except Exception as e:
        print(f"[engine] Fatal: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
