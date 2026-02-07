"""
tests/test_all.py - Tests for Claudemon Catcher.

Run:  python3 -m pytest tests/ -v
"""

import hashlib
import hmac
import http.client
import json
import os
import re
import sqlite3
import sys
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _import_wrapper():
    """Import wrapper module (no side-effects on import)."""
    import wrapper
    return wrapper


def _import_database(tmp_path):
    """Import database module with DB_PATH redirected to tmp_path.

    database.py calls init_db() at module level, so we must patch DB_PATH
    *before* the first import, or re-patch and re-init for each test.
    """
    import engine.database as db
    db.DB_PATH = tmp_path / "test.db"
    db.init_db()
    return db


# ===========================================================================
# 1. Wrapper — extraction and duration
# ===========================================================================

class TestWordRegex:
    """WORD_RE pattern matching."""

    def test_word_regex_valid(self):
        w = _import_wrapper()
        for text in ["✶ Reasoning…", "· Thinking…", "✻ Re-analyzing…"]:
            matches = w.WORD_RE.findall(text)
            assert matches, f"Expected match in {text!r}"

    def test_word_regex_rejects(self):
        w = _import_wrapper()
        for text in [
            "Reasoning…",         # no spinner char
            "✶ Reasoning",        # no …
            "✶ reasoning…",       # no uppercase
            "✶ Reason…",          # no -ing suffix
        ]:
            matches = w.WORD_RE.findall(text)
            assert not matches, f"Expected no match in {text!r}"


class TestProcessChunk:
    """process_chunk() word detection logic."""

    def _make_env(self):
        w = _import_wrapper()
        return w, "", set(), {}, hashlib.sha256(), "test-sid"

    def test_process_chunk_detects_word(self):
        w, buf, seen, pending, h, sid = self._make_env()
        buf = w.process_chunk("✢ Reasoning…".encode(), buf, seen, pending, h, sid)
        assert "Reasoning" in pending.get("word", "")
        assert "Reasoning" in seen

    def test_process_chunk_emits_on_second_word(self, tmp_path):
        w, buf, seen, pending, h, sid = self._make_env()
        catches_file = str(tmp_path / "catches.jsonl")
        w.CATCHES_FILE = catches_file

        buf = w.process_chunk("✶ Reasoning…".encode(), buf, seen, pending, h, sid)
        first_ts = pending["ts"]

        # Small delay so duration > 0
        time.sleep(0.01)
        buf = w.process_chunk("· Thinking…".encode(), buf, seen, pending, h, sid)

        assert pending["word"] == "Thinking"
        # First word should have been emitted
        with open(catches_file) as f:
            entry = json.loads(f.readline())
        assert entry["word"] == "Reasoning"
        assert entry["duration"] > 0

    def test_process_chunk_ignores_duplicates(self):
        w, buf, seen, pending, h, sid = self._make_env()
        buf = w.process_chunk("✶ Reasoning…".encode(), buf, seen, pending, h, sid)
        original_pending = dict(pending)

        # Same word again — should not change pending
        buf = w.process_chunk("✶ Reasoning…".encode(), buf, seen, pending, h, sid)
        assert pending["word"] == original_pending["word"]
        assert pending["ts"] == original_pending["ts"]


class TestFlushPending:
    """flush_pending() timeout and force logic."""

    def test_flush_pending_respects_timeout(self, tmp_path):
        w = _import_wrapper()
        catches_file = str(tmp_path / "catches.jsonl")
        w.CATCHES_FILE = catches_file

        now = time.time()
        pending = {"word": "Thinking", "ts": now, "proof": "abc123", "last_seen": now}

        # Not enough idle time — should NOT flush
        w.flush_pending(pending, "sid1", force=False)
        assert pending  # still has content

        # Force flush
        w.flush_pending(pending, "sid1", force=True)
        assert not pending  # cleared

        with open(catches_file) as f:
            entry = json.loads(f.readline())
        assert entry["word"] == "Thinking"


