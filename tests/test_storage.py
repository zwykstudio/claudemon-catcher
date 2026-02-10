"""
tests/test_storage.py - Tests for storage: config routing, CloudStorage
                        connection pooling, retry, CatchResult.
"""

import http.client
from unittest.mock import MagicMock, patch

import pytest

# ===========================================================================
# Config routing
# ===========================================================================

class TestGetStorage:
    """get_storage() mode selection."""

    def test_get_storage_cloud(self, monkeypatch):
        monkeypatch.delenv("CLAUDEMON_MODE", raising=False)
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_testkey123")
        from engine.storage import CloudStorage, get_storage
        s = get_storage()
        assert isinstance(s, CloudStorage)

    def test_get_storage_local(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CLAUDEMON_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDEMON_MODE", "local")
        # Redirect DB so LocalStorage.__init__ doesn't touch real DB
        import engine.database as db
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        from engine.storage import LocalStorage, get_storage
        s = get_storage()
        assert isinstance(s, LocalStorage)

    def test_get_storage_errors(self, monkeypatch):
        from engine.storage import ConfigError, get_storage

        # No key, cloud mode → error
        monkeypatch.delenv("CLAUDEMON_MODE", raising=False)
        monkeypatch.delenv("CLAUDEMON_API_KEY", raising=False)
        with pytest.raises(ConfigError):
            get_storage()

        # local + key → error
        monkeypatch.setenv("CLAUDEMON_MODE", "local")
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_x")
        with pytest.raises(ConfigError):
            get_storage()

        # Unknown mode → error
        monkeypatch.setenv("CLAUDEMON_MODE", "banana")
        monkeypatch.delenv("CLAUDEMON_API_KEY", raising=False)
        with pytest.raises(ConfigError):
            get_storage()


# ===========================================================================
# CloudStorage connection pooling
# ===========================================================================

class TestCloudStoragePooling:
    """Fix #1: CloudStorage reuses HTTP connections."""

    def test_init_parses_url(self, monkeypatch):
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        monkeypatch.setenv("CLAUDEMON_CLOUD_URL", "https://api.example.com:8443")
        from engine.storage import CloudStorage
        cs = CloudStorage()
        assert cs._scheme == "https"
        assert cs._host == "api.example.com"
        assert cs._port == 8443
        assert cs._conn is None

    def test_get_conn_creates_https(self, monkeypatch):
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        monkeypatch.setenv("CLAUDEMON_CLOUD_URL", "https://example.com")
        from engine.storage import CloudStorage
        cs = CloudStorage()
        conn = cs._get_conn()
        assert isinstance(conn, http.client.HTTPSConnection)
        assert cs._conn is conn

    def test_get_conn_creates_http(self, monkeypatch):
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        monkeypatch.setenv("CLAUDEMON_CLOUD_URL", "http://localhost:9000")
        from engine.storage import CloudStorage
        cs = CloudStorage()
        conn = cs._get_conn()
        assert isinstance(conn, http.client.HTTPConnection)
        assert not isinstance(conn, http.client.HTTPSConnection)

    def test_get_conn_reuses_connection(self, monkeypatch):
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        from engine.storage import CloudStorage
        cs = CloudStorage()
        conn1 = cs._get_conn()
        conn2 = cs._get_conn()
        assert conn1 is conn2

    def test_close_conn_resets(self, monkeypatch):
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        from engine.storage import CloudStorage
        cs = CloudStorage()
        cs._get_conn()
        assert cs._conn is not None
        cs._close_conn()
        assert cs._conn is None

    def test_request_closes_conn_on_error(self, monkeypatch):
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        from engine.storage import CloudStorage
        cs = CloudStorage()
        mock_conn = MagicMock()
        mock_conn.request.side_effect = OSError("Connection refused")
        cs._conn = mock_conn
        result = cs._request("GET", "/test")
        assert result is None
        assert cs._conn is None  # was reset
        assert "Connection refused" in cs.last_error


# ===========================================================================
# CloudStorage retry
# ===========================================================================

class TestCloudStorageRetry:
    """Fix #2: _request_with_retry retries on 5xx and connection errors."""

    def _make_storage(self, monkeypatch):
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        from engine.storage import CloudStorage
        return CloudStorage()

    def test_retry_succeeds_after_5xx(self, monkeypatch):
        cs = self._make_storage(monkeypatch)
        mock_conn = MagicMock()
        # First call: 500, second call: 200
        resp_500 = MagicMock(status=500)
        resp_500.read.return_value = b""
        resp_200 = MagicMock(status=200)
        resp_200.read.return_value = b'{"ok": true}'
        mock_conn.getresponse.side_effect = [resp_500, resp_200]
        # Patch _get_conn so fresh-connection-per-retry still returns our mock
        with patch.object(cs, "_get_conn", return_value=mock_conn):
            with patch("time.sleep"):
                result = cs._request_with_retry("POST", "/api/v1/sync", {"word": "Test"})
        assert result == {"ok": True}

    def test_retry_gives_up_on_4xx(self, monkeypatch):
        cs = self._make_storage(monkeypatch)
        mock_conn = MagicMock()
        resp_400 = MagicMock(status=400)
        resp_400.read.return_value = b'{"error": "bad request"}'
        mock_conn.getresponse.return_value = resp_400
        cs._conn = mock_conn

        result = cs._request_with_retry("POST", "/test", max_retries=3)
        assert result is None
        # Should NOT have retried — only 1 call
        assert mock_conn.request.call_count == 1

    def test_retry_on_connection_error(self, monkeypatch):
        cs = self._make_storage(monkeypatch)
        mock_conn = MagicMock()
        mock_conn.request.side_effect = ConnectionError("reset")
        # Patch _get_conn so it always returns our mock (even after _close_conn resets _conn)
        with patch.object(cs, "_get_conn", return_value=mock_conn):
            with patch("time.sleep"):
                result = cs._request_with_retry("GET", "/test", max_retries=2)
        assert result is None
        # 1 initial + 2 retries = 3 attempts
        assert mock_conn.request.call_count == 3

    def test_catch_uses_retry(self, monkeypatch):
        """catch() should use _request_with_retry, not plain _request."""
        cs = self._make_storage(monkeypatch)
        with patch.object(cs, "_request_with_retry", return_value={"is_new": True, "new_level": 1}) as mock:
            result = cs.catch("Testing")
            mock.assert_called_once()
            assert result is not None
            assert result.word == "Testing"

    def test_get_creature_uses_plain_request(self, monkeypatch):
        """GET endpoints should use plain _request (no retry)."""
        cs = self._make_storage(monkeypatch)
        with patch.object(cs, "_request", return_value={"word": "X", "level": 1}) as mock_plain:
            with patch.object(cs, "_request_with_retry") as mock_retry:
                cs.get_creature("X")
                mock_plain.assert_called_once()
                mock_retry.assert_not_called()


# ===========================================================================
# CatchResult and retry delay cap
# ===========================================================================

class TestCatchResultXpEarned:
    """CatchResult includes xp_earned field."""

    def test_from_dict_with_xp(self):
        from engine.storage import CatchResult
        r = CatchResult.from_dict("Test", {"is_new": True, "new_level": 1, "xp_earned": 42})
        assert r.xp_earned == 42
        assert r.word == "Test"

    def test_from_dict_defaults_xp_zero(self):
        from engine.storage import CatchResult
        r = CatchResult.from_dict("Test", {})
        assert r.xp_earned == 0


class TestRetryDelayCap:
    """_request_with_retry caps retry delay at 30s."""

    def test_retry_after_capped_at_30(self, monkeypatch):
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        from engine.storage import CloudStorage
        cs = CloudStorage()

        mock_conn = MagicMock()
        resp_429 = MagicMock(status=429)
        resp_429.read.return_value = b'{"error": "rate limited"}'
        resp_429.getheader.return_value = "86400"  # 24h!

        resp_200 = MagicMock(status=200)
        resp_200.read.return_value = b'{"ok": true}'
        mock_conn.getresponse.side_effect = [resp_429, resp_200]

        sleep_values = []

        def track_sleep(s):
            sleep_values.append(s)

        with patch.object(cs, "_get_conn", return_value=mock_conn):
            with patch("time.sleep", side_effect=track_sleep):
                result = cs._request_with_retry("POST", "/test", max_retries=1)

        assert result == {"ok": True}
        assert all(s <= 30 for s in sleep_values), f"Delay exceeded 30s cap: {sleep_values}"

    def test_429_retries(self, monkeypatch):
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        from engine.storage import CloudStorage
        cs = CloudStorage()

        mock_conn = MagicMock()
        resp_429 = MagicMock(status=429)
        resp_429.read.return_value = b'{"error": "too many"}'
        resp_429.getheader.return_value = None
        mock_conn.getresponse.return_value = resp_429

        with patch.object(cs, "_get_conn", return_value=mock_conn):
            with patch("time.sleep"):
                result = cs._request_with_retry("POST", "/test", max_retries=2)

        assert result is None
        # 1 initial + 2 retries = 3 attempts
        assert mock_conn.request.call_count == 3
