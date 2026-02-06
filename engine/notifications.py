"""
notifications.py - Notification system for Claudemon engine.

Handles sending notifications through:
- Native macOS notifications (via terminal-notifier if available)
- Web dashboard (via HTTP POST to local server)
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# Server port for web dashboard
DEFAULT_PORT = 17712

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


def notify(title, message, word=None, level=None, notif_type="info"):
    """
    Send notification to web dashboard + native macOS (if available).

    Args:
        title: Notification title
        message: Notification body
        word: Claudemon word (for image)
        level: Claudemon level (for image stage)
        notif_type: 'new', 'hatched', 'evolved', 'info'
    """
    # Check if native notifications are available
    has_native = shutil.which("terminal-notifier") is not None
    native_sent = False

    # 1. Try native macOS notification first (if installed)
    if has_native:
        try:
            native_title = f"✦ {word.upper() if word else title} ✦" if word else title
            cmd = [
                "terminal-notifier",
                "-title", "Claudemon",
                "-subtitle", native_title,
                "-message", message,
                "-group", "claudemon",
                "-ignoreDnD",
            ]

            # Add icon if creature image exists on disk
            if word and level:
                img_path = Path(__file__).parent.parent / "creatures" / f"{word}-lvl{level}.png"
                if img_path.exists():
                    cmd.extend(["-contentImage", str(img_path)])

            subprocess.run(cmd, capture_output=True, timeout=2)
            native_sent = True
        except Exception:
            pass

    # 2. Send to web dashboard
    try:
        data = json.dumps({
            "type": notif_type,
            "title": title,
            "message": message,
            "word": word,
            "level": level,
            "native_sent": native_sent
        }).encode()

        req = urllib.request.Request(
            f"http://localhost:{DEFAULT_PORT}/api/notify",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=1)
    except (urllib.error.URLError, Exception):
        pass


def notify_async(title, message, word=None, level=None, notif_type="info"):
    """Send a notification in background without blocking."""
    def _notify():
        notify(title, message, word, level, notif_type)
    threading.Thread(target=_notify, daemon=True).start()


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
            notif_type="new"
        )

    elif just_hatched:
        # Egg hatched!
        notify_async(
            _("notifications.hatched_title", word=word),
            _("notifications.hatched_msg", level=new_level),
            word=word,
            level=1,
            notif_type="hatched"
        )

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
            notif_type="evolved"
        )
