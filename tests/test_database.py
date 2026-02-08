"""
tests/test_database.py - Tests for database: game logic, evolution, team,
                         lazy init, backoff.
"""

import sqlite3
import threading
import time
from unittest.mock import patch

import pytest

from helpers import _import_database


# ===========================================================================
# Evolution
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


# ===========================================================================
# Catch word
# ===========================================================================

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


# ===========================================================================
# Team
# ===========================================================================

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
# Lazy init
# ===========================================================================

class TestLazyInitDb:
    """Fix #10: init_db() no longer runs at import time."""

    def test_no_init_on_import(self):
        """Module-level init_db() is removed — flag starts False."""
        import engine.database as db
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


# ===========================================================================
# Exponential backoff
# ===========================================================================

class TestExponentialBackoff:
    """Fix #7: with_retry uses real exponential backoff + jitter."""

    def test_backoff_delays_are_exponential(self, tmp_path):
        """Verify sleep() is called with exponentially increasing delays."""
        import engine.database as db
        db.DB_PATH = tmp_path / "test_backoff.db"
        db._db_initialized = False
        db.init_db()

        sleep_times = []

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
