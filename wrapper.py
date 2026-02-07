#!/usr/bin/env python3
"""
wrapper.py - PTY wrapper for Claude CLI spinner word capture.

Zero external dependencies on macOS/Linux. On Windows: pip install pywinpty

Usage:  python3 wrapper.py [claude args...]
Alias:  alias cc='python3 /path/to/wrapper.py'
Output: ~/.claudemon/catches.jsonl
"""

import atexit, hashlib, hmac, json, os, platform, re, shutil, sys, time, uuid

CATCHES_FILE = os.path.expanduser("~/.claudemon/catches.jsonl")
DEBUG = os.environ.get("CLAUDEMON_DEBUG", "") == "1"
DEBUG_LOG = os.path.expanduser("~/.claudemon/debug.log") if DEBUG else None
SPINNER_IDLE_TIMEOUT = 2.0
_debug_f = None

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]")
SPINNER_CHARS = "·✢✳✶✻✽"
WORD_RE = re.compile(r"[" + re.escape(SPINNER_CHARS) + r"]\s?([A-Z][a-zA-Z-]*ing)…")


def _close_debug():
    global _debug_f
    if _debug_f is not None:
        try:
            _debug_f.close()
        except Exception:
            pass
        _debug_f = None


def dbg(msg: str) -> None:
    global _debug_f
    if not DEBUG:
        return
    if _debug_f is None:
        os.makedirs(os.path.dirname(DEBUG_LOG), exist_ok=True)
        _debug_f = open(DEBUG_LOG, "a")
        _debug_f.write(f"\n{'='*60}\n[{time.strftime('%H:%M:%S')}] wrapper started (PID {os.getpid()})\n{'='*60}\n")
        atexit.register(_close_debug)
    _debug_f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    _debug_f.flush()


def emit(word: str, ts: float, proof: str, sid: str, duration: float = None) -> None:
    os.makedirs(os.path.dirname(CATCHES_FILE), exist_ok=True)
    entry = {"word": word, "ts": ts, "duration": round(duration, 3) if duration is not None else 0.0, "proof": proof, "sid": sid}
    with open(CATCHES_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def flush_pending(pending: dict, sid: str, force: bool = False) -> None:
    """Emit pending word if forced or spinner idle > SPINNER_IDLE_TIMEOUT."""
    if not pending:
        return
    if not force and time.time() - pending.get("last_seen", pending["ts"]) <= SPINNER_IDLE_TIMEOUT:
        return
    duration = time.time() - pending["ts"]
    emit(pending["word"], pending["ts"], pending["proof"], sid, duration=duration)
    dbg(f"  >>> FLUSHED: {pending['word']} (duration={duration:.3f}s)")
    pending.clear()


def process_chunk(raw: bytes, buf: str, seen: set, pending: dict, session_hash, sid: str) -> str:
    session_hash.update(raw)
    text = raw.decode("utf-8", errors="replace")
    clean = re.sub(r"<[^>]+>", "", ANSI_RE.sub("", text))
    buf = (buf + clean)[-500:]

    has_spinner = any(ch in clean for ch in SPINNER_CHARS)
    if DEBUG and (has_spinner or "…" in clean):
        dbg(f"CHUNK: {clean!r}")
        dbg(f"  BUF: {buf[-300:]!r}")

    if has_spinner and pending:
        pending["last_seen"] = time.time()

    word = (WORD_RE.findall(buf) or [None])[-1]
    if word and word not in seen:
        ts = time.time()
        proof = hmac.new(session_hash.digest(), f"{word}:{ts}".encode(), hashlib.sha256).hexdigest()[:16]

        if pending:
            duration = ts - pending["ts"]
            emit(pending["word"], pending["ts"], pending["proof"], sid, duration=duration)
            dbg(f"  >>> EMITTED: {pending['word']} (duration={duration:.3f}s)")

        pending.clear()
        pending.update({"word": word, "ts": ts, "proof": proof, "last_seen": ts})
        seen.add(word)
        dbg(f"  >>> WORD FOUND (pending): {word} (proof={proof[:8]}… sid={sid})")

        idx = buf.find("…")
        if idx != -1:
            buf = buf[idx + 1:]
    elif DEBUG and (has_spinner or "…" in clean):
        dbg(f"  --- {'ALREADY SEEN: ' + word if word and word in seen else 'NO MATCH'}")

    return buf


def find_claude() -> str:
    path = shutil.which("claude")
    if not path:
        print("Error: 'claude' not found in PATH", file=sys.stderr)
        sys.exit(1)
    return path


def _main_posix() -> None:
    import fcntl, pty, select, termios, tty

    claude = find_claude()
    master, slave = pty.openpty()
    fcntl.fcntl(master, fcntl.F_SETFL, fcntl.fcntl(master, fcntl.F_GETFL) | os.O_NONBLOCK)

    old_tty = None
    try:
        old_tty = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin)
    except termios.error:
        pass

    pid = os.fork()
    if pid == 0:
        os.close(master)
        os.setsid()
        for fd in (0, 1, 2):
            os.dup2(slave, fd)
        os.close(slave)
        os.execvp(claude, [claude] + sys.argv[1:])

    os.close(slave)
    buf, seen, pending = "", set(), {}
    sid = uuid.uuid4().hex[:12]
    session_hash = hashlib.sha256()
    dbg(f"Session ID: {sid}")

    try:
        while True:
            try:
                r, _, _ = select.select([master, sys.stdin.fileno()], [], [], 0.05)
            except (ValueError, OSError):
                break

            if master in r:
                try:
                    data = os.read(master, 512)
                    if not data:
                        break
                    os.write(sys.stdout.fileno(), data)
                    buf = process_chunk(data, buf, seen, pending, session_hash, sid)
                except (OSError, BlockingIOError):
                    pass

            if sys.stdin.fileno() in r:
                try:
                    data = os.read(sys.stdin.fileno(), 512)
                    if data:
                        os.write(master, data)
                    else:
                        break
                except (OSError, BlockingIOError):
                    pass

            flush_pending(pending, sid)

            res = os.waitpid(pid, os.WNOHANG)
            if res[0] != 0:
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
        if old_tty:
            termios.tcsetattr(sys.stdin, termios.TCSAFLUSH, old_tty)
        os.close(master)

    dbg(f"Session ended. Caught {len(seen)} words: {seen}")
    if _debug_f:
        _debug_f.close()
    try:
        _, status = os.waitpid(pid, 0)
        sys.exit(os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1)
    except ChildProcessError:
        sys.exit(0)


