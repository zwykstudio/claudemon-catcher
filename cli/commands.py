"""
commands.py - CLI command implementations for Claudemon.
"""

import json
import os
import sys
import webbrowser
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.database import get_all_claudemons, get_stats


# i18n - load locale from JSON
LOCALE_DIR = Path(__file__).parent.parent / "locales"
LOCALE = os.environ.get("CLAUDEMON_LANG", "en")


def load_locale(lang="en"):
    """Load locale strings from JSON file."""
    locale_file = LOCALE_DIR / f"{lang}.json"
    if not locale_file.exists():
        locale_file = LOCALE_DIR / "en.json"
    if locale_file.exists():
        with open(locale_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


STRINGS = load_locale(LOCALE)


def _(key, **kwargs):
    """Get translated string with optional formatting."""
    import random

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


def show_stats():
    """Display statistics."""
    stats = get_stats()
    print(f"\n{'='*40}")
    print("  CLAUDEMON STATS")
    print(f"{'='*40}")
    print(f"  {_('stats.discovered')}: {stats['total_discovered']}")
    print(f"  {_('stats.total_catches')}: {stats['total_catches']}")
    print(f"  {_('stats.max_level')}: {stats['max_level']}")
    print()


def show_list():
    """List all claudemons."""
    claudemons = get_all_claudemons()
    if not claudemons:
        print(_("cli.no_claudemon"))
        return

    print(f"\n{'='*50}")
    print(f"  {'CLAUDEMON':<20} {'LVL':>5} {'CATCHES':>8} {'STAGE':>6}")
    print(f"{'='*50}")
    for c in claudemons:
        print(f"  {c['word']:<20} {c['level']:>5} {c['times_caught']:>8} {c['evolution_stage']:>6}")
    print()


def serve_web():
    """Start a web server to view the collection."""
    from server.server import run_server
    run_server()


def open_dashboard():
    """Open the dashboard in the default browser."""
    DEFAULT_PORT = 17712
    url = f"http://localhost:{DEFAULT_PORT}"
    print(f"Opening {url}...")
    webbrowser.open(url)
