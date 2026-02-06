#!/usr/bin/env python3
"""
wrapper.py - Thin PTY wrapper for Claude CLI spinner word capture.

This is the ONLY file users need to audit. ~90 LOC, zero external dependencies.
It captures spinner words from Claude CLI and writes them to a JSONL file.

Usage:
    python3 wrapper.py [claude args...]
    # Or via alias: alias cc='python3 /path/to/wrapper.py'

Output:
    ~/.claudemon/catches.jsonl  (append-only, one JSON per line)
    Format: {"word": "Zigzagging", "ts": 1738765432.1, "proof": "a1b2c3d4e5f6a7b8"}
"""

import fcntl
import hashlib
import hmac
import json
import os
import pty
import re
import select
import sys
import time
import termios
import tty
import uuid

# Output file for caught words
CATCHES_FILE = os.path.expanduser("~/.claudemon/catches.jsonl")

# Debug mode: CLAUDEMON_DEBUG=1 python3 wrapper.py
DEBUG = os.environ.get("CLAUDEMON_DEBUG", "") == "1"
DEBUG_LOG = os.path.expanduser("~/.claudemon/debug.log") if DEBUG else None
_debug_f = None


def dbg(msg: str) -> None:
    """Write a debug line to the log file."""
    global _debug_f
    if not DEBUG:
        return
    if _debug_f is None:
        os.makedirs(os.path.dirname(DEBUG_LOG), exist_ok=True)
        _debug_f = open(DEBUG_LOG, "a")
        _debug_f.write(f"\n{'='*60}\n")
        _debug_f.write(f"[{time.strftime('%H:%M:%S')}] wrapper started (PID {os.getpid()})\n")
        _debug_f.write(f"{'='*60}\n")
    _debug_f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    _debug_f.flush()

# ANSI escape sequence pattern
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]")

# Spinner chars used by Claude CLI
SPINNER_CHARS = "·✢✳✶✻✽"

# Tight regex: spinner char + optional space + Titlecase word ending in "ing" + ellipsis
# Matches: "· Coalescing…", "✢ Spelunking…", "✳Re-evaluating…"
WORD_RE = re.compile(r"[" + re.escape(SPINNER_CHARS) + r"]\s?([A-Z][a-zA-Z-]*ing)…")


def extract(text: str) -> str | None:
    """Extract a spinner word from cleaned text using tight pattern matching."""
    matches = WORD_RE.findall(text)
    return matches[-1] if matches else None  # Last match = freshest


def emit(word: str, ts: float, proof: str, session_id: str) -> None:
    """Write a catch to the JSONL file."""
    os.makedirs(os.path.dirname(CATCHES_FILE), exist_ok=True)
    with open(CATCHES_FILE, "a") as f:
        f.write(json.dumps({"word": word, "ts": ts, "proof": proof, "sid": session_id}) + "\n")


def main() -> None:
    # Find claude binary
    claude = os.popen("which claude").read().strip()
    if not claude:
        print("Error: 'claude' not found in PATH", file=sys.stderr)
        sys.exit(1)

    # Open PTY
    master, slave = pty.openpty()
    fcntl.fcntl(master, fcntl.F_SETFL, fcntl.fcntl(master, fcntl.F_GETFL) | os.O_NONBLOCK)

    # Save terminal settings
    old_tty = None
    try:
        old_tty = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin)
    except termios.error:
        pass

    # Fork child process
    pid = os.fork()
    if pid == 0:
        # Child: exec claude
        os.close(master)
        os.setsid()
        for fd in (0, 1, 2):
            os.dup2(slave, fd)
        os.close(slave)
        os.execvp(claude, [claude] + sys.argv[1:])

    # Parent: relay I/O and capture words
    os.close(slave)
    buf = ""
    seen: set[str] = set()
    session_id = uuid.uuid4().hex[:12]  # Short unique session ID
    session_hash = hashlib.sha256()  # Rolling hash of all PTY output
    dbg(f"Session ID: {session_id}")

    try:
        while True:
            try:
                r, _, _ = select.select([master, sys.stdin.fileno()], [], [], 0.05)
            except (ValueError, OSError):
                break

            # Read from claude (master)
            if master in r:
                try:
                    data = os.read(master, 512)
                    if not data:
                        break
                    os.write(sys.stdout.fileno(), data)
                    session_hash.update(data)

                    # Extract spinner words
                    text = data.decode("utf-8", errors="replace")
                    clean = re.sub(r"<[^>]+>", "", ANSI_RE.sub("", text))
                    buf = (buf + clean)[-500:]

                    # Debug: log every buffer chunk that contains spinner chars or ellipsis
                    has_spinner = any(ch in clean for ch in SPINNER_CHARS)
                    has_ellipsis = "…" in clean
                    if DEBUG and (has_spinner or has_ellipsis):
                        dbg(f"CHUNK: {clean!r}")
                        dbg(f"  BUF: {buf[-300:]!r}")

                    word = extract(buf)
                    if word and word not in seen:
                        seen.add(word)
                        ts = time.time()
                        proof = hmac.new(
                            session_hash.digest(),
                            f"{word}:{ts}".encode(),
                            hashlib.sha256,
                        ).hexdigest()[:16]
                        emit(word, ts, proof, session_id)
                        dbg(f"  >>> WORD FOUND: {word} (proof={proof[:8]}… sid={session_id})")
                        # Consume matched portion
                        ellipsis_idx = buf.find("…")
                        if ellipsis_idx != -1:
                            buf = buf[ellipsis_idx + 1 :]
                    elif DEBUG and (has_spinner or has_ellipsis):
                        if word and word in seen:
                            dbg(f"  --- ALREADY SEEN: {word}")
                        else:
                            dbg(f"  --- NO WORD (extract returned: {word!r})")
                except (OSError, BlockingIOError):
                    pass

            # Read from stdin
            if sys.stdin.fileno() in r:
                try:
                    data = os.read(sys.stdin.fileno(), 512)
                    if data:
                        os.write(master, data)
                    else:
                        break
                except (OSError, BlockingIOError):
                    pass

            # Check if child exited
            res = os.waitpid(pid, os.WNOHANG)
            if res[0] != 0:
                # Drain remaining output
                try:
                    while True:
                        data = os.read(master, 512)
                        if not data:
                            break
                        os.write(sys.stdout.fileno(), data)
                except (OSError, BlockingIOError):
                    pass
                break

    finally:
        # Restore terminal
        if old_tty:
            termios.tcsetattr(sys.stdin, termios.TCSAFLUSH, old_tty)
        os.close(master)

    # Wait for child and exit with its status
    dbg(f"Session ended. Caught {len(seen)} words: {seen}")
    if _debug_f:
        _debug_f.close()
    _, status = os.waitpid(pid, 0)
    sys.exit(os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1)


if __name__ == "__main__":
    main()
