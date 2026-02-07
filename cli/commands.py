"""
commands.py - CLI command implementations for Claudemon.
"""

import json
import os
import random
import sys
import webbrowser
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.storage import get_storage, CloudStorage, ConfigError

SAAS_URL = "https://claudemon.zwyk-studio.com"

# i18n - load locale from JSON
LOCALE_DIR = Path(__file__).parent.parent / "locales"
LOCALE = os.environ.get("CLAUDEMON_LANG", "en")

# Display
EVOLUTION_NAMES = {1: "LARVA", 20: "SPAWN", 40: "BEAST", 60: "APEX", 80: "OMEGA", 100: "???"}
B = "\033[1m"
D = "\033[2m"
R = "\033[0m"
RED = "\033[31m"
GRN = "\033[32m"
YLW = "\033[33m"
CYN = "\033[36m"
MAG = "\033[35m"

STAGE_STYLE = {1: D, 20: GRN, 40: CYN, 60: YLW, 80: MAG, 100: RED}

# Enable ANSI escape sequences on Windows 10+
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7
        )
    except Exception:
        pass


def _no_color():
    return os.environ.get("NO_COLOR") is not None or not sys.stdout.isatty()


def _c(code, text):
    if _no_color():
        return str(text)
    return f"{code}{text}{R}"


def load_locale(lang="en"):
    locale_file = LOCALE_DIR / f"{lang}.json"
    if not locale_file.exists():
        locale_file = LOCALE_DIR / "en.json"
    if locale_file.exists():
        with open(locale_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


STRINGS = load_locale(LOCALE)


def _(key, **kwargs):
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


def _get_storage():
    try:
        return get_storage()
    except ConfigError as e:
        print(f"\n  {_c(RED, 'Configuration error:')}\n  {e}\n", file=sys.stderr)
        sys.exit(1)


def _check_cloud_error(storage):
    """Print cloud error details and exit if last request failed."""
    err = getattr(storage, "last_error", None)
    if err:
        print(f"\n  {_c(RED, 'Cloud API error:')} {err}")
    else:
        print(f"\n  {_c(RED, 'Could not reach the cloud platform.')}")
    print()
    sys.exit(1)


def _format_stage(stage):
    name = EVOLUTION_NAMES.get(stage, "LARVA")
    color = STAGE_STYLE.get(stage, D)
    return _c(color, name)


def _format_creature_line(c, i=None):
    word = c["word"]
    level = c.get("level", 1)
    catches = c.get("times_caught", 1)
    stage = c.get("evolution_stage", 1)
    is_egg = c.get("is_egg", catches < 3)
    in_team = c.get("in_team", False)

    prefix = f"  {i:>2}. " if i is not None else "  "
    tag = _c(YLW, " EGG") if is_egg else ""
    team = _c(CYN, " TEAM") if in_team else ""

    return f"{prefix}{_c(B, word):<32} Lv.{level:<4} {catches:>3}x  {_format_stage(stage):<14}{tag}{team}"


def _progress_bar(progress, width=8):
    filled = int(progress * width)
    return _c(D, "[") + _c(GRN, "=" * filled) + _c(D, "·" * (width - filled) + "]")


def show_stats():
    storage = _get_storage()
    stats = storage.get_stats()
    if stats is None:
        _check_cloud_error(storage)
    if not stats:
        print(f"\n  {_c(D, 'No stats yet. Start using Claude to catch some!')}")
        print()
        return

    discovered = stats.get("total_discovered", 0)
    hatched = stats.get("total_hatched", 0)
    eggs = stats.get("total_eggs", 0)
    catches = stats.get("total_catches", 0)
    max_lvl = stats.get("max_level", 0)
    team = stats.get("team_size", 0)
    max_team = stats.get("max_team_size", 6)

    print()
    print(f"  {_c(B, 'CLAUDEMON STATS')}")
    print(f"  {'─' * 30}")
    print(f"  {_('stats.discovered'):<20} {_c(B, discovered)}")
    print(f"  {'Hatched':<20} {hatched}  {_c(D, f'(+{eggs} eggs)')}")
    print(f"  {_('stats.total_catches'):<20} {catches}")
    print(f"  {_('stats.max_level'):<20} {max_lvl}")
    print(f"  {'Team':<20} {team}/{max_team}")
    print()


def show_list():
    storage = _get_storage()
    claudemons = storage.get_all()

    if claudemons is None:
        _check_cloud_error(storage)

    if not claudemons:
        print(f"\n  {_c(D, _('cli.no_claudemon'))}")
        print(f"  {_c(D, 'Start using Claude to catch some!')}")
        print()
        return

    hatched = [c for c in claudemons if not c.get("is_egg", False)]
    eggs = [c for c in claudemons if c.get("is_egg", False)]

    print()
    if hatched:
        print(f"  {_c(B, 'COLLECTION')}  {_c(D, f'({len(hatched)} hatched)')}")
        print(f"  {'─' * 50}")
        for c in hatched:
            print(_format_creature_line(c))
        print()

    if eggs:
        print(f"  {_c(YLW, 'EGGS')}  {_c(D, f'({len(eggs)} incubating)')}")
        print(f"  {'─' * 50}")
        for c in eggs:
            progress = c.get("hatch_progress", 0)
            bar = _progress_bar(progress)
            print(f"{_format_creature_line(c)}  {bar}")
        print()

    print(f"  {_c(D, f'Total: {len(claudemons)} discovered')}")
    print()


def open_dashboard():
    mode = os.environ.get("CLAUDEMON_MODE", "").lower().strip()
    base = os.environ.get("CLAUDEMON_CLOUD_URL", SAAS_URL).rstrip("/")
    if mode == "local":
        print(f"\n  {_c(YLW, 'Dashboard is not available in local mode.')}")
        print(f"  {_c(D, f'Switch to cloud mode to access: {base}/dashboard')}")
        print()
        return
    url = f"{base}/dashboard"
    print(f"  Opening {_c(CYN, url)}...")
    webbrowser.open(url)
