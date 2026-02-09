"""
tests/test_wrapper.py - Tests for wrapper: word extraction, live statusline,
                        banner, recap, CLI dispatch, debug handle, polling.
"""

import hashlib
import json
import os
import time
from unittest.mock import patch

import pytest

from helpers import _import_wrapper


# ===========================================================================
# Word regex
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


# ===========================================================================
# process_chunk
# ===========================================================================

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

    def test_carriage_return_clears_old_frames(self):
        """Spinner \r rewrites should not accumulate in the buffer."""
        w, buf, seen, pending, h, sid = self._make_env()
        # Simulate spinner cycling: multiple frames with \r
        chunk = "✶ Propagating…\r✳ Propagating…\r✻ Propagating…".encode()
        buf = w.process_chunk(chunk, buf, seen, pending, h, sid)
        # Only one copy of the word should be in seen
        assert "Propagating" in seen
        # Buffer should only have the last frame, not all three
        assert buf.count("Propagating") == 1

    def test_carriage_return_prevents_corruption(self):
        """Split reads with \r should not produce truncated words."""
        w, buf, seen, pending, h, sid = self._make_env()
        # First chunk: spinner frame + start of rewrite
        buf = w.process_chunk("✶ Propagating…\r✳ Propag".encode(), buf, seen, pending, h, sid)
        assert "Propagating" in seen  # first frame captured

        # Second chunk: rest of the rewrite
        buf = w.process_chunk("ating…".encode(), buf, seen, pending, h, sid)
        # "Propaging" should NOT be captured (it's a corruption)
        assert "Propaging" not in seen
        # Only "Propagating" should be in seen (duplicate, correctly ignored)
        assert len(seen) == 1

    def test_word_across_chunks_without_cr(self):
        """Word split across two reads (no \\r) should still be captured."""
        w, buf, seen, pending, h, sid = self._make_env()
        buf = w.process_chunk("✶ Propag".encode(), buf, seen, pending, h, sid)
        assert not seen  # not yet complete
        buf = w.process_chunk("ating…".encode(), buf, seen, pending, h, sid)
        assert "Propagating" in seen


# ===========================================================================
# flush_pending
# ===========================================================================

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

    def test_refreshes_live_file_during_long_capture(self, tmp_path):
        """Live file ts is refreshed periodically so statusline.py doesn't time out."""
        w = _import_wrapper()
        w.STATUSLINE_LIVE_DIR = str(tmp_path)
        w.CATCHES_FILE = str(tmp_path / "catches.jsonl")

        now = time.time()
        pending = {
            "word": "Cultivating", "ts": now - 70, "proof": "abc",
            "last_seen": now,  # spinner still active
            "_live_refresh": now - 10,  # last refresh was 10s ago (>5s threshold)
        }

        w.flush_pending(pending, "longsid", force=False)

        # Should NOT have flushed (spinner still active)
        assert pending
        assert pending["word"] == "Cultivating"

        # But live file should have been refreshed
        live_file = tmp_path / "statusline-longsid-live.json"
        assert live_file.exists()
        data = json.loads(live_file.read_text())
        assert data["phase"] == "capturing"
        assert data["word"] == "Cultivating"
        assert data["ts"] >= now  # ts is fresh

    def test_no_refresh_when_recently_refreshed(self, tmp_path):
        """Live file is NOT refreshed if <5s since last refresh."""
        w = _import_wrapper()
        w.STATUSLINE_LIVE_DIR = str(tmp_path)
        w.CATCHES_FILE = str(tmp_path / "catches.jsonl")

        now = time.time()
        pending = {
            "word": "Thinking", "ts": now - 10, "proof": "abc",
            "last_seen": now,
            "_live_refresh": now - 2,  # refreshed 2s ago (<5s threshold)
        }

        w.flush_pending(pending, "nosid", force=False)

        # Live file should NOT have been written
        live_file = tmp_path / "statusline-nosid-live.json"
        assert not live_file.exists()


# ===========================================================================
# emit
# ===========================================================================

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
# Live statusline
# ===========================================================================

