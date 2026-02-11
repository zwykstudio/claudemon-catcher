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

import http.client
import json
import os
import ssl
import time

try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()
import urllib.error
import urllib.parse
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
    xp_earned: int

    @classmethod
    def from_dict(cls, word: str, d: dict) -> "CatchResult":
        return cls(
            word=word,
            is_new=d.get("is_new", False),
            new_level=d.get("new_level", 1),
            evolved=d.get("evolved", False),
            just_hatched=d.get("just_hatched", False),
            is_egg=d.get("is_egg", True),
            xp_earned=d.get("xp_earned", 0),
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
        # Connection pooling
        parsed = urllib.parse.urlparse(self.base_url)
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port
        self._conn = None

    def _get_conn(self) -> http.client.HTTPConnection:
        """Return a persistent HTTP(S) connection, creating one if needed."""
        if self._conn is not None:
            return self._conn
        if self._scheme == "https":
            self._conn = http.client.HTTPSConnection(
                self._host, self._port, timeout=self.timeout,
                context=_SSL_CONTEXT,
            )
        else:
            self._conn = http.client.HTTPConnection(
                self._host, self._port, timeout=self.timeout,
            )
        return self._conn

    def _close_conn(self):
        """Close and discard the current connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _request(self, method: str, path: str, data: dict = None) -> Optional[dict]:
        """Make an authenticated API request using a persistent connection."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = json.dumps(data).encode() if data else None
        try:
            conn = self._get_conn()
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            resp_body = resp.read()
            if 200 <= resp.status < 300:
                self.last_error = None
                return json.loads(resp_body.decode())
            self.last_error = f"HTTP {resp.status} on {method} {path}"
            return None
        except (http.client.HTTPException, OSError, ConnectionError) as e:
            self.last_error = f"{method} {path}: {e}"
            self._close_conn()
            return None

    def _request_with_retry(self, method: str, path: str, data: dict = None, max_retries: int = 3) -> Optional[dict]:
        """_request with exponential backoff for transient errors (5xx, 429, timeout, connection)."""
        debug = os.environ.get("CLAUDEMON_DEBUG")
        for attempt in range(max_retries + 1):
            # Fresh connection on each attempt to avoid stale connections
            if attempt > 0:
                self._close_conn()
            try:
                conn = self._get_conn()
                body = json.dumps(data).encode() if data else None
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                conn.request(method, path, body=body, headers=headers)
                conn.sock.settimeout(self.timeout)
                resp = conn.getresponse()
                resp_body = resp.read()
                if 200 <= resp.status < 300:
                    self.last_error = None
                    return json.loads(resp_body.decode())
                # Parse error body for better messages
                try:
                    err_json = json.loads(resp_body.decode())
                    err_reason = err_json.get("error", resp_body.decode()[:200])
                except Exception:
                    err_reason = resp_body.decode()[:200]
                if (resp.status >= 500 or resp.status == 429) and attempt < max_retries:
                    retry_after = resp.getheader("Retry-After")
                    delay = min(float(retry_after) if retry_after and retry_after.isdigit() else 0.5 * (2 ** attempt), 30)
                    if debug:
                        print(f"[cloud] {resp.status} on {method} {path}: {err_reason} — retry in {delay:.1f}s ({attempt+1}/{max_retries})")
                    self._close_conn()
                    time.sleep(delay)
                    continue
                self.last_error = f"HTTP {resp.status} on {method} {path}: {err_reason}"
                return None
            except (http.client.HTTPException, OSError, ConnectionError, TimeoutError) as e:
                self.last_error = f"{method} {path}: {e}"
                self._close_conn()
                if debug:
                    print(f"[cloud] Connection error on {method} {path}: {e} — retry {attempt+1}/{max_retries}")
                if attempt < max_retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                return None
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
        result = self._request_with_retry("POST", "/api/v1/sync", payload)
        if result is None:
            return None
        if os.environ.get("CLAUDEMON_DEBUG"):
            print(f"[cloud] sync response for {word}: {json.dumps(result, default=str)[:300]}")
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
