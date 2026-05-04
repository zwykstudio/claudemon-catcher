#!/usr/bin/env python3
"""
wrapper.py - PTY wrapper for Claude CLI spinner word capture.

Zero external dependencies on macOS/Linux. On Windows: pip install pywinpty

Usage:  python3 wrapper.py [claude args...]
Alias:  alias cc='python3 /path/to/wrapper.py'
Output: ~/.claudemon/catches.jsonl
"""

import atexit
import hashlib
import hmac
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import time
import uuid

from _version import VERSION
CATCHES_FILE = os.path.expanduser("~/.claudemon/catches.jsonl")
VERSION_CHECK_FILE = os.path.expanduser("~/.claudemon/version.check")
VERSION_CHECK_TTL = 86400  # 24 hours
DEBUG = os.environ.get("CLAUDEMON_DEBUG", "") == "1"
DEBUG_LOG = os.path.expanduser("~/.claudemon/debug.log") if DEBUG else None
SPINNER_IDLE_TIMEOUT = 2.0
_debug_f = None

ANSI_RE = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]")
CURSOR_MOVE_RE = re.compile(r"\x1b\[[0-9;]*[ABCDEFGHdf]")
SPINNER_CHARS = "·✢✳✶✻✽"
WORD_RE = re.compile(r"[" + re.escape(SPINNER_CHARS) + r"]\s?([A-Z][a-zA-Z-]*in[g'])…")


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


# Session catch log (wrapper-side, independent of engine)
_session_catches: list[dict] = []
STATUSLINE_LIVE_DIR = os.path.expanduser("~/.claudemon")


def _write_live_status(sid: str, data: dict):
    """Write live capture status for the statusline (wrapper → statusline.py)."""
    try:
        data["ts"] = time.time()
        live_file = os.path.join(STATUSLINE_LIVE_DIR, f"statusline-{sid}-live.json")
        tmp = live_file + ".tmp"
        os.makedirs(STATUSLINE_LIVE_DIR, exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, live_file)
    except Exception:
        pass


