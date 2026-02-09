"""
engine_commands.py - Engine management CLI for Claudemon.

Usage:
    cc engine              Show engine status
    cc engine status       Show engine status
    cc engine restart      Restart the engine daemon
    cc engine update-key   Update the API key in the daemon config
    cc engine reset        Reset engine state and restart
"""

import json
import os
import platform
import subprocess
import sys
import time

CLAUDEMON_DIR = os.path.expanduser("~/.claudemon")
CATCHES_FILE = os.path.join(CLAUDEMON_DIR, "catches.jsonl")
POSITION_FILE = os.path.join(CLAUDEMON_DIR, "engine.pos")
HEALTH_FILE = os.path.join(CLAUDEMON_DIR, "engine.status")

IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

PLIST_LABEL = "com.claudemon.engine"
PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{PLIST_LABEL}.plist")
SYSTEMD_UNIT = "claudemon-engine"

# Colors
B = "\033[1m"
D = "\033[2m"
R = "\033[0m"
RED = "\033[31m"
GRN = "\033[32m"
YLW = "\033[33m"
CYN = "\033[36m"


def _c(code, text):
    if not sys.stderr.isatty():
        return str(text)
    return f"{code}{text}{R}"


def _mask_key(key: str) -> str:
    """Mask an API key for display: sk_claudemon_...9kG"""
    if len(key) > 16:
        return key[:13] + "..." + key[-3:]
    return key[:4] + "..."


# ── Daemon process checks ──


def _is_running_mac() -> bool:
    r = subprocess.run(
        ["launchctl", "list", PLIST_LABEL],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return False
    # Parse PID from launchctl list output — first field is PID (or "-")
    for line in r.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == PLIST_LABEL:
            return parts[0] != "-"
        # Single-item output (when querying specific label)
        if len(parts) >= 1 and parts[0] not in ("-", "PID"):
            try:
                int(parts[0])
                return True
            except ValueError:
                pass
    # If launchctl list succeeded, the service is at least registered
    return True


def _is_running_linux() -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "is-active", SYSTEMD_UNIT],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "active"


def _is_running() -> bool:
    if IS_MAC:
        return _is_running_mac()
    if IS_LINUX:
        return _is_running_linux()
    return False


# ── Restart helpers ──


def _restart_mac() -> bool:
    if not os.path.isfile(PLIST_PATH):
        print(f"  {_c(RED, 'Error:')} plist not found at {PLIST_PATH}", file=sys.stderr)
        print(f"  {_c(D, 'Run install/setup.sh to install the engine daemon.')}", file=sys.stderr)
        return False
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
    r = subprocess.run(["launchctl", "load", PLIST_PATH], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  {_c(RED, 'Failed to load:')} {r.stderr.strip()}", file=sys.stderr)
        return False
    return True


def _restart_linux() -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "restart", SYSTEMD_UNIT],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  {_c(RED, 'Failed to restart:')} {r.stderr.strip()}", file=sys.stderr)
        return False
    return True


def _restart() -> bool:
    if IS_MAC:
        return _restart_mac()
    if IS_LINUX:
        return _restart_linux()
    print(f"  {_c(RED, 'Unsupported platform')}", file=sys.stderr)
    return False


# ── Read plist env var ──


def _read_plist_env(key: str) -> str | None:
    """Read an environment variable from the installed plist."""
    if not os.path.isfile(PLIST_PATH):
        return None
    r = subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c", f"Print :EnvironmentVariables:{key}", PLIST_PATH],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return r.stdout.strip()
    return None


# ── Actions ──


