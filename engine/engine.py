#!/usr/bin/env python3
"""
engine.py - Claudemon game engine daemon.

Watches ~/.claudemon/catches.jsonl for new catches and processes them:
- Updates storage (local SQLite or cloud platform)
- Triggers notifications (new/hatched/evolved)

This runs as a background daemon, started by launchd.

Environment:
    CLAUDEMON_MODE: "local" (default) or "cloud"
    CLAUDEMON_API_KEY: Required for cloud mode (sk_claudemon_...)
"""

import json
import os
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.storage import get_storage
from engine.notifications import notify_catch

# Configuration
CATCHES_FILE = os.path.expanduser("~/.claudemon/catches.jsonl")
POLL_INTERVAL = 0.5  # seconds


def watch_catches():
    """Tail -f style watching of the catches file."""
    os.makedirs(os.path.dirname(CATCHES_FILE), exist_ok=True)
    if not os.path.exists(CATCHES_FILE):
        Path(CATCHES_FILE).touch()

    storage = get_storage()
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
                    )
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"[engine] Invalid catch line: {e}", file=sys.stderr)
            else:
                time.sleep(POLL_INTERVAL)


def handle_catch(storage, word: str, ts: float = None, proof: str = None, sid: str = None):
    """Process a caught word: update storage and send notifications."""
    print(f"[engine] Catch: {word} (sid={sid})")

    try:
        result = storage.catch(word, ts=ts, proof=proof, sid=sid)
        if result is None:
            print(f"[engine] Storage failed for {word}", file=sys.stderr)
            return

        creature = storage.get_creature(word)
        notify_catch(word, result.__dict__, creature)

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
