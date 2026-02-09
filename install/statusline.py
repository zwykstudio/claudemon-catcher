#!/usr/bin/env python3
"""
Claudemon statusline for Claude Code.

Claude Code calls this once per assistant message. Since the engine needs
a few seconds to sync, we briefly poll for the result before returning.

Install:
  cc --install-statusline
"""

import json
import os
import sys
import time

STATUSLINE_DIR = os.path.expanduser("~/.claudemon")
HEALTH_FILE = os.path.join(STATUSLINE_DIR, "engine.status")
DEBUG_LOG = os.path.join(STATUSLINE_DIR, "statusline-debug.log")

# ANSI colors
RST = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
DIM = "\033[2m"

TAG = "[Claudemon]"

_debug_enabled = os.environ.get("CLAUDEMON_DEBUG") == "1"


def _debug(msg):
    if _debug_enabled:
        try:
            with open(DEBUG_LOG, "a") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        except OSError:
            pass


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def main():
    # Read Claude Code session data from stdin
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}

    model = (data.get("model") or {}).get("display_name", "")
    pct = int((data.get("context_window") or {}).get("used_percentage", 0))

    base = model
    if pct:
        base = f"{base} {pct}%"

    # No SID → plain statusline
    sid = os.environ.get("CLAUDEMON_SID", "")
    if not sid:
        print(base)
        return

    # Check engine health
    health = _read_json(HEALTH_FILE)
    if health and health.get("status") == "error":
        age = time.time() - float(health.get("ts", 0))
        if age < 300:
            err = str(health.get("error", ""))[:50]
            print(f"{base} | {TAG} {RED}\u26a0 {err}{RST}")
            return

    engine_file = os.path.join(STATUSLINE_DIR, f"statusline-{sid}.json")
    live_file = os.path.join(STATUSLINE_DIR, f"statusline-{sid}-live.json")

    def read_live():
        d = _read_json(live_file)
        if not d:
            return None
        return d

    def read_engine():
        d = _read_json(engine_file)
        if not d:
            return {}
        return d

    live = read_live()
    eng = read_engine()
    live_word = (live or {}).get("word", "")
    live_phase = (live or {}).get("phase", "")
    live_ts = float((live or {}).get("ts", 0))

    _debug(f"SID={sid} live={live_word}:{live_phase}:{live_ts} eng={eng.get('word', '')}:{eng.get('ts', 0)}")

    def engine_has_word(word):
        return (eng.get("word") and eng["word"] == word
                and float(eng.get("ts", 0)) >= live_ts)

    # Build session summary from engine data
    eng_count = int(eng.get("count", 0))
    eng_total_xp = int(eng.get("total_xp", 0))
    summary = ""
    if eng_total_xp > 0:
        summary = f"{eng_count} caught \u00b7 {eng_total_xp}xp"
    elif eng_count > 0:
        summary = f"{eng_count} caught"

    now = time.time()

    # Phase 1: Word is currently being captured (spinner still active)
    # Show immediately — no waiting
    if live_word and live_phase == "capturing" and (now - live_ts) < 120:
        elapsed = now - float((live or {}).get("start", live_ts))
        elapsed_str = f"{elapsed:.0f}s" if elapsed >= 1 else ""
        line = f"{live_word}"
        if elapsed_str:
            line += f" {DIM}{elapsed_str}{RST}"
        line += f" {DIM}...{RST}"
        _debug(f"-> capturing {live_word} ({elapsed_str})")
        if summary:
            print(f"{base} | {TAG} {line} ({summary})")
        else:
            print(f"{base} | {TAG} {line}")
        return

    # Phase 2: Word was flushed, waiting for engine sync
    # Brief poll (up to ~3s) since it was already submitted
    if live_word and live_phase == "syncing" and (now - live_ts) < 30:
        if not engine_has_word(live_word):
            _debug(f"waiting for engine to sync '{live_word}'...")
            for i in range(1, 7):
                time.sleep(0.5)
                eng = read_engine()
                if engine_has_word(live_word):
                    _debug(f"-> synced after {i}x0.5s")
                    # Refresh summary
                    eng_count = int(eng.get("count", 0))
                    eng_total_xp = int(eng.get("total_xp", 0))
                    if eng_total_xp > 0:
                        summary = f"{eng_count} caught \u00b7 {eng_total_xp}xp"
                    elif eng_count > 0:
                        summary = f"{eng_count} caught"
                    break

    # Phase 3: Show synced engine data (if available and recent)
    eng_word = eng.get("word", "")
    eng_ts = float(eng.get("ts", 0))

    if eng_word and (now - eng_ts) < 300:
        line = eng_word
        eng_xp = int(eng.get("xp", 0))
        eng_dur = eng.get("duration", 0)
        eng_event = eng.get("event", "")

        if eng_xp > 0:
            line += f" {DIM}+{eng_xp}xp{RST}"
        if eng_dur:
            line += f" {DIM}{eng_dur}s{RST}"
        if eng_event == "new":
            line += f" {GREEN}NEW{RST}"
        elif eng_event == "evolved":
            line += f" {MAGENTA}EVOLVED{RST}"
        elif eng_event == "hatched":
            line += f" {YELLOW}HATCHED{RST}"

        if not summary:
            summary = f"{eng_count} caught"
        _debug(f"-> synced {eng_word} +{eng_xp}xp {eng_event}")
        print(f"{base} | {TAG} {line} ({summary})")
        return

    # Phase 4: Syncing but engine didn't respond in time — show word without xp
    if live_word and live_phase == "syncing" and (now - live_ts) < 120:
        dur = (live or {}).get("duration", 0)
        line = live_word
        if dur:
            line += f" {DIM}{dur}s{RST}"
        line += f" {DIM}syncing...{RST}"
        _debug(f"-> syncing {live_word} (engine timeout)")
        if summary:
            print(f"{base} | {TAG} {line} ({summary})")
        else:
            print(f"{base} | {TAG} {line}")
        return

    # No data or stale
    if summary:
        _debug("-> summary only")
        print(f"{base} | {TAG} ({summary})")
    else:
        _debug("-> tag only")
        print(f"{base} | {TAG}")


if __name__ == "__main__":
    main()