class TestWriteLiveStatus:
    """_write_live_status() writes live capture state for statusline.py."""

    def test_writes_live_file_with_timestamp(self, tmp_path):
        w = _import_wrapper()
        w.STATUSLINE_LIVE_DIR = str(tmp_path)

        before = time.time()
        w._write_live_status("sid1", {"phase": "capturing", "word": "Flowing", "start": 100.0})
        after = time.time()

        live_file = tmp_path / "statusline-sid1-live.json"
        assert live_file.exists()
        data = json.loads(live_file.read_text())
        assert data["phase"] == "capturing"
        assert data["word"] == "Flowing"
        assert data["start"] == 100.0
        assert before <= data["ts"] <= after

    def test_writes_syncing_phase(self, tmp_path):
        w = _import_wrapper()
        w.STATUSLINE_LIVE_DIR = str(tmp_path)

        w._write_live_status("sid2", {"phase": "syncing", "word": "Zesting", "duration": 5.1})

        data = json.loads((tmp_path / "statusline-sid2-live.json").read_text())
        assert data["phase"] == "syncing"
        assert data["duration"] == 5.1

    def test_atomic_write(self, tmp_path):
        """Write uses tmp + os.replace for atomicity."""
        w = _import_wrapper()
        w.STATUSLINE_LIVE_DIR = str(tmp_path)

        w._write_live_status("sid3", {"phase": "capturing", "word": "Test"})
        # No leftover .tmp file
        assert not (tmp_path / "statusline-sid3-live.json.tmp").exists()
        assert (tmp_path / "statusline-sid3-live.json").exists()

    def test_silently_handles_errors(self):
        """Should not raise even with invalid path."""
        w = _import_wrapper()
        old = w.STATUSLINE_LIVE_DIR
        w.STATUSLINE_LIVE_DIR = "/nonexistent/path/that/does/not/exist"
        w._write_live_status("sid", {"phase": "test"})  # should not raise
        w.STATUSLINE_LIVE_DIR = old


class TestProcessChunkLiveStatus:
    """process_chunk() writes live statusline on word detection."""

    def test_writes_capturing_on_new_word(self, tmp_path):
        w = _import_wrapper()
        w.STATUSLINE_LIVE_DIR = str(tmp_path)
        w.CATCHES_FILE = str(tmp_path / "catches.jsonl")

        buf, seen, pending = "", set(), {}
        h, sid = hashlib.sha256(), "livesid1"

        before = time.time()
        w.process_chunk("✶ Reasoning…".encode(), buf, seen, pending, h, sid)

        live_file = tmp_path / f"statusline-{sid}-live.json"
        assert live_file.exists()
        data = json.loads(live_file.read_text())
        assert data["phase"] == "capturing"
        assert data["word"] == "Reasoning"
        assert data["start"] >= before


class TestFlushPendingLiveStatus:
    """flush_pending() writes syncing phase to live statusline."""

    def test_writes_syncing_on_flush(self, tmp_path):
        w = _import_wrapper()
        w.STATUSLINE_LIVE_DIR = str(tmp_path)
        w.CATCHES_FILE = str(tmp_path / "catches.jsonl")
        old_catches = list(w._session_catches)

        now = time.time()
        pending = {"word": "Flowing", "ts": now - 5.0, "proof": "abc", "last_seen": now - 5.0}

        w.flush_pending(pending, "flushsid", force=True)

        live_file = tmp_path / "statusline-flushsid-live.json"
        assert live_file.exists()
        data = json.loads(live_file.read_text())
        assert data["phase"] == "syncing"
        assert data["word"] == "Flowing"
        assert data["duration"] > 0
        assert not pending  # cleared

        # Restore
        w._session_catches[:] = old_catches


