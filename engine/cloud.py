"""
cloud.py - Cloud sync module for Claudemon

Handles synchronization of captured words to the Claudemon cloud platform.
Supports automatic fallback to local storage if cloud is unavailable.

Environment variables:
    CLAUDEMON_MODE: "local" (default) or "cloud"
    CLAUDEMON_CLOUD_URL: Platform URL (default: https://claudemon.zwyk-studio.com)
    CLAUDEMON_API_KEY: Your API key (sk_claudemon_...)
"""

import json
import os
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

# Configuration from environment
CLAUDEMON_MODE = os.environ.get("CLAUDEMON_MODE", "local")
CLAUDEMON_CLOUD_URL = os.environ.get(
    "CLAUDEMON_CLOUD_URL", "https://claudemon.zwyk-studio.com"
)
CLAUDEMON_API_KEY = os.environ.get("CLAUDEMON_API_KEY", "")

# Timeout for cloud requests (seconds)
CLOUD_TIMEOUT = 5


@dataclass
class SyncResult:
    """Result from cloud sync operation."""
    word: str
    is_new: bool
    new_level: int
    times_caught: int
    evolution_stage: int
    evolved: bool
    just_hatched: bool
    is_egg: bool
    hatch_progress: float

    @classmethod
    def from_dict(cls, data: dict) -> "SyncResult":
        return cls(
            word=data["word"],
            is_new=data["is_new"],
            new_level=data["new_level"],
            times_caught=data["times_caught"],
            evolution_stage=data["evolution_stage"],
            evolved=data["evolved"],
            just_hatched=data["just_hatched"],
            is_egg=data["is_egg"],
            hatch_progress=data["hatch_progress"],
        )

    def to_local_format(self) -> dict:
        """Convert to format expected by local database functions."""
        return {
            "is_new": self.is_new,
            "new_level": self.new_level,
            "times_caught": self.times_caught,
            "evolution_stage": self.evolution_stage,
            "evolved": self.evolved,
            "just_hatched": self.just_hatched,
        }


def is_cloud_mode() -> bool:
    """Check if cloud mode is enabled and properly configured."""
    return (
        CLAUDEMON_MODE.lower() == "cloud"
        and bool(CLAUDEMON_API_KEY)
        and CLAUDEMON_API_KEY.startswith("sk_claudemon_")
    )


def get_cloud_url() -> str:
    """Get the configured cloud URL."""
    return CLAUDEMON_CLOUD_URL.rstrip("/")


def sync_word_to_cloud(word: str) -> Optional[SyncResult]:
    """
    Sync a captured word to the cloud platform.

    Args:
        word: The captured word to sync

    Returns:
        SyncResult if successful, None if failed (should fallback to local)
    """
    if not is_cloud_mode():
        return None

    url = f"{get_cloud_url()}/api/v1/sync"

    try:
        data = json.dumps({"word": word}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {CLAUDEMON_API_KEY}",
        }

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=CLOUD_TIMEOUT) as response:
            result_data = json.loads(response.read().decode("utf-8"))
            return SyncResult.from_dict(result_data)

    except urllib.error.HTTPError as e:
        # Log error but don't crash - fallback to local
        if os.environ.get("CLAUDEMON_DEBUG"):
            import sys
            sys.stderr.write(f"Cloud sync HTTP error: {e.code} {e.reason}\n")
        return None

    except urllib.error.URLError as e:
        # Network error - fallback to local
        if os.environ.get("CLAUDEMON_DEBUG"):
            import sys
            sys.stderr.write(f"Cloud sync network error: {e.reason}\n")
        return None

    except Exception as e:
        # Any other error - fallback to local
        if os.environ.get("CLAUDEMON_DEBUG"):
            import sys
            sys.stderr.write(f"Cloud sync error: {e}\n")
        return None


def fetch_stats() -> Optional[dict]:
    """
    Fetch stats from cloud platform.

    Returns:
        Stats dict if successful, None if failed
    """
    if not is_cloud_mode():
        return None

    url = f"{get_cloud_url()}/api/v1/stats"

    try:
        headers = {"Authorization": f"Bearer {CLAUDEMON_API_KEY}"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=CLOUD_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def fetch_claudemons() -> Optional[list]:
    """
    Fetch all claudemons from cloud platform.

    Returns:
        List of claudemons if successful, None if failed
    """
    if not is_cloud_mode():
        return None

    url = f"{get_cloud_url()}/api/v1/claudemons"

    try:
        headers = {"Authorization": f"Bearer {CLAUDEMON_API_KEY}"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=CLOUD_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("claudemons", [])
    except Exception:
        return None


def fetch_team() -> Optional[list]:
    """
    Fetch team from cloud platform.

    Returns:
        List of team members if successful, None if failed
    """
    if not is_cloud_mode():
        return None

    url = f"{get_cloud_url()}/api/v1/team"

    try:
        headers = {"Authorization": f"Bearer {CLAUDEMON_API_KEY}"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=CLOUD_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("team", [])
    except Exception:
        return None


# Export check for easy imports
__all__ = [
    "is_cloud_mode",
    "sync_word_to_cloud",
    "fetch_stats",
    "fetch_claudemons",
    "fetch_team",
    "SyncResult",
    "CLAUDEMON_MODE",
    "CLAUDEMON_CLOUD_URL",
    "CLAUDEMON_API_KEY",
]