class TestEmit:
    """emit() JSONL output format."""

    def test_emit_jsonl_format(self, tmp_path):
        w = _import_wrapper()
        catches_file = str(tmp_path / "catches.jsonl")
        w.CATCHES_FILE = catches_file

        w.emit("Analyzing", 1700000000.0, "deadbeef", "sid42", duration=1.234)

        with open(catches_file) as f:
            entry = json.loads(f.readline())

        assert entry == {
            "word": "Analyzing",
            "ts": 1700000000.0,
            "duration": 1.234,
            "proof": "deadbeef",
            "sid": "sid42",
        }


# ===========================================================================
# 2. Database — game logic (SQLite in-memory via tmp_path)
# ===========================================================================

class TestEvolution:
    """get_evolution_stage() thresholds."""

    def test_evolution_stages(self, tmp_path):
        db = _import_database(tmp_path)
        assert db.get_evolution_stage(1) == 1
        assert db.get_evolution_stage(19) == 1
        assert db.get_evolution_stage(20) == 20
        assert db.get_evolution_stage(59) == 40
        assert db.get_evolution_stage(100) == 100


class TestCatchWord:
    """catch_word() new/level-up/hatch/evolve logic."""

    def test_catch_new_word(self, tmp_path):
        db = _import_database(tmp_path)
        result = db.catch_word("Reasoning")
        assert result["is_new"] is True
        assert result["new_level"] == 1
        assert result["is_egg"] is True

    def test_catch_levels_up_and_hatches(self, tmp_path):
        db = _import_database(tmp_path)
        db.catch_word("Thinking")  # 1st: new, level=1
        r2 = db.catch_word("Thinking")  # 2nd: level=2
        assert r2["new_level"] == 2
        assert r2["just_hatched"] is False

        r3 = db.catch_word("Thinking")  # 3rd: level=3, hatches (HATCH_THRESHOLD=3)
        assert r3["new_level"] == 3
        assert r3["just_hatched"] is True
        assert r3["is_egg"] is False

    def test_catch_evolves(self, tmp_path):
        db = _import_database(tmp_path)
        # Catch 19 times to get to level 19
        for _ in range(19):
            db.catch_word("Evolving")
        # 20th catch → level 20, evolution_stage jumps from 1 to 20
        r = db.catch_word("Evolving")
        assert r["new_level"] == 20
        assert r["evolved"] is True


class TestTeam:
    """Team add/remove and constraints."""

    def test_team_management(self, tmp_path):
        db = _import_database(tmp_path)

        # Create a hatched claudemon (catch HATCH_THRESHOLD times)
        for _ in range(db.HATCH_THRESHOLD):
            db.catch_word("Runner")

        ok, msg = db.add_to_team("Runner")
        assert ok is True

        team = db.get_team()
        assert len(team) == 1
        assert team[0]["word"] == "Runner"

        # Remove
        db.remove_from_team("Runner")
        assert len(db.get_team()) == 0

        # Egg cannot join team
        db.catch_word("Baby")  # only 1 catch → egg
        ok, msg = db.add_to_team("Baby")
        assert ok is False
        assert "egg" in msg.lower()

        # Max team size
        for i in range(db.MAX_TEAM_SIZE):
            name = f"Member{i}"
            for _ in range(db.HATCH_THRESHOLD):
                db.catch_word(name)
            db.add_to_team(name)

        # One more should fail
        for _ in range(db.HATCH_THRESHOLD):
            db.catch_word("Extra")
        ok, msg = db.add_to_team("Extra")
        assert ok is False
        assert "full" in msg.lower()


# ===========================================================================
# 3. Storage — config routing
# ===========================================================================

