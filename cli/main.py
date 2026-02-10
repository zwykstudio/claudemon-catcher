#!/usr/bin/env python3
"""
Claudemon Catcher — gotta catch 'em all, while you code.

Usage:
    cc "your prompt"              Use Claude — catches happen automatically
    cc --stats                    Show your stats
    cc --list                     List all your creatures
    cc --dashboard, -d            Open cloud dashboard in browser
    cc --install-statusline       Configure the Claude Code statusline

Engine management:
    cc engine                     Show engine daemon status
    cc engine restart             Restart the engine daemon
    cc engine update-key [KEY]    Update API key in daemon config
    cc engine reset               Reset engine state and restart
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.commands import open_dashboard, show_list, show_stats


def main():
    """Main CLI entry point."""
    args = sys.argv[1:]

    if "--stats" in args:
        show_stats()
    elif "--list" in args:
        show_list()
    elif "--dashboard" in args or "-d" in args:
        open_dashboard()
    elif "--help" in args or "-h" in args:
        print(__doc__)
    else:
        print("Usage: cc [--stats | --list | --dashboard | engine | --help]")
        print("Run cc --help for all commands.")


if __name__ == "__main__":
    main()
