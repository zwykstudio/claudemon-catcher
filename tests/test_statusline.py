"""
tests/test_statusline.py - Tests for statusline.py bash script.

The script polls for engine sync (up to 8s) before falling back to capturing.
Tests that provide matching engine data return instantly. Tests for the
capturing fallback are slower.
"""

import json
import os
import subprocess
import sys
import time

from helpers import STATUSLINE_PY, _run_statusline


# ===========================================================================
# Synced (engine has processed the word) — fast tests
# ===========================================================================

class TestStatuslineSynced:
    """statusline.py shows synced result with XP, events, counts."""

    def test_shows_full_result(self, tmp_path):
        sl_dir = tmp_path / ".claudemon"
        sl_dir.mkdir()
        now = time.time()
        output = _run_statusline(
            sl_dir, "synced1",
            engine_data={
                "word": "Reasoning", "xp": 15, "event": "new", "duration": 7.2,
                "count": 3, "total_xp": 45, "ts": now,
            },
        )
        assert "Reasoning" in output
        assert "+15xp" in output
        assert "7.2s" in output
        assert "NEW" in output
        assert "3 caught" in output
        assert "45xp" in output

    def test_shows_evolved_event(self, tmp_path):
        sl_dir = tmp_path / ".claudemon"
        sl_dir.mkdir()
        output = _run_statusline(
            sl_dir, "synced2",
            engine_data={
                "word": "Evolving", "xp": 20, "event": "evolved", "duration": 3.0,
                "count": 1, "total_xp": 20, "ts": time.time(),
            },
        )
        assert "EVOLVED" in output

    def test_shows_hatched_event(self, tmp_path):
        sl_dir = tmp_path / ".claudemon"
        sl_dir.mkdir()
        output = _run_statusline(
            sl_dir, "synced3",
            engine_data={
                "word": "Hatching", "xp": 5, "event": "hatched", "duration": 2.0,
                "count": 1, "total_xp": 5, "ts": time.time(),
            },
        )
        assert "HATCHED" in output

    def test_waits_then_shows_synced(self, tmp_path):
        """Script polls and picks up engine data that arrives after live data."""
        sl_dir = tmp_path / ".claudemon"
        sl_dir.mkdir()
        now = time.time()
        # Live file says capturing, engine file already has the result
        output = _run_statusline(
            sl_dir, "wait1",
            live_data={"phase": "capturing", "word": "Flowing", "ts": now - 0.5},
            engine_data={
                "word": "Flowing", "xp": 10, "event": "new", "duration": 5.0,
                "count": 1, "total_xp": 10, "ts": now,
            },
        )
        assert "Flowing" in output
        assert "+10xp" in output
        assert "NEW" in output
        assert "..." not in output

    def test_stale_data_shows_summary(self, tmp_path):
        sl_dir = tmp_path / ".claudemon"
        sl_dir.mkdir()
        output = _run_statusline(
            sl_dir, "synced4",
            engine_data={
                "word": "Old", "xp": 5, "event": None, "duration": 2.0,
                "count": 3, "total_xp": 25, "ts": time.time() - 600,  # 10 min old
            },
        )
        assert "[Claudemon]" in output
        assert "Old" not in output
        assert "3 caught" in output
        assert "25xp" in output

    def test_same_second_float_comparison(self, tmp_path):
        """Engine syncs 0.3s after live write — same integer second."""
        sl_dir = tmp_path / ".claudemon"
        sl_dir.mkdir()
        now = time.time()
        output = _run_statusline(
            sl_dir, "float1",
            live_data={"phase": "syncing", "word": "Kneading", "duration": 4.4, "ts": now - 0.3},
            engine_data={
                "word": "Kneading", "xp": 8, "event": None, "duration": 4.4,
                "count": 1, "total_xp": 8, "ts": now,
            },
        )
        assert "..." not in output
        assert "Kneading" in output


# ===========================================================================
# Engine health
# ===========================================================================

class TestStatuslineEngineHealth:
    """statusline.py shows engine errors in statusline."""

    def test_shows_recent_error(self, tmp_path):
        sl_dir = tmp_path / ".claudemon"
        sl_dir.mkdir()
        output = _run_statusline(
            sl_dir, "health1",
            health_data={"status": "error", "error": "HTTP 429 rate limited", "ts": time.time()},
        )
        assert "429" in output or "rate" in output

    def test_ignores_old_error(self, tmp_path):
        sl_dir = tmp_path / ".claudemon"
        sl_dir.mkdir()
        now = time.time()
        output = _run_statusline(
            sl_dir, "health2",
            health_data={"status": "error", "error": "old problem", "ts": now - 600},
            engine_data={
                "word": "Recent", "xp": 5, "event": None, "duration": 3.0,
                "count": 1, "total_xp": 5, "ts": now,
            },
        )
        assert "old problem" not in output

    def test_ok_status_shows_normal(self, tmp_path):
        sl_dir = tmp_path / ".claudemon"
        sl_dir.mkdir()
        output = _run_statusline(
            sl_dir, "health3",
            health_data={"status": "ok", "ts": time.time()},
            engine_data={
                "word": "Fine", "xp": 5, "event": None, "duration": 3.0,
                "count": 1, "total_xp": 5, "ts": time.time(),
            },
        )
        assert "Fine" in output
        assert "⚠" not in output


# ===========================================================================
# No SID / empty session
# ===========================================================================

class TestStatuslineEdgeCases:
    """Edge cases: no SID, empty session."""

    def test_plain_output_without_sid(self, tmp_path):
        env = os.environ.copy()
        env.pop("CLAUDEMON_SID", None)
        stdin_data = json.dumps({"model": {"display_name": "Opus"}, "context_window": {"used_percentage": 25.3}})
        result = subprocess.run(
            [sys.executable, STATUSLINE_PY],
            input=stdin_data, capture_output=True, text=True,
            env=env, timeout=5,
        )
        output = result.stdout.strip()
        assert "Opus" in output
        assert "[Claudemon]" not in output

    def test_empty_session_shows_tag(self, tmp_path):
        """No live file, no engine file → just tag."""
        sl_dir = tmp_path / ".claudemon"
        sl_dir.mkdir()
        output = _run_statusline(sl_dir, "empty1")
        assert "[Claudemon]" in output
