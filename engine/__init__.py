"""
Claudemon Engine - Game engine daemon for processing catches.

Architecture:
    wrapper.py → ~/.claudemon/catches.jsonl → engine.py → storage → notifications

Storage backends:
    - LocalStorage: SQLite (~/.claudemon/claudemon.db)
    - CloudStorage: Platform API (CLAUDEMON_API_KEY required)
"""

from engine.storage import get_storage, ConfigError, CatchResult, LocalStorage, CloudStorage
from engine.notifications import notify_catch, notify_async

__all__ = [
    "get_storage",
    "ConfigError",
    "CatchResult",
    "LocalStorage",
    "CloudStorage",
    "notify_catch",
    "notify_async",
]