class TestEmitTracksSessionCatches:
    """emit() appends to _session_catches for wrapper-side recap."""

    def test_appends_to_session_catches(self, tmp_path):
        w = _import_wrapper()
        w.CATCHES_FILE = str(tmp_path / "catches.jsonl")
        old_catches = list(w._session_catches)
        w._session_catches.clear()

        w.emit("Analyzing", 1700000000.0, "proof1", "sid1", duration=3.456)
        w.emit("Thinking", 1700000001.0, "proof2", "sid1", duration=1.2)

        assert len(w._session_catches) == 2
        assert w._session_catches[0] == {"word": "Analyzing", "duration": 3.5}
        assert w._session_catches[1] == {"word": "Thinking", "duration": 1.2}

        # Restore
        w._session_catches[:] = old_catches


# ===========================================================================
# Banner, statusline check, API key check
# ===========================================================================

class TestCheckApiKey:
    """_check_api_key() returns (status, detail) tuple."""

    def test_cloud_with_valid_key(self, monkeypatch):
        w = _import_wrapper()
        monkeypatch.delenv("CLAUDEMON_MODE", raising=False)
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_abc123")
        assert w._check_api_key() == ("cloud", "cloud sync")

    def test_local_mode(self, monkeypatch):
        w = _import_wrapper()
        monkeypatch.setenv("CLAUDEMON_MODE", "local")
        monkeypatch.delenv("CLAUDEMON_API_KEY", raising=False)
        assert w._check_api_key() == ("local", "local mode")

    def test_no_key(self, monkeypatch):
        w = _import_wrapper()
        monkeypatch.delenv("CLAUDEMON_MODE", raising=False)
        monkeypatch.delenv("CLAUDEMON_API_KEY", raising=False)
        status, detail = w._check_api_key()
        assert status == "error"

    def test_invalid_key_format(self, monkeypatch):
        w = _import_wrapper()
        monkeypatch.delenv("CLAUDEMON_MODE", raising=False)
        monkeypatch.setenv("CLAUDEMON_API_KEY", "not_a_valid_key")
        status, detail = w._check_api_key()
        assert status == "error"
        assert "invalid" in detail


class TestCheckStatusline:
    """_check_statusline() reads ~/.claude/settings.json."""

    def test_returns_true_when_configured(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "statusLine": {"command": "python3 /path/to/statusline.py"}
        }))
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(settings) if "~" in p else p)
        assert w._check_statusline() is True

    def test_returns_false_when_not_configured(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"other": "stuff"}))
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(settings) if "~" in p else p)
        assert w._check_statusline() is False

    def test_returns_false_when_file_missing(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "nope.json") if "~" in p else p)
        assert w._check_statusline() is False


