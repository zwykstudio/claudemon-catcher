"""
Claudemon CLI - Command-line interface tools.

Provides commands for:
- Viewing statistics
- Listing claudemons
- Opening the dashboard
- Starting the web server
"""

from cli.commands import show_stats, show_list, open_dashboard, serve_web

__all__ = ["show_stats", "show_list", "open_dashboard", "serve_web"]