def _main_windows() -> None:
    import threading

    try:
        from winpty import PTY
    except ImportError:
        print("Error: pywinpty not installed. Install: pip install pywinpty", file=sys.stderr)
        sys.exit(1)

    claude = find_claude()
    args = sys.argv[1:]
    cmd = claude + (" " + " ".join(args) if args else "")

    try:
        cols, rows = os.get_terminal_size()
    except OSError:
        cols, rows = 80, 24

    pty_proc = PTY(cols, rows)
    pty_proc.spawn(cmd)

    buf, seen, pending = "", set(), {}
    sid = uuid.uuid4().hex[:12]
    session_hash = hashlib.sha256()
    dbg(f"Session ID: {sid}")

    def stdin_reader():
        import msvcrt
        while pty_proc.isalive():
            if msvcrt.kbhit():
                pty_proc.write(msvcrt.getwch())
            else:
                time.sleep(0.02)

    threading.Thread(target=stdin_reader, daemon=True).start()

    try:
        while pty_proc.isalive():
            data_str = pty_proc.read(4096, blocking=False)
            if data_str:
                sys.stdout.write(data_str)
                sys.stdout.flush()
                buf = process_chunk(data_str.encode("utf-8"), buf, seen, pending, session_hash, sid)
            else:
                time.sleep(0.03)
            flush_pending(pending, sid)

        data_str = pty_proc.read(4096, blocking=False)
        if data_str:
            sys.stdout.write(data_str)
            sys.stdout.flush()
    finally:
        dbg(f"Session ended. Caught {len(seen)} words: {seen}")
        if _debug_f:
            _debug_f.close()

    sys.exit(pty_proc.get_exitstatus() or 0)


CLI_FLAGS = {"--stats", "--list", "--dashboard", "-d", "--help", "-h"}


def _dispatch_cli():
    """Check if argv contains a CLI flag; if so, delegate to cli.main."""
    if any(arg in CLI_FLAGS for arg in sys.argv[1:]):
        from cli.main import main as cli_main
        cli_main()
        return True
    return False


if __name__ == "__main__":
    if _dispatch_cli():
        pass
    elif platform.system() == "Windows":
        _main_windows()
    else:
        _main_posix()
