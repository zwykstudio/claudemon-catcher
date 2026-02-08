"""
tests/test_notifications.py - Tests for notification thread pool.
"""

from unittest.mock import patch


# ===========================================================================
# Notification pool
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
