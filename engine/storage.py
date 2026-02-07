"""
storage.py - Storage adapter for Claudemon engine.

Provides a unified interface for local (SQLite) and cloud (Platform API) modes.

Mode logic:
    - Default = cloud: requires CLAUDEMON_API_KEY (sk_claudemon_...)
    - Local = opt-in: CLAUDEMON_MODE=local
    - API key + local mode = error (pick one)

Usage:
    from engine.storage import get_storage, ConfigError
    storage = get_storage()
    result = storage.catch("Zigzagging")
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

SAAS_URL = "https://claudemon.zwyk-studio.com"


class ConfigError(Exception):
    """Raised when Claudemon configuration is invalid or incomplete."""
    pass


@dataclass
class CatchResult:
    """Unified result from a catch operation."""
    word: str
    is_new: bool
    new_level: int
    evolved: bool
    just_hatched: bool
    is_egg: bool

    @classmethod
    def from_dict(cls, word: str, d: dict) -> "CatchResult":
        return cls(
            word=word,
            is_new=d.get("is_new", False),
            new_level=d.get("new_level", 1),
            evolved=d.get("evolved", False),
            just_hatched=d.get("just_hatched", False),
            is_egg=d.get("is_egg", True),
        )


class LocalStorage:
    """SQLite-based local storage."""

    def __init__(self):
        from engine.database import init_db
        init_db()

    def catch(self, word: str, ts: float = None, proof: str = None, sid: str = None, duration: float = None) -> CatchResult:
        from engine.database import catch_word
        result = catch_word(word)
        return CatchResult.from_dict(word, result)

    def get_creature(self, word: str) -> Optional[dict]:
        from engine.database import get_claudemon
        return get_claudemon(word)

    def get_stats(self) -> dict:
        from engine.database import get_stats
        return get_stats()

    def get_all(self) -> list:
        from engine.database import get_all_claudemons
        return get_all_claudemons()

    def get_team(self) -> list:
        from engine.database import get_team
        return get_team()

    def add_to_team(self, word: str) -> tuple[bool, str]:
        from engine.database import add_to_team
        return add_to_team(word)

    def remove_from_team(self, word: str) -> bool:
        from engine.database import remove_from_team
        return remove_from_team(word)


class CloudStorage:
    """Cloud platform storage via API."""

    def __init__(self):
        self.base_url = os.environ.get(
            "CLAUDEMON_CLOUD_URL", SAAS_URL
        ).rstrip("/")
        self.api_key = os.environ.get("CLAUDEMON_API_KEY", "")
        self.timeout = 5
        self.last_error = None

    def _request(self, method: str, path: str, data: dict = None) -> Optional[dict]:
        """Make an authenticated API request."""
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self.last_error = None
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            self.last_error = f"HTTP {e.code} on {method} {path}"
            return None
        except (urllib.error.URLError, Exception) as e:
            self.last_error = f"{method} {path}: {e}"
            return None

    def catch(self, word: str, ts: float = None, proof: str = None, sid: str = None, duration: float = None) -> Optional[CatchResult]:
        payload = {"word": word}
        if ts is not None:
            payload["ts"] = ts
        if proof is not None:
            payload["proof"] = proof
        if sid is not None:
            payload["sid"] = sid
        if duration is not None:
            payload["duration"] = round(duration, 3)
        result = self._request("POST", "/api/v1/sync", payload)
        if result is None:
            return None
        return CatchResult.from_dict(word, result)

    def get_creature(self, word: str) -> Optional[dict]:
        return self._request("GET", f"/api/v1/claudemons/{word}")

    def get_stats(self) -> Optional[dict]:
        return self._request("GET", "/api/v1/stats")

    def get_all(self) -> Optional[list]:
        result = self._request("GET", "/api/v1/claudemons")
        return result.get("claudemons", []) if result else None

    def get_team(self) -> Optional[list]:
        result = self._request("GET", "/api/v1/team")
        return result.get("team", []) if result else None

    def add_to_team(self, word: str) -> tuple[bool, str]:
        result = self._request("POST", "/api/v1/team", {"action": "add", "word": word})
        if result is None:
            return False, "Cloud unavailable"
        return result.get("success", False), result.get("message", "")

    def remove_from_team(self, word: str) -> bool:
        result = self._request("POST", "/api/v1/team", {"action": "remove", "word": word})
        return result.get("success", False) if result else False


def get_storage() -> LocalStorage | CloudStorage:
    """Return the appropriate storage backend.

    Mode logic:
        - No mode set (or empty) → cloud mode, requires CLAUDEMON_API_KEY
        - CLAUDEMON_MODE=local → local mode, refuses if API key is also set
        - Any other value → error

    Raises:
        ConfigError: If configuration is invalid or incomplete.
    """
    mode = os.environ.get("CLAUDEMON_MODE", "").lower().strip()
    api_key = os.environ.get("CLAUDEMON_API_KEY", "").strip()

    if mode == "local":
        if api_key:
            raise ConfigError(
                "CLAUDEMON_API_KEY is set but CLAUDEMON_MODE=local.\n"
                "Pick one: remove the API key for local mode, or remove CLAUDEMON_MODE for cloud."
            )
        return LocalStorage()

    if mode and mode != "cloud":
        raise ConfigError(
            f"Unknown CLAUDEMON_MODE='{mode}'.\n"
            "Valid options: remove CLAUDEMON_MODE (cloud, default) or CLAUDEMON_MODE=local"
        )

    # Cloud mode (default)
    if not api_key or not api_key.startswith("sk_claudemon_"):
        raise ConfigError(
            "Claudemon cloud mode requires an API key.\n"
            f"  1. Get your key at {SAAS_URL}/dashboard/settings\n"
            "  2. Export it:  export CLAUDEMON_API_KEY=sk_claudemon_...\n"
            "\n"
            "For local-only mode (no cloud sync):\n"
            "  export CLAUDEMON_MODE=local"
        )

    return CloudStorage()