def emit(word: str, ts: float, proof: str, sid: str, duration: float = None) -> None:
    os.makedirs(os.path.dirname(CATCHES_FILE), exist_ok=True)
    dur = round(duration, 3) if duration is not None else 0.0
    entry = {"word": word, "ts": ts, "duration": dur, "proof": proof, "sid": sid}
    with open(CATCHES_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    _session_catches.append({"word": word, "duration": round(dur, 1)})


def flush_pending(pending: dict, sid: str, force: bool = False) -> None:
    """Emit pending word if forced or spinner idle > SPINNER_IDLE_TIMEOUT."""
    if not pending:
        return
    if not force and time.time() - pending.get("last_seen", pending["ts"]) <= SPINNER_IDLE_TIMEOUT:
        # Still capturing — refresh live file every ~5s so statusline.py ts stays fresh
        if time.time() - pending.get("_live_refresh", 0) > 5:
            _write_live_status(sid, {"phase": "capturing", "word": pending["word"], "start": pending["ts"]})
            pending["_live_refresh"] = time.time()
        return
    word = pending["word"]
    duration = time.time() - pending["ts"]
    emit(word, pending["ts"], pending["proof"], sid, duration=duration)
    _write_live_status(sid, {"phase": "syncing", "word": word, "duration": round(duration, 1)})
    dbg(f"  >>> FLUSHED: {word} (duration={duration:.3f}s)")
    pending.clear()


def process_chunk(raw: bytes, buf: str, seen: set, pending: dict, session_hash, sid: str) -> str:
    session_hash.update(raw)
    text = raw.decode("utf-8", errors="replace")
    text = CURSOR_MOVE_RE.sub(" ", text)  # preserve word boundaries at cursor jumps
    clean = re.sub(r"<[^>]+>", "", ANSI_RE.sub("", text))
    buf = (buf + clean)[-500:]

    has_spinner = any(ch in clean for ch in SPINNER_CHARS)
    if DEBUG and (has_spinner or "…" in clean):
        dbg(f"CHUNK: {clean!r}")
        dbg(f"  BUF: {buf[-300:]!r}")

    if has_spinner and pending:
        pending["last_seen"] = time.time()

    word = (WORD_RE.findall(buf) or [None])[-1]
    if word:
        # Normalize slang: "Thinkin'" → "Thinking"
        if word.endswith("in'"):
            word = word[:-3] + "ing"
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
        _write_live_status(sid, {"phase": "capturing", "word": word, "start": ts})
        dbg(f"  >>> WORD FOUND (pending): {word} (proof={proof[:8]}… sid={sid})")

        idx = buf.find("…")
        if idx != -1:
            buf = buf[idx + 1:]
    elif DEBUG and (has_spinner or "…" in clean):
        dbg(f"  --- {'ALREADY SEEN: ' + word if word and word in seen else 'NO MATCH'}")

    # Simulate \r (carriage return): the terminal overwrites the current line
    # from the start. Discard stale content before the last \r to prevent
    # accumulating overlapping spinner frames that cause word corruption.
    cr_idx = buf.rfind("\r")
    if cr_idx != -1:
        buf = buf[cr_idx + 1:]

    return buf


def _check_statusline() -> bool:
    """Return True if claudemon statusline is configured in Claude Code."""
    try:
        with open(os.path.expanduser("~/.claude/settings.json")) as f:
            cfg = json.load(f)
        sl = cfg.get("statusLine", {})
        cmd = sl.get("command", "") if isinstance(sl, dict) else str(sl)
        return "claudemon" in cmd.lower() or "statusline." in cmd
    except Exception:
        return False


def _check_api_key() -> tuple[str, str]:
    """Check API key / mode config. Returns (status, detail)."""
    mode = os.environ.get("CLAUDEMON_MODE", "").lower().strip()
    api_key = os.environ.get("CLAUDEMON_API_KEY", "").strip()

    if mode == "local":
        return ("local", "local mode")
    if api_key and api_key.startswith("sk_claudemon_"):
        return ("cloud", "cloud sync")
    if api_key:
        return ("error", "invalid API key format")
    return ("error", "no API key set")


def _check_spinner_verbs() -> bool:
    """Return True if user has custom spinnerVerbs in Claude settings."""
    try:
        with open(SETTINGS_FILE) as f:
            cfg = json.load(f)
        sv = cfg.get("spinnerVerbs")
        if not sv or not isinstance(sv, dict):
            return False
        verbs = sv.get("verbs", [])
        return bool(verbs)
    except Exception:
        return False


def _is_cloud_mode() -> bool:
    """Return True if running in cloud mode (has valid API key, not local)."""
    status, _ = _check_api_key()
    return status == "cloud"


def _should_bypass() -> bool:
    """Return True if wrapper should bypass PTY (cloud + custom spinnerVerbs)."""
    return _is_cloud_mode() and _check_spinner_verbs()


def _bypass_claude() -> None:
    """Print warning and exec Claude directly without PTY wrapping."""
    YELLOW = "\033[33m"
    DIM = "\033[2m"
    RESET = "\033[0m"
    MAGENTA = "\033[35m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"

    print(
        f"{BOLD}{MAGENTA}claudemon{RESET} {CYAN}v{VERSION}{RESET} — "
        f"{YELLOW}⚠ custom spinnerVerbs detected — wrapper disabled{RESET}",
        file=sys.stderr,
    )
    print(
        f"  {DIM}remove spinnerVerbs from ~/.claude/settings.json to enable catching{RESET}",
        file=sys.stderr,
    )
    print(file=sys.stderr)

    claude = find_claude()
    os.execvp(claude, [claude] + sys.argv[1:])


def _check_engine_health():
    """Quick local check: is the engine daemon running and healthy?

    Returns:
        True  — running and healthy (or can't determine)
        False — definitely not running
        str   — running but last sync had an error (returns error message)
    """
    try:
        if platform.system() == "Darwin":
            r = subprocess.run(
                ["launchctl", "list", "com.claudemon.engine"],
                capture_output=True, timeout=2,
            )
            if r.returncode != 0:
                return False
        elif platform.system() == "Linux":
            r = subprocess.run(
                ["systemctl", "--user", "is-active", "claudemon-engine"],
                capture_output=True, text=True, timeout=2,
            )
            if r.stdout.strip() != "active":
                return False
        else:
            return True  # can't check on this platform
    except (OSError, subprocess.TimeoutExpired):
        return True  # don't warn if we can't check

    # Check health file for recent errors
    health_file = os.path.expanduser("~/.claudemon/engine.status")
    try:
        with open(health_file) as f:
            health = json.load(f)
        if health.get("status") == "error":
            age = time.time() - health.get("ts", 0)
            if age < 3600:  # only warn about recent errors
                return health.get("error", "unknown error")
    except (OSError, json.JSONDecodeError):
        pass

    return True


def _check_update() -> tuple:
    """Compare local HEAD with remote HEAD.

    Returns (status, message) where status is "current", "behind", or "error".
    Uses a 24h cache (~/.claudemon/version.check) to avoid hitting the
    remote on every startup. Never raises.
    """
    try:
        repo_dir = os.path.dirname(os.path.abspath(__file__))

        # Current local HEAD
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=3, cwd=repo_dir,
        ).stdout.strip()
        if not local:
            return ("error", "")

        def _is_behind(local_sha, remote_sha):
            """True only if local is an ancestor of remote (i.e. remote is ahead)."""
            r = subprocess.run(
                ["git", "merge-base", "--is-ancestor", local_sha, remote_sha],
                capture_output=True, timeout=3, cwd=repo_dir,
            )
            return r.returncode == 0

        # Check cache
        try:
            with open(VERSION_CHECK_FILE) as f:
                cache = json.load(f)
            if (
                time.time() - cache.get("ts", 0) < VERSION_CHECK_TTL
                and cache.get("local") == local
            ):
                remote = cache.get("remote", "")
                if remote and remote != local and _is_behind(local, remote):
                    return ("behind", "update available — run: cc update")
                return ("current", "")
        except (OSError, json.JSONDecodeError, KeyError):
            pass

        # Fetch remote HEAD
        r = subprocess.run(
            ["git", "ls-remote", "origin", "HEAD"],
            capture_output=True, text=True, timeout=3, cwd=repo_dir,
        )
        remote = r.stdout.split()[0] if r.returncode == 0 and r.stdout.strip() else ""

        # Write cache atomically
        try:
            os.makedirs(os.path.dirname(VERSION_CHECK_FILE), exist_ok=True)
            tmp = VERSION_CHECK_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"ts": time.time(), "local": local, "remote": remote}, f)
            os.replace(tmp, VERSION_CHECK_FILE)
        except OSError:
            pass

        if remote and remote != local and _is_behind(local, remote):
            return ("behind", "update available — run: cc update")
        return ("current", "")
    except Exception:
        return ("error", "")