def cmd_status():
    """Show engine daemon status."""
    print(file=sys.stderr)
    print(f"  {_c(B, 'ENGINE STATUS')}", file=sys.stderr)
    print(f"  {'─' * 34}", file=sys.stderr)

    # Running?
    running = _is_running()
    if running:
        print(f"  Daemon     {_c(GRN, 'running')}", file=sys.stderr)
    else:
        print(f"  Daemon     {_c(RED, 'stopped')}", file=sys.stderr)

    # API key (from plist/systemd env or shell env)
    api_key = None
    if IS_MAC:
        api_key = _read_plist_env("CLAUDEMON_API_KEY")
    if not api_key:
        api_key = os.environ.get("CLAUDEMON_API_KEY", "")
    mode = None
    if IS_MAC:
        mode = _read_plist_env("CLAUDEMON_MODE")
    if not mode:
        mode = os.environ.get("CLAUDEMON_MODE", "")

    if mode.lower() == "local":
        print(f"  Mode       {_c(YLW, 'local')}", file=sys.stderr)
    elif api_key and api_key.startswith("sk_claudemon_"):
        print(f"  API key    {_c(D, _mask_key(api_key))}", file=sys.stderr)
    elif api_key:
        print(f"  API key    {_c(RED, 'invalid format')}", file=sys.stderr)
    else:
        print(f"  API key    {_c(RED, 'not set')}", file=sys.stderr)

    # Health file
    try:
        with open(HEALTH_FILE) as f:
            health = json.load(f)
        status = health.get("status", "unknown")
        ts = health.get("ts", 0)
        age = time.time() - ts
        if age < 60:
            ago = f"{int(age)}s ago"
        elif age < 3600:
            ago = f"{int(age / 60)}m ago"
        else:
            ago = f"{int(age / 3600)}h ago"

        if status == "ok":
            print(f"  Last sync  {_c(GRN, 'ok')} {_c(D, ago)}", file=sys.stderr)
        else:
            err = health.get("error", "unknown error")
            print(f"  Last sync  {_c(RED, 'error')} {_c(D, ago)}", file=sys.stderr)
            print(f"             {_c(RED, err)}", file=sys.stderr)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"  Last sync  {_c(D, 'no data')}", file=sys.stderr)

    # Pending catches
    try:
        catches_size = os.path.getsize(CATCHES_FILE)
    except OSError:
        catches_size = 0

    try:
        with open(POSITION_FILE) as f:
            pos = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        pos = catches_size  # assume caught up

    pending = max(0, catches_size - pos)
    if pending > 0:
        # Estimate line count
        try:
            with open(CATCHES_FILE, "rb") as f:
                f.seek(pos)
                pending_lines = f.read().count(b"\n")
        except OSError:
            pending_lines = 0
        print(f"  Pending    {_c(YLW, f'{pending_lines} catches')} {_c(D, f'({pending} bytes)')}", file=sys.stderr)
    else:
        print(f"  Pending    {_c(D, 'none')}", file=sys.stderr)

    # Log file hint
    if IS_MAC:
        print(f"  Log        {_c(D, '/tmp/claudemon-engine.log')}", file=sys.stderr)
    elif IS_LINUX:
        print(f"  Log        {_c(D, 'journalctl --user -u claudemon-engine -f')}", file=sys.stderr)

    print(file=sys.stderr)

    if not running:
        print(f"  {_c(D, 'Run:')} cc engine restart", file=sys.stderr)
        print(file=sys.stderr)


def cmd_restart():
    """Restart the engine daemon."""
    print(f"  Restarting engine...", file=sys.stderr)
    if _restart():
        time.sleep(0.5)
        if _is_running():
            print(f"  {_c(GRN, '✓')} Engine restarted", file=sys.stderr)
        else:
            print(f"  {_c(YLW, '⚠')} Loaded but process not yet running", file=sys.stderr)
            print(f"  {_c(D, 'Check logs:')} {'cat /tmp/claudemon-engine.log' if IS_MAC else 'journalctl --user -u claudemon-engine'}", file=sys.stderr)