class TestInstallStatusline:
    """_install_statusline() writes to Claude Code settings.json."""

    def test_installs_to_settings(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        settings_file = str(tmp_path / "settings.json")
        (tmp_path / "settings.json").write_text("{}")
        monkeypatch.setattr(w, "SETTINGS_FILE", settings_file)

        w._install_statusline()

        data = json.loads((tmp_path / "settings.json").read_text())
        assert "statusLine" in data
        assert data["statusLine"]["type"] == "command"
        assert "statusline.py" in data["statusLine"]["command"]

    def test_preserves_existing_settings(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        settings_file = str(tmp_path / "settings.json")
        (tmp_path / "settings.json").write_text(json.dumps({"theme": "dark", "other": 42}))
        monkeypatch.setattr(w, "SETTINGS_FILE", settings_file)

        w._install_statusline()

        data = json.loads((tmp_path / "settings.json").read_text())
        assert data["theme"] == "dark"
        assert data["other"] == 42
        assert "statusLine" in data

    def test_creates_file_if_missing(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        settings_file = str(tmp_path / "newsettings.json")
        monkeypatch.setattr(w, "SETTINGS_FILE", settings_file)

        w._install_statusline()

        assert os.path.exists(settings_file)
        data = json.loads(open(settings_file).read())
        assert "statusLine" in data


class TestPrintBanner:
    """print_banner() shows version, API status, and statusline tip."""

    def test_shows_version(self, capsys, monkeypatch):
        w = _import_wrapper()
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        monkeypatch.setattr(w, "_check_statusline", lambda: True)
        monkeypatch.setattr(w, "_check_update", lambda: ("current", ""))
        w.print_banner()
        err = capsys.readouterr().err
        assert w.VERSION in err
        assert "claudemon" in err

    def test_shows_api_error(self, capsys, monkeypatch):
        w = _import_wrapper()
        monkeypatch.delenv("CLAUDEMON_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDEMON_MODE", raising=False)
        monkeypatch.setattr(w, "_check_statusline", lambda: True)
        monkeypatch.setattr(w, "_check_update", lambda: ("error", ""))
        w.print_banner()
        err = capsys.readouterr().err
        assert "no API key" in err or "✗" in err

    def test_shows_statusline_tip(self, capsys, monkeypatch):
        w = _import_wrapper()
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        monkeypatch.setattr(w, "_check_statusline", lambda: False)
        monkeypatch.setattr(w, "_check_update", lambda: ("current", ""))
        w.print_banner()
        err = capsys.readouterr().err
        assert "install-statusline" in err


# ===========================================================================
# Version update check
# ===========================================================================

class TestCheckUpdate:
    """_check_update() compares local HEAD with remote HEAD."""

    def test_returns_current_when_up_to_date(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        cache_file = str(tmp_path / "version.check")
        monkeypatch.setattr(w, "VERSION_CHECK_FILE", cache_file)

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = "abc123\n"
            if cmd[1] == "rev-parse":
                return R()
            if cmd[1] == "ls-remote":
                R.stdout = "abc123\tHEAD\n"
                return R()
            return R()

        monkeypatch.setattr(w.subprocess, "run", fake_run)
        status, msg = w._check_update()
        assert status == "current"
        assert msg == ""

    def test_returns_behind_when_outdated(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        cache_file = str(tmp_path / "version.check")
        monkeypatch.setattr(w, "VERSION_CHECK_FILE", cache_file)

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
            if cmd[1] == "rev-parse":
                R.stdout = "aaa111\n"
            elif cmd[1] == "ls-remote":
                R.stdout = "bbb222\tHEAD\n"
            return R()

        monkeypatch.setattr(w.subprocess, "run", fake_run)
        status, msg = w._check_update()
        assert status == "behind"
        assert "update available" in msg
        assert "cc update" in msg

    def test_returns_current_when_ahead(self, tmp_path, monkeypatch):
        """Local has commits not yet pushed — should NOT say 'update available'."""
        w = _import_wrapper()
        cache_file = str(tmp_path / "version.check")
        monkeypatch.setattr(w, "VERSION_CHECK_FILE", cache_file)

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
            if cmd[1] == "rev-parse":
                R.stdout = "aaa111\n"
            elif cmd[1] == "ls-remote":
                R.stdout = "bbb222\tHEAD\n"
            elif cmd[1] == "merge-base":
                R.returncode = 1  # local is NOT ancestor of remote (we're ahead)
            return R()

        monkeypatch.setattr(w.subprocess, "run", fake_run)
        status, msg = w._check_update()
        assert status == "current"

    def test_uses_fresh_cache(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        cache_file = str(tmp_path / "version.check")
        monkeypatch.setattr(w, "VERSION_CHECK_FILE", cache_file)

        # Write a fresh cache that says update available
        import json as _json
        with open(cache_file, "w") as f:
            _json.dump({"ts": time.time(), "local": "aaa111", "remote": "bbb222"}, f)

        cmds = []
        def fake_run(cmd, **kw):
            cmds.append(cmd[1])
            class R:
                returncode = 0
                stdout = "aaa111\n"
            return R()

        monkeypatch.setattr(w.subprocess, "run", fake_run)
        status, msg = w._check_update()
        assert status == "behind"
        assert "update available" in msg
        # rev-parse + merge-base, but NOT ls-remote (cache hit)
        assert "rev-parse" in cmds
        assert "merge-base" in cmds
        assert "ls-remote" not in cmds

    def test_skips_stale_cache(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        cache_file = str(tmp_path / "version.check")
        monkeypatch.setattr(w, "VERSION_CHECK_FILE", cache_file)

        # Write an expired cache
        import json as _json
        with open(cache_file, "w") as f:
            _json.dump({"ts": time.time() - 90000, "local": "aaa111", "remote": "aaa111"}, f)

        cmds = []
        def fake_run(cmd, **kw):
            cmds.append(cmd[1])
            class R:
                returncode = 0
                stdout = "aaa111\n" if cmd[1] == "rev-parse" else "bbb222\tHEAD\n"
            return R()

        monkeypatch.setattr(w.subprocess, "run", fake_run)
        status, msg = w._check_update()
        assert status == "behind"
        assert "update available" in msg
        # rev-parse + ls-remote + merge-base
        assert "ls-remote" in cmds
        assert "merge-base" in cmds

    def test_returns_error_on_failure(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        cache_file = str(tmp_path / "version.check")
        monkeypatch.setattr(w, "VERSION_CHECK_FILE", cache_file)

        def fake_run(cmd, **kw):
            raise OSError("network down")

        monkeypatch.setattr(w.subprocess, "run", fake_run)
        status, msg = w._check_update()
        assert status == "error"
        assert msg == ""

    def test_returns_error_on_timeout(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        cache_file = str(tmp_path / "version.check")
        monkeypatch.setattr(w, "VERSION_CHECK_FILE", cache_file)

        def fake_run(cmd, **kw):
            raise w.subprocess.TimeoutExpired(cmd, 3)

        monkeypatch.setattr(w.subprocess, "run", fake_run)
        status, msg = w._check_update()
        assert status == "error"
        assert msg == ""

    def test_writes_cache_atomically(self, tmp_path, monkeypatch):
        w = _import_wrapper()
        cache_file = str(tmp_path / "version.check")
        monkeypatch.setattr(w, "VERSION_CHECK_FILE", cache_file)

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = "abc123\n" if cmd[1] == "rev-parse" else "abc123\tHEAD\n"
            return R()

        monkeypatch.setattr(w.subprocess, "run", fake_run)
        w._check_update()

        # Cache file written, no tmp file left
        assert os.path.exists(cache_file)
        assert not os.path.exists(cache_file + ".tmp")
        import json as _json
        with open(cache_file) as f:
            data = _json.load(f)
        assert data["local"] == "abc123"
        assert data["remote"] == "abc123"


class TestBannerShowsUpdate:
    """print_banner() shows update status inline and detail line."""

    def test_shows_update_in_banner(self, capsys, monkeypatch):
        w = _import_wrapper()
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        monkeypatch.setattr(w, "_check_statusline", lambda: True)
        monkeypatch.setattr(w, "_check_update", lambda: ("behind", "update available — run: cc update"))
        w.print_banner()
        err = capsys.readouterr().err
        assert "update available" in err
        assert "↑" in err
        assert "◆" in err

    def test_shows_up_to_date(self, capsys, monkeypatch):
        w = _import_wrapper()
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        monkeypatch.setattr(w, "_check_statusline", lambda: True)
        monkeypatch.setattr(w, "_check_update", lambda: ("current", ""))
        w.print_banner()
        err = capsys.readouterr().err
        assert "up to date" in err
        assert "update available" not in err

    def test_no_suffix_on_error(self, capsys, monkeypatch):
        w = _import_wrapper()
        monkeypatch.setenv("CLAUDEMON_API_KEY", "sk_claudemon_test")
        monkeypatch.setattr(w, "_check_statusline", lambda: True)
        monkeypatch.setattr(w, "_check_update", lambda: ("error", ""))
        w.print_banner()
        err = capsys.readouterr().err
        assert "up to date" not in err
        assert "update available" not in err


# ===========================================================================
# cc update command
# ===========================================================================

class TestCmdUpdate:
    """_cmd_update() pulls code and restarts engine."""

    def test_pulls_and_restarts(self, capsys, tmp_path, monkeypatch):
        w = _import_wrapper()
        monkeypatch.setattr(w, "VERSION_CHECK_FILE", str(tmp_path / "version.check"))

        calls = []
        def fake_run(cmd, **kw):
            calls.append(cmd)
            class R:
                returncode = 0
                stdout = "Already up to date.\n"
                stderr = ""
            return R()

        monkeypatch.setattr(w.subprocess, "run", fake_run)
        monkeypatch.setattr("cli.engine_commands._restart", lambda: True)
        monkeypatch.setattr("cli.engine_commands._is_running", lambda: True)

        w._cmd_update()

        err = capsys.readouterr().err
        assert "Already up to date" in err
        assert "Engine restarted" in err
        # Should have called git pull --ff-only
        assert any("pull" in str(c) for c in calls)

    def test_clears_version_cache(self, capsys, tmp_path, monkeypatch):
        w = _import_wrapper()
        cache_file = str(tmp_path / "version.check")
        monkeypatch.setattr(w, "VERSION_CHECK_FILE", cache_file)
        # Create a cache file
        with open(cache_file, "w") as f:
            f.write("{}")

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = "Already up to date.\n"
                stderr = ""
            return R()

        monkeypatch.setattr(w.subprocess, "run", fake_run)
        monkeypatch.setattr("cli.engine_commands._restart", lambda: True)
        monkeypatch.setattr("cli.engine_commands._is_running", lambda: True)

        w._cmd_update()
        assert not os.path.exists(cache_file)

    def test_dispatch_routes_update(self, monkeypatch):
        w = _import_wrapper()
        called = {}
        monkeypatch.setattr(w, "_cmd_update", lambda: called.update(yes=True))
        monkeypatch.setattr("sys.argv", ["wrapper.py", "update"])
        assert w._dispatch_cli() is True
        assert called.get("yes")


# ===========================================================================
# Session recap
# ===========================================================================

class TestPrintRecap:
    """print_recap() shows session summary on exit."""

    def test_no_output_when_no_catches(self, capsys):
        w = _import_wrapper()
        old = list(w._session_catches)
        w._session_catches.clear()
        w.print_recap("sid-empty", set(), time.time())
        err = capsys.readouterr().err
        assert err == ""
        w._session_catches[:] = old

    def test_shows_catches_and_totals(self, capsys, tmp_path):
        w = _import_wrapper()
        old = list(w._session_catches)
        w._session_catches[:] = [
            {"word": "Reasoning", "duration": 3.5},
            {"word": "Thinking", "duration": 2.1},
        ]
        w.STATUSLINE_LIVE_DIR = str(tmp_path)  # no engine file → wrapper-only recap

        w.print_recap("recap-sid", {"Reasoning", "Thinking"}, time.time() - 60)

        err = capsys.readouterr().err
        assert "Reasoning" in err
        assert "Thinking" in err
        assert "2" in err  # count
        assert "recap" in err.lower()

        w._session_catches[:] = old

    def test_enriches_with_engine_data(self, capsys, tmp_path):
        w = _import_wrapper()
        old = list(w._session_catches)
        w._session_catches[:] = [{"word": "Flowing", "duration": 5.0}]
        w.STATUSLINE_LIVE_DIR = str(tmp_path)

        # Write engine data
        engine_file = tmp_path / "statusline-enrich-sid.json"
        engine_file.write_text(json.dumps({
            "total_xp": 42,
            "catches": [{"word": "Flowing", "xp": 42, "duration": 5.0, "event": "new"}],
        }))

        w.print_recap("enrich-sid", {"Flowing"}, time.time() - 30)

        err = capsys.readouterr().err
        assert "42xp" in err or "42" in err
        assert "NEW" in err

        w._session_catches[:] = old


# ===========================================================================
# CLI dispatch
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

    def test_dispatch_cli_ignores_flags_after_first_arg(self, monkeypatch):
        """CLI flags in argv[2:] should NOT be intercepted (they're for claude)."""
        w = _import_wrapper()
        for flag in ["--stats", "--list", "--dashboard", "-d", "--help", "-h"]:
            monkeypatch.setattr("sys.argv", ["wrapper.py", "-p", flag])
            assert w._dispatch_cli() is False, f"{flag} in argv[2] should not be intercepted"


# ===========================================================================
# Polling and debug handle
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
