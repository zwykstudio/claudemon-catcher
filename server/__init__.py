"""
Claudemon Server - Local web server for the dashboard.

Provides:
- HTTP API for claudemon data
- Static file serving for the frontend
- Real-time notifications
"""

from server.server import run_server, DEFAULT_PORT

__all__ = ["run_server", "DEFAULT_PORT"]
