"""
tests/test_cli.py - Tests for CLI display and MCP formatting.
"""

import json
from unittest.mock import MagicMock

import pytest

# ===========================================================================
# CLI display
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
# MCP formatting
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
# MCP tail_lines
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
# Engine key validation & update
# ===========================================================================

class TestCmdUpdateKeyValidation:
    """API key validation rejects non-alphanumeric characters."""

    def test_rejects_key_with_semicolons(self, monkeypatch, capsys):
        from cli import engine_commands
        monkeypatch.setattr(engine_commands, "IS_MAC", False)
        monkeypatch.setattr(engine_commands, "IS_LINUX", False)
        engine_commands.cmd_update_key(["sk_claudemon_; rm -rf /"])
        err = capsys.readouterr().err
        assert "Invalid key format" in err

    def test_rejects_key_with_spaces(self, monkeypatch, capsys):
        from cli import engine_commands
        engine_commands.cmd_update_key(["sk_claudemon_abc def"])
        err = capsys.readouterr().err
        assert "Invalid key format" in err

    def test_rejects_key_with_quotes(self, monkeypatch, capsys):
        from cli import engine_commands
        engine_commands.cmd_update_key(['sk_claudemon_abc"def'])
        err = capsys.readouterr().err
        assert "Invalid key format" in err

    def test_rejects_key_with_dollar(self, monkeypatch, capsys):
        from cli import engine_commands
        engine_commands.cmd_update_key(["sk_claudemon_abc$(whoami)"])
        err = capsys.readouterr().err
        assert "Invalid key format" in err

    def test_accepts_valid_alphanumeric_key(self, monkeypatch, capsys):
        from cli import engine_commands
        monkeypatch.setattr(engine_commands, "IS_MAC", True)
        monkeypatch.setattr(engine_commands, "IS_LINUX", False)
        # Should pass validation but fail at plist check
        monkeypatch.setattr("os.path.isfile", lambda p: False)
        engine_commands.cmd_update_key(["sk_claudemon_WWv9ZTXYtTJ5H1QD"])
        err = capsys.readouterr().err
        # Should have passed validation and hit "plist not found"
        assert "Invalid key format" not in err
        assert "plist not found" in err

    def test_rejects_empty_suffix(self, monkeypatch, capsys):
        from cli import engine_commands
        engine_commands.cmd_update_key(["sk_claudemon_"])
        err = capsys.readouterr().err
        assert "Invalid key format" in err


class TestCmdUpdateKeyMac:
    """macOS uses 'defaults write' instead of PlistBuddy for the API key."""

    def test_uses_defaults_write_not_plistbuddy_for_key(self, monkeypatch, tmp_path):
        from cli import engine_commands

        monkeypatch.setattr(engine_commands, "IS_MAC", True)
        monkeypatch.setattr(engine_commands, "IS_LINUX", False)
        plist = tmp_path / "test.plist"
        plist.write_text("<plist></plist>")
        monkeypatch.setattr(engine_commands, "PLIST_PATH", str(plist))

        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)

        monkeypatch.setattr("subprocess.run", fake_run)
        monkeypatch.setattr(engine_commands, "_restart", lambda: True)

        engine_commands.cmd_update_key(["sk_claudemon_abc123"])

        # First call should be 'defaults write' with key as separate arg
        defaults_call = calls[0]
        assert defaults_call[0] == "defaults"
        assert defaults_call[1] == "write"
        assert "CLAUDEMON_API_KEY" in defaults_call
        assert "sk_claudemon_abc123" in defaults_call

        # PlistBuddy used only for Delete of CLAUDEMON_MODE (no user input)
        plistbuddy_calls = [c for c in calls if "/usr/libexec/PlistBuddy" in str(c)]
        for c in plistbuddy_calls:
            # Ensure no API key leaks into PlistBuddy commands
            assert "sk_claudemon_abc123" not in str(c)
