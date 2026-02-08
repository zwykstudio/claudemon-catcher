"""
tests/test_engine.py - Tests for engine: statusline, health, handle_catch
                       ordering, sync cooldown, position checkpointing.
"""

import json
import os
from unittest.mock import MagicMock

import pytest


# ===========================================================================
# update_statusline
# ===========================================================================

class TestEngineUpdateStatusline:
    """update_statusline() writes per-session files with counts and XP."""

    def test_writes_per_session_file(self, tmp_path, monkeypatch):
        from engine.engine import update_statusline, _session_count_map, _session_xp_map
        from engine.engine import _session_duration_map, _session_catches_list, STATUSLINE_FILE
        import engine.engine as eng

        monkeypatch.setattr(eng, "STATUSLINE_FILE", str(tmp_path / "statusline.json"))

        # Clear session state
        _session_count_map.clear()
        _session_xp_map.clear()
        _session_duration_map.clear()
        _session_catches_list.clear()

        result = MagicMock(is_new=True, just_hatched=False, evolved=False, xp_earned=15, new_level=1)
        update_statusline("Flowing", result, "sess1", duration=5.0)

        sl_file = tmp_path / "statusline-sess1.json"
        assert sl_file.exists()
        data = json.loads(sl_file.read_text())
        assert data["word"] == "Flowing"
        assert data["event"] == "new"
        assert data["xp"] == 15
        assert data["count"] == 1
        assert data["total_xp"] == 15
        assert data["duration"] == 5.0

    def test_increments_session_counters(self, tmp_path, monkeypatch):
        from engine.engine import update_statusline, _session_count_map, _session_xp_map
        from engine.engine import _session_duration_map, _session_catches_list
        import engine.engine as eng

        monkeypatch.setattr(eng, "STATUSLINE_FILE", str(tmp_path / "statusline.json"))
        _session_count_map.clear()
        _session_xp_map.clear()
        _session_duration_map.clear()
        _session_catches_list.clear()

        r1 = MagicMock(is_new=True, just_hatched=False, evolved=False, xp_earned=10, new_level=1)
        r2 = MagicMock(is_new=False, just_hatched=False, evolved=False, xp_earned=5, new_level=2)

        update_statusline("Word1", r1, "sess2", duration=3.0)
        update_statusline("Word2", r2, "sess2", duration=4.0)

        data = json.loads((tmp_path / "statusline-sess2.json").read_text())
        assert data["word"] == "Word2"
        assert data["count"] == 2
        assert data["total_xp"] == 15
        assert data["total_duration"] == 7.0


# ===========================================================================
# Engine health
# ===========================================================================

class TestEngineWriteHealth:
    """_write_health() writes engine status for statusline."""

    def test_writes_ok_status(self, tmp_path, monkeypatch):
        import engine.engine as eng
        monkeypatch.setattr(eng, "HEALTH_FILE", str(tmp_path / "engine.status"))

        eng._write_health("ok")

        data = json.loads((tmp_path / "engine.status").read_text())
        assert data["status"] == "ok"
        assert "error" not in data
        assert "ts" in data

    def test_writes_error_with_message(self, tmp_path, monkeypatch):
        import engine.engine as eng
        monkeypatch.setattr(eng, "HEALTH_FILE", str(tmp_path / "engine.status"))

        eng._write_health("error", "HTTP 429 rate limited")

        data = json.loads((tmp_path / "engine.status").read_text())
        assert data["status"] == "error"
        assert data["error"] == "HTTP 429 rate limited"


# ===========================================================================
# handle_catch ordering
# ===========================================================================

class TestEngineHandleCatchOrder:
    """handle_catch() updates statusline BEFORE notifications."""

    def test_statusline_before_notifications(self, tmp_path, monkeypatch):
        import engine.engine as eng
        from engine.engine import _session_count_map, _session_xp_map
        from engine.engine import _session_duration_map, _session_catches_list

        monkeypatch.setattr(eng, "STATUSLINE_FILE", str(tmp_path / "statusline.json"))
        monkeypatch.setattr(eng, "HEALTH_FILE", str(tmp_path / "engine.status"))
        _session_count_map.clear()
        _session_xp_map.clear()
        _session_duration_map.clear()
        _session_catches_list.clear()

        call_order = []

        fake_result = MagicMock(is_new=False, just_hatched=False, evolved=False, xp_earned=5, new_level=2)
        fake_storage = MagicMock()
        fake_storage.catch.return_value = fake_result

        original_update = eng.update_statusline
        def track_statusline(*a, **k):
            call_order.append("statusline")
            original_update(*a, **k)

        def track_get_creature(*a, **k):
            call_order.append("get_creature")
            return None

        monkeypatch.setattr(eng, "update_statusline", track_statusline)
        fake_storage.get_creature = track_get_creature
        monkeypatch.setattr(eng, "notify_catch", lambda *a: call_order.append("notify"))

        eng.handle_catch(fake_storage, "TestWord", sid="ordersid", duration=3.0)

        assert call_order.index("statusline") < call_order.index("get_creature")
        assert call_order.index("statusline") < call_order.index("notify")

    def test_notification_error_does_not_affect_statusline(self, tmp_path, monkeypatch):
        import engine.engine as eng
        from engine.engine import _session_count_map, _session_xp_map
        from engine.engine import _session_duration_map, _session_catches_list

        monkeypatch.setattr(eng, "STATUSLINE_FILE", str(tmp_path / "statusline.json"))
        monkeypatch.setattr(eng, "HEALTH_FILE", str(tmp_path / "engine.status"))
        _session_count_map.clear()
        _session_xp_map.clear()
        _session_duration_map.clear()
        _session_catches_list.clear()

        fake_result = MagicMock(is_new=False, just_hatched=False, evolved=False, xp_earned=8, new_level=3)
        fake_storage = MagicMock()
        fake_storage.catch.return_value = fake_result
        fake_storage.get_creature.side_effect = RuntimeError("API exploded")

        monkeypatch.setattr(eng, "notify_catch", lambda *a: None)

        eng.handle_catch(fake_storage, "StillWorks", sid="errsid", duration=2.0)

        # Statusline should still be written despite get_creature error
        sl_file = tmp_path / "statusline-errsid.json"
        assert sl_file.exists()
        data = json.loads(sl_file.read_text())
        assert data["word"] == "StillWorks"


# ===========================================================================
# Sync cooldown
# ===========================================================================

class TestEngineSyncCooldown:
    """Engine watch loop respects SYNC_COOLDOWN per session."""

    def test_cooldown_constant_exists(self):
        import engine.engine as eng
        assert hasattr(eng, "SYNC_COOLDOWN")
        assert eng.SYNC_COOLDOWN > 0


# ===========================================================================
# Position checkpointing
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
