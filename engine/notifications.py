"""
notifications.py - Notification system for Claudemon engine.

Handles sending notifications through:
- Native macOS notifications (via terminal-notifier if available)
- Native Linux notifications (via notify-send / libnotify)
- Webhook (via HTTP POST to CLAUDEMON_WEBHOOK_URL, local mode only)
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# i18n support
LOCALE_DIR = Path(__file__).parent.parent / "locales"
LOCALE = os.environ.get("CLAUDEMON_LANG", "en")
STRINGS = {}


def load_locale(lang="en"):
    """Load locale strings from JSON file."""
    locale_file = LOCALE_DIR / f"{lang}.json"
    if not locale_file.exists():
        locale_file = LOCALE_DIR / "en.json"
    if locale_file.exists():
        with open(locale_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _(key, **kwargs):
    """Get translated string with optional formatting."""
    import random

    global STRINGS
    if not STRINGS:
        STRINGS = load_locale(LOCALE)

    parts = key.split(".")
    value = STRINGS
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return key
    if isinstance(value, list):
        value = random.choice(value)
    if isinstance(value, str):
        return value.format(**kwargs)
    return key


def _get_creature_icon(word, level):
    """Get creature image path if it exists on disk."""
    if word and level:
        img_path = Path(__file__).parent.parent / "creatures" / f"{word}-lvl{level}.png"
        if img_path.exists():
            return str(img_path)
    return None


def _send_native_notification(title, message, word=None, level=None):
    """Send a native OS notification. Returns True if sent successfully."""
    system = platform.system()

    try:
        if system == "Darwin" and shutil.which("terminal-notifier"):
            native_title = f"✦ {word.upper() if word else title} ✦" if word else title
            cmd = [
                "terminal-notifier",
                "-title", "Claudemon",
                "-subtitle", native_title,
                "-message", message,
                "-group", "claudemon",
                "-ignoreDnD",
            ]
            icon = _get_creature_icon(word, level)
            if icon:
                cmd.extend(["-contentImage", icon])
            subprocess.run(cmd, capture_output=True, timeout=2)
            return True

        elif system == "Linux" and shutil.which("notify-send"):
            native_title = f"✦ {word.upper() if word else title} ✦" if word else title
            cmd = [
                "notify-send",
                f"Claudemon — {native_title}",
                message,
                "--app-name=Claudemon",
            ]
            icon = _get_creature_icon(word, level)
            if icon:
                cmd.extend(["--icon", icon])
            subprocess.run(cmd, capture_output=True, timeout=2)
            return True

        elif system == "Windows":
            safe_title = f"Claudemon — {title}".replace("'", "''")
            safe_msg = message.replace("'", "''")
            ps = (
                "[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');"
                "$n = New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon = [System.Drawing.SystemIcons]::Application;"
                f"$n.BalloonTipTitle = '{safe_title}';"
                f"$n.BalloonTipText = '{safe_msg}';"
                "$n.Visible = $true;"
                "$n.ShowBalloonTip(3000);"
                "Start-Sleep 4;"
                "$n.Dispose()"
            )
            subprocess.run(
                ["powershell", "-WindowStyle", "Hidden", "-Command", ps],
                capture_output=True, timeout=8,
            )
            return True

    except Exception:
        pass

    return False


def _send_webhook(event, word=None, level=None, is_new=False, evolved=False, just_hatched=False):
    """Send a webhook POST to CLAUDEMON_WEBHOOK_URL (local mode only)."""
    webhook_url = os.environ.get("CLAUDEMON_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return

    # Only send webhooks in local mode
    mode = os.environ.get("CLAUDEMON_MODE", "").lower().strip()
    if mode != "local":
        return

    payload = {
        "event": event,
        "word": word,
        "level": level,
        "is_new": is_new,
        "evolved": evolved,
        "just_hatched": just_hatched,
        "timestamp": time.time(),
    }

    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except (urllib.error.URLError, Exception):
        if os.environ.get("CLAUDEMON_DEBUG"):
            import traceback
            print(f"[webhook] Error sending to {webhook_url}:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)


def _send_webhook_async(event, **kwargs):
    """Fire-and-forget webhook send in a background thread."""
    threading.Thread(
        target=_send_webhook, args=(event,), kwargs=kwargs, daemon=True
    ).start()


def notify_async(title, message, word=None, level=None):
    """Send a native notification in background without blocking."""
    threading.Thread(
        target=_send_native_notification, args=(title, message, word, level), daemon=True
    ).start()


def notify_catch(word, result, stats):
    """
    Send notification for a catch event.

    Args:
        word: The captured word
        result: Dict from catch_word() with is_new, evolved, just_hatched, etc.
        stats: Dict from get_claudemon() with creature info
    """
    is_new = result.get("is_new", False)
    new_level = result.get("new_level", 1)
    evolved = result.get("evolved", False)
    just_hatched = result.get("just_hatched", False)

    if is_new:
        # New claudemon discovered!
        has_image = stats is not None
        notify_async(
            _("notifications.new_title"),
            _("notifications.new_discovered", word=word) if has_image else _("notifications.new_no_image", word=word),
            word=word,
            level=1 if has_image else None,

        )
        _send_webhook_async("new", word=word, level=1, is_new=True)

    elif just_hatched:
        # Egg hatched!
        notify_async(
            _("notifications.hatched_title", word=word),
            _("notifications.hatched_msg", level=new_level),
            word=word,
            level=1,

        )
        _send_webhook_async("hatched", word=word, level=new_level, just_hatched=True)

    elif evolved:
        # Evolution!
        stage = (new_level // 20) * 20
        if stage == 0:
            stage = 1
        notify_async(
            _("notifications.evolved_title", word=word),
            _("notifications.evolved_msg", level=new_level),
            word=word,
            level=stage,

        )
        _send_webhook_async("evolved", word=word, level=new_level, evolved=True)

    else:
        # Regular catch — webhook only (no native notification for every catch)
        _send_webhook_async("catch", word=word, level=new_level)