def cmd_update_key(args: list[str]):
    """Update the API key in the daemon configuration."""
    # Get key from args or env
    if args:
        key = args[0]
    else:
        key = os.environ.get("CLAUDEMON_API_KEY", "").strip()

    if not key:
        print(f"  {_c(RED, 'No key provided.')}", file=sys.stderr)
        print(f"  Usage: cc engine update-key <KEY>", file=sys.stderr)
        print(f"  Or set CLAUDEMON_API_KEY in your environment.", file=sys.stderr)
        return

    if not key.startswith("sk_claudemon_"):
        print(f"  {_c(RED, 'Invalid key format.')} Must start with sk_claudemon_", file=sys.stderr)
        return

    print(f"  Updating API key to {_c(D, _mask_key(key))}...", file=sys.stderr)

    if IS_MAC:
        if not os.path.isfile(PLIST_PATH):
            print(f"  {_c(RED, 'Error:')} plist not found. Run install/setup.sh first.", file=sys.stderr)
            return
        # Update plist with PlistBuddy
        pb = "/usr/libexec/PlistBuddy"
        subprocess.run([pb, "-c", "Delete :EnvironmentVariables:CLAUDEMON_API_KEY", PLIST_PATH],
                       capture_output=True)
        r = subprocess.run([pb, "-c", f"Add :EnvironmentVariables:CLAUDEMON_API_KEY string {key}", PLIST_PATH],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  {_c(RED, 'Failed to update plist:')} {r.stderr.strip()}", file=sys.stderr)
            return
        # Also remove CLAUDEMON_MODE=local if it was set
        subprocess.run([pb, "-c", "Delete :EnvironmentVariables:CLAUDEMON_MODE", PLIST_PATH],
                       capture_output=True)

    elif IS_LINUX:
        service_path = os.path.expanduser(f"~/.config/systemd/user/{SYSTEMD_UNIT}.service")
        if not os.path.isfile(service_path):
            print(f"  {_c(RED, 'Error:')} service file not found. Run install/setup.sh first.", file=sys.stderr)
            return
        # Read, update, write
        with open(service_path) as f:
            lines = f.readlines()
        new_lines = [l for l in lines if "CLAUDEMON_API_KEY=" not in l and "CLAUDEMON_MODE=" not in l]
        # Insert after WorkingDirectory line
        for i, l in enumerate(new_lines):
            if l.startswith("WorkingDirectory="):
                new_lines.insert(i + 1, f'Environment="CLAUDEMON_API_KEY={key}"\n')
                break
        with open(service_path, "w") as f:
            f.writelines(new_lines)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    else:
        print(f"  {_c(RED, 'Unsupported platform')}", file=sys.stderr)
        return

    # Restart
    print(f"  Restarting engine...", file=sys.stderr)
    if _restart():
        time.sleep(0.5)
        print(f"  {_c(GRN, '✓')} API key updated and engine restarted", file=sys.stderr)
    else:
        print(f"  {_c(YLW, '⚠')} Key updated but restart failed", file=sys.stderr)


def cmd_reset():
    """Reset engine state (reprocess from start) and restart."""
    removed = []
    for f in (POSITION_FILE, HEALTH_FILE):
        if os.path.exists(f):
            os.remove(f)
            removed.append(os.path.basename(f))

    if removed:
        print(f"  Cleared: {', '.join(removed)}", file=sys.stderr)
    else:
        print(f"  {_c(D, 'No state files to clear')}", file=sys.stderr)

    print(f"  Restarting engine...", file=sys.stderr)
    if _restart():
        time.sleep(0.5)
        print(f"  {_c(GRN, '✓')} Engine reset and restarted", file=sys.stderr)
        print(f"  {_c(D, 'The engine will reprocess all catches from the beginning.')}", file=sys.stderr)


def engine_main(args: list[str]):
    """Entry point for `cc engine [action]`."""
    action = args[0] if args else "status"

    if action in ("status", "s"):
        cmd_status()
    elif action in ("restart", "r"):
        cmd_restart()
    elif action == "update-key":
        cmd_update_key(args[1:])
    elif action == "reset":
        cmd_reset()
    else:
        print(f"  Unknown action: {action}", file=sys.stderr)
        print(file=sys.stderr)
        print(f"  Usage: cc engine [status|restart|update-key|reset]", file=sys.stderr)
        print(file=sys.stderr)
        print(f"  {_c(B, 'Actions:')}", file=sys.stderr)
        print(f"    status       Show engine health (default)", file=sys.stderr)
        print(f"    restart      Restart the engine daemon", file=sys.stderr)
        print(f"    update-key   Update API key in daemon config", file=sys.stderr)
        print(f"    reset        Reset engine state and restart", file=sys.stderr)
        print(file=sys.stderr)
