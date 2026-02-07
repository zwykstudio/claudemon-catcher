"""
tests/test_all.py - Tests for Claudemon Catcher.

Run:  python3 -m pytest tests/ -v
"""

import hashlib
import hmac
import json
import os
import re
import sys
import time

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