def _auto_update() -> bool:
    """Pull latest code and install missing deps. Returns True on success."""
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        r = subprocess.run(
            ["git", "-C", repo_dir, "pull", "--ff-only"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False
    except Exception:
        return False

    # Install missing dependencies
    for dep in ("certifi", "cryptography"):
        try:
            __import__(dep)
        except ImportError:
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-q", dep],
                    capture_output=True, timeout=30,
                )
            except Exception:
                pass

    # Clear version cache
    try:
        os.remove(VERSION_CHECK_FILE)
    except OSError:
        pass

    return True


def print_banner() -> None:
    """Print a single-line startup banner before launching Claude."""
    DIM = "\033[2m"
    RESET = "\033[0m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"

    status, detail = _check_api_key()
    if status == "error":
        status_str = f"{RED}✗ {detail}{RESET}"
    elif status == "local":
        status_str = f"{YELLOW}● {detail}{RESET}"
    else:
        status_str = f"{GREEN}✓ {detail}{RESET}"

    MAGENTA = "\033[35m"
    BOLD = "\033[1m"

    update_status, _ = _check_update()
    if update_status == "current":
        ver_suffix = f" {DIM}✓ up to date{RESET}"
    elif update_status == "behind":
        print(f"{BOLD}{MAGENTA}claudemon{RESET} {CYAN}v{VERSION}{RESET} {YELLOW}↑ updating...{RESET}", file=sys.stderr)
        if _auto_update():
            print(f"  {GREEN}✓{RESET} Updated successfully", file=sys.stderr)
        else:
            print(f"  {YELLOW}⚠ auto-update failed — run manually: cc update{RESET}", file=sys.stderr)
        ver_suffix = ""
    else:
        ver_suffix = ""

    line = f"{BOLD}{MAGENTA}claudemon{RESET} {CYAN}v{VERSION}{RESET}{ver_suffix} — {status_str}"
    print(line, file=sys.stderr)

    if status == "error" and not os.environ.get("CLAUDEMON_MODE", "").strip():
        print(f"  {DIM}set CLAUDEMON_API_KEY or CLAUDEMON_MODE=local{RESET}", file=sys.stderr)

    # Quick engine health check (no network calls)
    engine_ok = _check_engine_health()
    if engine_ok is False:
        print(f"  {YELLOW}⚠ engine not running — run: cc engine restart{RESET}", file=sys.stderr)
    elif isinstance(engine_ok, str):
        print(f"  {YELLOW}⚠ engine error: {engine_ok}{RESET}", file=sys.stderr)

    if not _check_statusline():
        print(
            f"  {DIM}tip: add claudemon to your statusline:{RESET} "
            f"{DIM}cc --install-statusline{RESET}",
            file=sys.stderr,
        )

    print(file=sys.stderr)


RECAP_MESSAGES = [
    "Keep catching 'em all!",
    "Another productive session!",
    "Your claudemons grow stronger.",
    "The wild words never saw it coming.",
    "Gotta catch 'em all!",
    "You're on fire, trainer!",
    "Those words didn't stand a chance.",
    "Your collection grows...",
]

def print_recap(sid: str, seen: set, start_time: float) -> None:
    """Print a session recap after Claude exits."""
    if not seen:
        return

    DIM = "\033[2m"
    RESET = "\033[0m"
    BOLD = "\033[1m"
    MAGENTA = "\033[35m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"

    # Use wrapper-side catch log as base (always available)
    local_catches = list(_session_catches)
    total_dur = sum(c["duration"] for c in local_catches)

    elapsed = time.time() - start_time
    elapsed_str = f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s" if elapsed >= 60 else f"{elapsed:.0f}s"

    out = sys.stderr

    # Print header immediately so user sees feedback right away
    print(file=out)
    print(f"{BOLD}{MAGENTA}claudemon{RESET} {DIM}session recap{RESET}", file=out)
    print(f"{DIM}{'─' * 38}{RESET}", file=out)

    # Try to enrich with engine data (XP, events) — best effort
    sl_file = os.path.join(STATUSLINE_LIVE_DIR, f"statusline-{sid}.json")
    engine_data = {}
    total_xp = 0
    showed_sync = False
    try:
        for attempt in range(4):
            try:
                with open(sl_file) as f:
                    sl_data = json.load(f)
                total_xp = sl_data.get("total_xp", 0)
                for c in sl_data.get("catches", []):
                    engine_data[c["word"]] = c
                if len(engine_data) >= len(local_catches):
                    break
            except (OSError, json.JSONDecodeError):
                pass
            if not showed_sync:
                print(f"  {DIM}syncing catches…{RESET}", end="", flush=True, file=out)
                showed_sync = True
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    if showed_sync:
        print(f"\r{' ' * 30}\r", end="", flush=True, file=out)

    for c in local_catches:
        word = c["word"]
        dur = c["duration"]
        # Merge engine data if available
        eng = engine_data.get(word, {})
        xp = eng.get("xp", 0)
        event = eng.get("event")

        xp_str = f"+{xp}xp" if xp else ""
        dur_str = f"{dur}s" if dur else ""
        event_str = ""
        if event == "new":
            event_str = f"{GREEN}NEW{RESET}"
        elif event == "evolved":
            event_str = f"{MAGENTA}EVOLVED{RESET}"
        elif event == "hatched":
            event_str = f"{YELLOW}HATCHED{RESET}"

        parts = [f"{word:<22s}"]
        if xp_str:
            parts.append(f"{DIM}{xp_str:>7s}{RESET}")
        if dur_str:
            parts.append(f"{DIM}{dur_str:>7s}{RESET}")
        if event_str:
            parts.append(f" {event_str}")
        print("".join(parts), file=out)

    print(f"{DIM}{'─' * 38}{RESET}", file=out)

    summary_parts = [f"{BOLD}{len(local_catches)}{RESET} caught"]
    if total_xp:
        summary_parts.append(f"{CYAN}{total_xp}xp{RESET}")
    if total_dur:
        dur_fmt = f"{total_dur:.1f}s"
        summary_parts.append(f"{DIM}{dur_fmt}{RESET}")
    summary_parts.append(f"{DIM}session {elapsed_str}{RESET}")

    print(f"{' · '.join(summary_parts)}", file=out)
    print(f"{DIM}{random.choice(RECAP_MESSAGES)}{RESET}", file=out)
    print(file=out)


def find_claude() -> str:
    path = shutil.which("claude")
    if not path:
        print("Error: 'claude' not found in PATH", file=sys.stderr)
        sys.exit(1)
    return path


def _main_posix() -> None:
    if _should_bypass():
        _bypass_claude()

    import fcntl
    import pty
    import select
    import signal
    import termios
    import tty

    claude = find_claude()
    print_banner()

    sid = uuid.uuid4().hex[:12]
    os.environ["CLAUDEMON_SID"] = sid

    master, slave = pty.openpty()
    fcntl.fcntl(master, fcntl.F_SETFL, fcntl.fcntl(master, fcntl.F_GETFL) | os.O_NONBLOCK)

    def _sync_winsize(target_fd):
        try:
            sz = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
            fcntl.ioctl(target_fd, termios.TIOCSWINSZ, sz)
        except (OSError, ValueError):
            pass

    _sync_winsize(slave)

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
        if slave > 2:
            os.close(slave)
        # Acquire the slave as the controlling terminal so SIGWINCH
        # (and other tty signals) are delivered to Claude on resize.
        try:
            tmp_fd = os.open(os.ttyname(0), os.O_RDWR)
            os.close(tmp_fd)
        except OSError:
            pass
        os.execvp(claude, [claude] + sys.argv[1:])

    os.close(slave)

    def _on_winch(_signum, _frame):
        _sync_winsize(master)

    try:
        signal.signal(signal.SIGWINCH, _on_winch)
    except (OSError, ValueError):
        pass

    buf, seen, pending = "", set(), {}
    session_hash = hashlib.sha256()
    start_time = time.time()
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

    flush_pending(pending, sid, force=True)
    dbg(f"Session ended. Caught {len(seen)} words: {seen}")
    if _debug_f:
        _debug_f.close()
    print_recap(sid, seen, start_time)
    try:
        _, status = os.waitpid(pid, 0)
        sys.exit(os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1)
    except ChildProcessError:
        sys.exit(0)


def _main_windows() -> None:
    if _should_bypass():
        _bypass_claude()

    import threading

    try:
        from winpty import PTY
    except ImportError:
        print("Error: pywinpty not installed. Install: pip install pywinpty", file=sys.stderr)
        sys.exit(1)

    claude = find_claude()
    print_banner()

    sid = uuid.uuid4().hex[:12]
    os.environ["CLAUDEMON_SID"] = sid

    args = sys.argv[1:]
    cmd = claude + (" " + " ".join(args) if args else "")

    try:
        cols, rows = os.get_terminal_size()
    except OSError:
        cols, rows = 80, 24

    pty_proc = PTY(cols, rows)
    pty_proc.spawn(cmd)

    buf, seen, pending = "", set(), {}
    session_hash = hashlib.sha256()
    start_time = time.time()
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
        flush_pending(pending, sid, force=True)
        dbg(f"Session ended. Caught {len(seen)} words: {seen}")
        if _debug_f:
            _debug_f.close()

    print_recap(sid, seen, start_time)
    sys.exit(pty_proc.get_exitstatus() or 0)


CLI_FLAGS = {"--stats", "--list", "--dashboard", "-d", "--help", "-h"}

SETTINGS_FILE = os.path.expanduser("~/.claude/settings.json")


def _install_statusline() -> None:
    """Install claudemon statusline into Claude Code settings.json."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install", "statusline.py")
    if not os.path.isfile(script):
        print(f"Error: statusline script not found at {script}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(SETTINGS_FILE) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    except json.JSONDecodeError:
        print(f"Error: {SETTINGS_FILE} is not valid JSON", file=sys.stderr)
        sys.exit(1)

    cfg["statusLine"] = {"type": "command", "command": f"python3 {script}"}

    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")

    print("\033[32m✓\033[0m claudemon statusline installed")


def _cmd_update() -> None:
    """Pull latest code and restart the engine daemon."""
    DIM = "\033[2m"
    RESET = "\033[0m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"

    repo_dir = os.path.dirname(os.path.abspath(__file__))

    # 1. git pull
    print("  Pulling latest changes...", file=sys.stderr)
    r = subprocess.run(
        ["git", "-C", repo_dir, "pull", "--ff-only"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        print(f"  {RED}✗ git pull failed:{RESET} {r.stderr.strip()}", file=sys.stderr)
        print(f"  {DIM}Try manually: git -C {repo_dir} pull{RESET}", file=sys.stderr)
        sys.exit(1)

    pull_output = r.stdout.strip()
    if "Already up to date" in pull_output:
        print(f"  {GREEN}✓{RESET} Already up to date", file=sys.stderr)
    else:
        print(f"  {GREEN}✓{RESET} Updated", file=sys.stderr)
        for line in pull_output.splitlines():
            if line.strip():
                print(f"    {DIM}{line}{RESET}", file=sys.stderr)

    # 2. Install missing dependencies
    for dep in ("certifi", "cryptography"):
        try:
            __import__(dep)
        except ImportError:
            print(f"  Installing {dep}...", file=sys.stderr)
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", dep],
                capture_output=True, timeout=30,
            )

    # 3. Restart engine
    import time as _time

    from cli.engine_commands import _is_running, _restart
    print("  Restarting engine...", file=sys.stderr)
    if _restart():
        _time.sleep(0.5)
        if _is_running():
            print(f"  {GREEN}✓{RESET} Engine restarted", file=sys.stderr)
        else:
            print(f"  {YELLOW}⚠{RESET} Loaded but process not yet running", file=sys.stderr)
    else:
        print(f"  {YELLOW}⚠{RESET} Engine restart failed — run: cc engine restart", file=sys.stderr)

    # 4. Clear version cache so next launch shows fresh status
    try:
        os.remove(VERSION_CHECK_FILE)
    except OSError:
        pass

    print(file=sys.stderr)


def _dispatch_cli():
    """Check if argv[1] is a claudemon CLI flag; if so, delegate to cli.main."""
    if len(sys.argv) < 2:
        return False
    first = sys.argv[1]
    if first == "--install-statusline":
        _install_statusline()
        return True
    if first == "update":
        _cmd_update()
        return True
    if first == "engine":
        from cli.engine_commands import engine_main
        engine_main(sys.argv[2:])
        return True
    if first in CLI_FLAGS:
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
