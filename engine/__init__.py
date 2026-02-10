"""
Claudemon Engine - Game engine daemon for processing catches.

Architecture:
    wrapper.py → ~/.claudemon/catches.jsonl → engine.py → storage → notifications

Storage backends:
    - LocalStorage: SQLite (~/.claudemon/claudemon.db)
    - CloudStorage: Platform API (CLAUDEMON_API_KEY required)
"""

from engine.notifications import notify_async, notify_catch
from engine.storage import CatchResult, CloudStorage, ConfigError, LocalStorage, get_storage

__all__ = [
    "get_storage",
    "ConfigError",
    "CatchResult",
    "LocalStorage",
    "CloudStorage",
    "notify_catch",
    "notify_async",
]
