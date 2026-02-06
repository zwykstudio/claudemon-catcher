#!/usr/bin/env python3
"""
main.py - CLI entry point for Claudemon.

Usage:
    cc --stats       Show statistics
    cc --list        List all claudemons
    cc --dashboard   Open web dashboard in browser
    cc --serve       Start the web server

For capturing words from Claude CLI, use wrapper.py instead.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.commands import show_stats, show_list, open_dashboard, serve_web


def main():
    """Main CLI entry point."""
    args = sys.argv[1:]

    if "--stats" in args:
        show_stats()
    elif "--list" in args:
        show_list()
    elif "--dashboard" in args or "-d" in args:
        open_dashboard()
    elif "--serve" in args:
        serve_web()
    elif "--help" in args or "-h" in args:
        print(__doc__)
    else:
        print("Usage: cc [--stats|--list|--dashboard|--serve]")
        print("For Claude CLI capture, use: python wrapper.py [claude args...]")


if __name__ == "__main__":
    main()