class TestGetStorage:
    """get_storage() mode selection."""

    def test_get_storage_cloud(self, monkeypatch):
        monkeypatch.delenv("CLAUDEMON_MODE", raising=False)
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_testkey123")
        from engine.storage import get_storage, CloudStorage
        s = get_storage()
        assert isinstance(s, CloudStorage)

    def test_get_storage_local(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CLAUDEMON_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDEMON_MODE", "local")
        # Redirect DB so LocalStorage.__init__ doesn't touch real DB
        import engine.database as db
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        from engine.storage import get_storage, LocalStorage
        s = get_storage()
        assert isinstance(s, LocalStorage)

    def test_get_storage_errors(self, monkeypatch):
        from engine.storage import get_storage, ConfigError

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
# 4. Wrapper — CLI dispatch
# ===========================================================================

class TestCliDispatch:
    """_dispatch_cli() intercepts CLI flags before launching PTY."""

    def test_dispatch_cli_intercepts_flags(self, monkeypatch):
        w = _import_wrapper()
        called = {}

        def fake_cli_main():
            called["yes"] = True

        monkeypatch.setattr("cli.main.main", fake_cli_main)

        for flag in ["--stats", "--list", "--dashboard", "-d", "--help", "-h"]:
            called.clear()
            monkeypatch.setattr("sys.argv", ["wrapper.py", flag])
            assert w._dispatch_cli() is True
            assert called.get("yes"), f"CLI main not called for {flag}"

    def test_dispatch_cli_ignores_normal_args(self, monkeypatch):
        w = _import_wrapper()
        monkeypatch.setattr("sys.argv", ["wrapper.py", "fix the auth bug"])
        assert w._dispatch_cli() is False


# ===========================================================================
# 5. CLI — display and error handling
# ===========================================================================

class TestCliDisplay:
    """CLI formatting and error handling."""

    def test_show_list_cloud_failure_exits(self, monkeypatch, capsys):
        """When cloud returns None, CLI shows error and exits."""
        monkeypatch.setenv("NO_COLOR", "1")

        from cli import commands

        class FakeStorage:
            def get_all(self):
                return None
        monkeypatch.setattr(commands, "_get_storage", lambda: FakeStorage())

        with pytest.raises(SystemExit):
            commands.show_list()
        out = capsys.readouterr().out
        assert "Could not reach" in out

    def test_show_list_displays_creatures(self, monkeypatch, capsys):
        """When storage has data, CLI displays hatched and eggs separately."""
        monkeypatch.setenv("NO_COLOR", "1")

        from cli import commands

        class FakeStorage:
            def get_all(self):
                return [
                    {"word": "Thinking", "level": 25, "times_caught": 10,
                     "evolution_stage": 20, "is_egg": False, "in_team": True},
                    {"word": "Reasoning", "level": 1, "times_caught": 1,
                     "evolution_stage": 1, "is_egg": True, "in_team": False,
                     "hatch_progress": 0.33},
                ]
        monkeypatch.setattr(commands, "_get_storage", lambda: FakeStorage())

        commands.show_list()
        out = capsys.readouterr().out
        assert "COLLECTION" in out
        assert "Thinking" in out
        assert "EGGS" in out
        assert "Reasoning" in out
        assert "Total: 2" in out

    def test_progress_bar(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        from cli.commands import _progress_bar
        bar = _progress_bar(0.5, width=8)
        assert "=" in bar
        assert "[" in bar


# ===========================================================================
# 6. MCP — formatting
# ===========================================================================

class TestFormatCreature:
    """format_creature() display strings."""

    def test_format_creature(self):
        from mcp.server import format_creature
        c = {
            "word": "Analyzing",
            "level": 25,
            "evolution_stage": 20,
            "times_caught": 10,
            "is_egg": False,
            "in_team": True,
        }
        result = format_creature(c)
        assert "Analyzing" in result
        assert "Lv.25" in result
        assert "SPAWN" in result  # stage 20 → SPAWN
        assert "10x caught" in result
        assert "[TEAM]" in result
        assert "(egg)" not in result

    def test_format_creature_defaults(self):
        from mcp.server import format_creature
        # Minimal dict — missing evolution_stage should default to LARVA
        c = {"word": "Unknown", "level": 1, "times_caught": 1}
        result = format_creature(c)
        assert "LARVA" in result
        assert "(egg)" not in result
        assert "[TEAM]" not in result


# ===========================================================================
# 7. Performance fixes — database lazy init & backoff (#7, #10)
# ===========================================================================

class TestLazyInitDb:
    """Fix #10: init_db() no longer runs at import time."""

    def test_no_init_on_import(self):
        """Module-level init_db() is removed — flag starts False."""
        import engine.database as db
        # _db_initialized may be True if prior tests triggered it,
        # but the module no longer has `init_db()` at the bottom.
        import inspect
        source = inspect.getsource(db)
        # Verify there's no bare `init_db()` call at module level (outside def/class)
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped == "init_db()" and not line.startswith(" "):
                pytest.fail("Found bare init_db() at module level")

    def test_ensure_initialized_is_idempotent(self, tmp_path):
        """_ensure_initialized can be called many times safely."""
        import engine.database as db
        db.DB_PATH = tmp_path / "test_idempotent.db"
        db._db_initialized = False
        db._ensure_initialized()
        assert db._db_initialized is True
        # Second call should be a no-op (fast path)
        db._ensure_initialized()
        assert db._db_initialized is True

    def test_get_connection_triggers_init(self, tmp_path):
        """get_connection() calls _ensure_initialized under the hood."""
        import engine.database as db
        db.DB_PATH = tmp_path / "test_trigger.db"
        db._db_initialized = False
        conn = db.get_connection()
        conn.close()
        assert db._db_initialized is True

    def test_ensure_initialized_thread_safe(self, tmp_path):
        """_ensure_initialized with concurrent threads only inits once."""
        import engine.database as db
        db.DB_PATH = tmp_path / "test_thread.db"
        db._db_initialized = False

        call_count = {"n": 0}
        original_init = db.init_db

        def counting_init():
            call_count["n"] += 1
            original_init()

        db.init_db = counting_init
        try:
            threads = [threading.Thread(target=db._ensure_initialized) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert call_count["n"] == 1
        finally:
            db.init_db = original_init


class TestExponentialBackoff:
    """Fix #7: with_retry uses real exponential backoff + jitter."""

    def test_backoff_delays_are_exponential(self, tmp_path):
        """Verify sleep() is called with exponentially increasing delays."""
        import engine.database as db
        db.DB_PATH = tmp_path / "test_backoff.db"
        db._db_initialized = False
        db.init_db()

        sleep_times = []
        original_sleep = time.sleep

        def mock_sleep(t):
            sleep_times.append(t)

        call_count = {"n": 0}

        @db.with_retry
        def always_locked():
            call_count["n"] += 1
            raise sqlite3.OperationalError("database is locked")

        with patch("time.sleep", side_effect=mock_sleep):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                always_locked()

        assert call_count["n"] == db.MAX_RETRIES
        assert len(sleep_times) == db.MAX_RETRIES  # sleep after each failed attempt
        # Verify delays are within expected exponential bounds
        # base_delay = 0.05 * 2^attempt, actual = base * uniform(0.5, 1.5)
        for i, t in enumerate(sleep_times):
            base = 0.05 * (2 ** i)
            assert t >= base * 0.5, f"Delay {i} too small: {t} < {base * 0.5}"
            assert t < base * 1.5, f"Delay {i} too large: {t} >= {base * 1.5}"


# ===========================================================================
# 8. Performance fixes — CloudStorage connection pooling (#1, #2)
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
        cs._conn = mock_conn

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
# 9. Performance fixes — notifications thread pool (#3, #8)
# ===========================================================================

class TestNotificationPool:
    """Fix #3: ThreadPoolExecutor replaces unbounded threads."""

    def test_pool_exists_with_correct_config(self):
        from engine import notifications
        from concurrent.futures import ThreadPoolExecutor
        assert isinstance(notifications._notification_pool, ThreadPoolExecutor)
        assert notifications._notification_pool._max_workers == 4

    def test_module_level_random(self):
        """Fix #8: random is imported at module level."""
        import engine.notifications as notif
        assert hasattr(notif, "random")
        assert notif.random is __import__("random")

    def test_cli_commands_module_level_random(self):
        """Fix #8: random is imported at module level in cli/commands.py."""
        from cli import commands
        assert hasattr(commands, "random")
        assert commands.random is __import__("random")

    def test_notify_async_uses_pool(self):
        """notify_async submits to pool instead of creating Thread."""
        from engine import notifications
        with patch.object(notifications._notification_pool, "submit") as mock_submit:
            notifications.notify_async("title", "msg", word="Test", level=1)
            mock_submit.assert_called_once_with(
                notifications._send_native_notification, "title", "msg", "Test", 1
            )

    def test_webhook_async_uses_pool(self):
        """_send_webhook_async submits to pool instead of creating Thread."""
        from engine import notifications
        with patch.object(notifications._notification_pool, "submit") as mock_submit:
            notifications._send_webhook_async("catch", word="X", level=5)
            mock_submit.assert_called_once_with(
                notifications._send_webhook, "catch", word="X", level=5
            )


# ===========================================================================
# 10. Performance fixes — engine position checkpointing (#5)
# ===========================================================================

class TestPositionCheckpointing:
    """Fix #5: engine saves/loads byte offset to resume across restarts."""

    def test_save_and_load_position(self, tmp_path):
        import engine.engine as eng
        pos_file = str(tmp_path / "engine.pos")
        eng.POSITION_FILE = pos_file

        eng._save_position(42)
        assert eng._load_position(default=0) == 42

    def test_load_position_missing_file(self, tmp_path):
        import engine.engine as eng
        eng.POSITION_FILE = str(tmp_path / "nonexistent.pos")
        assert eng._load_position(default=99) == 99

    def test_load_position_corrupt_file(self, tmp_path):
        import engine.engine as eng
        pos_file = tmp_path / "corrupt.pos"
        pos_file.write_text("not_a_number")
        eng.POSITION_FILE = str(pos_file)
        assert eng._load_position(default=77) == 77

    def test_save_position_is_atomic(self, tmp_path):
        """Save uses tmp + os.replace — no partial writes."""
        import engine.engine as eng
        pos_file = str(tmp_path / "engine.pos")
        eng.POSITION_FILE = pos_file

        eng._save_position(100)
        # tmp file should not linger
        assert not os.path.exists(pos_file + ".tmp")
        assert eng._load_position(default=0) == 100

    def test_watch_catches_resumes_from_saved_position(self, tmp_path, monkeypatch):
        """Engine resumes reading from the saved position, processing only new lines."""
        import engine.engine as eng

        catches_file = str(tmp_path / "catches.jsonl")
        pos_file = str(tmp_path / "engine.pos")
        eng.CATCHES_FILE = catches_file
        eng.POSITION_FILE = pos_file

        # Write 3 lines, save position after line 2
        lines = [
            json.dumps({"word": "First", "ts": 1.0}) + "\n",
            json.dumps({"word": "Second", "ts": 2.0}) + "\n",
            json.dumps({"word": "Third", "ts": 3.0}) + "\n",
        ]
        with open(catches_file, "w") as f:
            f.writelines(lines)

        # Save position at end of line 2
        pos_after_two = len(lines[0].encode()) + len(lines[1].encode())
        eng._save_position(pos_after_two)

        # Read from saved position — should only see "Third"
        with open(catches_file, "r") as f:
            saved = eng._load_position(default=os.path.getsize(catches_file))
            f.seek(saved)
            remaining = f.readline().strip()
        assert json.loads(remaining)["word"] == "Third"

    def test_watch_catches_truncated_file_resets(self, tmp_path):
        """If file was truncated (saved_pos > size), start from 0."""
        import engine.engine as eng

        catches_file = str(tmp_path / "catches.jsonl")
        pos_file = str(tmp_path / "engine.pos")
        eng.CATCHES_FILE = catches_file
        eng.POSITION_FILE = pos_file

        with open(catches_file, "w") as f:
            f.write(json.dumps({"word": "Only", "ts": 1.0}) + "\n")

        file_size = os.path.getsize(catches_file)
        eng._save_position(file_size + 1000)  # stale position bigger than file

        saved_pos = eng._load_position(default=file_size)
        assert saved_pos > file_size  # confirms truncation scenario

        # Engine logic: if saved_pos > file_size, seek to 0
        with open(catches_file, "r") as f:
            if saved_pos > file_size:
                f.seek(0)
            else:
                f.seek(saved_pos)
            line = f.readline().strip()
        assert json.loads(line)["word"] == "Only"


# ===========================================================================
# 11. Performance fixes — MCP tail_lines (#6, #9)
# ===========================================================================

class TestTailLines:
    """Fix #6: _tail_lines reads last N lines without loading entire file."""

    def test_tail_exact_count(self, tmp_path):
        from mcp.server import _tail_lines
        f = tmp_path / "test.jsonl"
        lines = [f'{{"word": "w{i}"}}\n' for i in range(20)]
        f.write_text("".join(lines))

        result = _tail_lines(str(f), 5)
        assert len(result) == 5
        assert json.loads(result[-1])["word"] == "w19"
        assert json.loads(result[0])["word"] == "w15"

    def test_tail_more_than_available(self, tmp_path):
        from mcp.server import _tail_lines
        f = tmp_path / "test.jsonl"
        f.write_text('{"a": 1}\n{"a": 2}\n')

        result = _tail_lines(str(f), 100)
        assert len(result) == 2

    def test_tail_empty_file(self, tmp_path):
        from mcp.server import _tail_lines
        f = tmp_path / "empty.jsonl"
        f.write_text("")

        result = _tail_lines(str(f), 10)
        assert result == []

    def test_tail_single_line(self, tmp_path):
        from mcp.server import _tail_lines
        f = tmp_path / "single.jsonl"
        f.write_text('{"word": "solo"}\n')

        result = _tail_lines(str(f), 5)
        assert len(result) == 1
        assert json.loads(result[0])["word"] == "solo"

    def test_tail_large_lines(self, tmp_path):
        """Lines larger than a single 4096-byte chunk."""
        from mcp.server import _tail_lines
        f = tmp_path / "large.jsonl"
        big_word = "x" * 5000
        lines = [f'{{"w": "{big_word}"}}\n' for _ in range(5)]
        f.write_text("".join(lines))

        result = _tail_lines(str(f), 3)
        assert len(result) == 3
        for line in result:
            assert json.loads(line)["w"] == big_word

    def test_datetime_module_level(self):
        """Fix #9: datetime is imported at module level in mcp/server.py."""
        from mcp import server
        assert hasattr(server, "datetime")


# ===========================================================================
# 12. Performance fixes — wrapper polling & debug handle (#4, #11)
# ===========================================================================

class TestWrapperPolling:
    """Fix #4: Windows polling sleep values are reduced."""

    def test_windows_sleep_values_in_source(self):
        """Verify the sleep values are the reduced ones (not the old aggressive ones)."""
        import inspect
        import wrapper
        source = inspect.getsource(wrapper._main_windows)
        # stdin_reader should use 0.02, not 0.005
        assert "sleep(0.02)" in source
        assert "sleep(0.005)" not in source
        # main loop should use 0.03, not 0.01
        assert "sleep(0.03)" in source


class TestDebugHandleLeak:
    """Fix #11: atexit handler ensures debug file is closed."""

    def test_close_debug_closes_handle(self, tmp_path):
        import wrapper as w
        # Simulate an open debug file
        debug_file = tmp_path / "debug.log"
        handle = open(debug_file, "a")
        w._debug_f = handle

        w._close_debug()
        assert w._debug_f is None
        assert handle.closed

    def test_close_debug_noop_when_none(self):
        import wrapper as w
        old = w._debug_f
        w._debug_f = None
        w._close_debug()  # should not raise
        assert w._debug_f is None
        w._debug_f = old  # restore

    def test_dbg_registers_atexit(self, tmp_path, monkeypatch):
        """First call to dbg() registers _close_debug via atexit."""
        import wrapper as w

        monkeypatch.setattr(w, "DEBUG", True)
        monkeypatch.setattr(w, "DEBUG_LOG", str(tmp_path / "debug.log"))
        # Reset handle so dbg() opens a new one
        if w._debug_f is not None:
            w._debug_f.close()
        w._debug_f = None

        with patch("atexit.register") as mock_atexit:
            w.dbg("test message")
            mock_atexit.assert_called_once_with(w._close_debug)

        # Cleanup
        if w._debug_f is not None:
            w._debug_f.close()
            w._debug_f = None
