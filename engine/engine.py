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

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.notifications import notify_catch
from engine.storage import ConfigError, get_storage

# Configuration
CATCHES_FILE = os.path.expanduser("~/.claudemon/catches.jsonl")
STATUSLINE_FILE = os.path.expanduser("~/.claudemon/statusline.json")
POSITION_FILE = os.path.expanduser("~/.claudemon/engine.pos")
POLL_INTERVAL = 0.5  # seconds
HEALTH_FILE = os.path.expanduser("~/.claudemon/engine.status")
_last_sync_ts: dict[str, float] = {}  # per-session last sync timestamp
SYNC_COOLDOWN = 3.5  # seconds between API calls per session


def _load_position(default: int) -> int:
    """Load the saved byte offset, or return default if no position file."""
    try:
        with open(POSITION_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return default


def _save_position(pos: int):
    """Atomically save the byte offset."""
    tmp = POSITION_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(pos))
    os.replace(tmp, POSITION_FILE)


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

    file_size = os.path.getsize(CATCHES_FILE)
    saved_pos = _load_position(default=file_size)

    with open(CATCHES_FILE, "r") as f:
        if saved_pos > file_size:
            # File was truncated — start from beginning
            f.seek(0)
        elif saved_pos <= file_size:
            f.seek(saved_pos)

        while True:
            line = f.readline()
            if line:
                try:
                    catch = json.loads(line.strip())
                    sid = catch.get("sid")
                    # Throttle: wait if we synced this session too recently
                    if sid:
                        elapsed = time.time() - _last_sync_ts.get(sid, 0)
                        if elapsed < SYNC_COOLDOWN:
                            time.sleep(SYNC_COOLDOWN - elapsed)
                    handle_catch(
                        storage, catch["word"],
                        ts=catch.get("ts"), proof=catch.get("proof"), sid=sid,
                        duration=catch.get("duration"),
                    )
                    if sid:
                        _last_sync_ts[sid] = time.time()
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"[engine] Invalid catch line: {e}", file=sys.stderr)
                _save_position(f.tell())
            else:
                time.sleep(POLL_INTERVAL)


_session_count_map: dict[str, int] = {}
_session_xp_map: dict[str, int] = {}
_session_duration_map: dict[str, float] = {}
_session_catches_list: dict[str, list] = {}


def update_statusline(word: str, result, sid: str, duration: float = None):
    """Write last catch + session totals to ~/.claudemon/statusline-{sid}.json."""
    try:
        event = None
        if result.is_new:
            event = "new"
        elif result.just_hatched:
            event = "hatched"
        elif result.evolved:
            event = "evolved"

        dur = round(duration, 1) if duration else 0

        count = _session_count_map.get(sid, 0) + 1
        _session_count_map[sid] = count
        total_xp = _session_xp_map.get(sid, 0) + result.xp_earned
        _session_xp_map[sid] = total_xp
        total_dur = _session_duration_map.get(sid, 0) + (duration or 0)
        _session_duration_map[sid] = total_dur

        catches = _session_catches_list.setdefault(sid, [])
        catches.append({"word": word, "xp": result.xp_earned, "duration": dur, "event": event})

        statusline_file = os.path.join(os.path.dirname(STATUSLINE_FILE), f"statusline-{sid}.json")
        data = {
            "word": word,
            "level": result.new_level,
            "event": event,
            "xp": result.xp_earned,
            "duration": dur,
            "count": count,
            "total_xp": total_xp,
            "total_duration": round(total_dur, 1),
            "catches": catches,
            "ts": time.time(),
        }
        tmp = statusline_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, statusline_file)

        # Cleanup old session files (>1h)
        sl_dir = os.path.dirname(STATUSLINE_FILE)
        now = time.time()
        for fname in os.listdir(sl_dir):
            if fname.startswith("statusline-") and fname.endswith(".json"):
                fpath = os.path.join(sl_dir, fname)
                if now - os.path.getmtime(fpath) > 3600:
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
    except Exception as e:
        print(f"[engine] Statusline write error: {e}", file=sys.stderr)


def _write_health(status: str, error: str = None):
    """Write engine health to ~/.claudemon/engine.status."""
    try:
        data = {"status": status, "ts": time.time()}
        if error:
            data["error"] = error
        tmp = HEALTH_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, HEALTH_FILE)
    except Exception:
        pass


def handle_catch(storage, word: str, ts: float = None, proof: str = None, sid: str = None, duration: float = None):
    """Process a caught word: update storage and send notifications."""
    print(f"[engine] Catch: {word} (sid={sid}, duration={duration})")

    try:
        result = storage.catch(word, ts=ts, proof=proof, sid=sid, duration=duration)
        if result is None:
            err = getattr(storage, 'last_error', 'unknown')
            print(f"[engine] Storage failed for {word}: {err}", file=sys.stderr)
            _write_health("error", err)
            return

        flags = []
        if result.is_new:
            flags.append("NEW")
        if result.just_hatched:
            flags.append("HATCHED")
        if result.evolved:
            flags.append("EVOLVED")
        tag = f" [{', '.join(flags)}]" if flags else ""
        print(f"[engine] Synced: {word} lvl={result.new_level}{tag}")

        _write_health("ok")

        # Update statusline FIRST (instant, local file write)
        if sid:
            update_statusline(word, result, sid, duration=duration)

        # Then do slower operations (extra API call + notifications)
        try:
            creature = storage.get_creature(word)
            notify_catch(word, result.__dict__, creature)
        except Exception as e:
            print(f"[engine] Notification error for {word}: {e}", file=sys.stderr)

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
