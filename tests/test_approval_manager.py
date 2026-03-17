"""Tests for the approval manager module."""

import pytest
from unittest.mock import patch, MagicMock

from sysadmin_agent.approval.approval_manager import ApprovalManager


def _action(command, description="test action", destructive=False):
    """Helper to build an action dict matching the ApprovalManager.request_approval API."""
    return {
        "command": command,
        "description": description,
        "destructive": destructive,
    }


class TestAutoApprove:
    """Test auto-approve mode."""

    def test_auto_approve_returns_true(self):
        mgr = ApprovalManager(auto_approve=True)
        result = mgr.request_approval(_action("rm -rf /tmp/old_logs"))
        assert result is True

    def test_auto_approve_does_not_call_prompt(self):
        prompt_fn = MagicMock(return_value="y")
        mgr = ApprovalManager(auto_approve=True, prompt_fn=prompt_fn)
        mgr.request_approval(_action("systemctl restart nginx"))
        prompt_fn.assert_not_called()


class TestCustomPromptFn:
    """Test custom prompt function."""

    @patch("sys.stdin")
    def test_custom_prompt_fn_is_used(self, mock_stdin):
        mock_stdin.isatty.return_value = True
        prompt_fn = MagicMock(return_value="y")
        mgr = ApprovalManager(prompt_fn=prompt_fn)
        result = mgr.request_approval(_action("apt-get update"))

        prompt_fn.assert_called_once()
        assert result is True

    @patch("sys.stdin")
    def test_custom_prompt_fn_receives_action_dict(self, mock_stdin):
        mock_stdin.isatty.return_value = True
        captured = {}

        def my_prompt(action):
            captured.update(action)
            return "y"

        mgr = ApprovalManager(prompt_fn=my_prompt)
        mgr.request_approval(_action("apt-get upgrade", description="upgrade all"))

        assert captured["command"] == "apt-get upgrade"
        assert captured["description"] == "upgrade all"


class TestDenial:
    """Test approval denial."""

    @patch("sys.stdin")
    def test_denial_when_prompt_returns_false(self, mock_stdin):
        mock_stdin.isatty.return_value = True
        prompt_fn = MagicMock(return_value="n")
        mgr = ApprovalManager(prompt_fn=prompt_fn)
        result = mgr.request_approval(_action("rm -rf /"))
        assert result is False

    @patch("sys.stdin")
    def test_denial_when_prompt_returns_no_string(self, mock_stdin):
        mock_stdin.isatty.return_value = True
        prompt_fn = MagicMock(return_value="no")
        mgr = ApprovalManager(prompt_fn=prompt_fn)
        result = mgr.request_approval(_action("dd if=/dev/zero of=/dev/sda"))
        assert result is False


class TestStatsTracking:
    """Test approval statistics tracking."""

    def test_stats_tracking_all_approved(self):
        mgr = ApprovalManager(auto_approve=True)
        mgr.request_approval(_action("cmd1"))
        mgr.request_approval(_action("cmd2"))
        mgr.request_approval(_action("cmd3"))

        stats = mgr.get_stats()
        assert stats["total"] == 3
        assert stats["approved"] == 3
        assert stats["denied"] == 0

    @patch("sys.stdin")
    def test_stats_tracking_all_denied(self, mock_stdin):
        mock_stdin.isatty.return_value = True
        prompt_fn = MagicMock(return_value="n")
        mgr = ApprovalManager(prompt_fn=prompt_fn)
        mgr.request_approval(_action("cmd1"))
        mgr.request_approval(_action("cmd2"))

        stats = mgr.get_stats()
        assert stats["total"] == 2
        assert stats["approved"] == 0
        assert stats["denied"] == 2

    @patch("sys.stdin")
    def test_stats_tracking_mixed(self, mock_stdin):
        mock_stdin.isatty.return_value = True
        responses = iter(["y", "n", "y"])
        prompt_fn = MagicMock(side_effect=responses)
        mgr = ApprovalManager(prompt_fn=prompt_fn)

        mgr.request_approval(_action("cmd1"))  # approved
        mgr.request_approval(_action("cmd2"))  # denied
        mgr.request_approval(_action("cmd3"))  # approved

        stats = mgr.get_stats()
        assert stats["total"] == 3
        assert stats["approved"] == 2
        assert stats["denied"] == 1

    def test_stats_initially_zero(self):
        mgr = ApprovalManager(auto_approve=True)
        stats = mgr.get_stats()
        assert stats["total"] == 0
        assert stats["approved"] == 0
        assert stats["denied"] == 0


class TestNonInteractiveMode:
    """Test non-interactive mode behavior."""

    @patch("sys.stdin")
    def test_non_interactive_mode_denies(self, mock_stdin):
        mock_stdin.isatty.return_value = False
        # Without auto_approve and without prompt_fn, non-interactive should deny
        mgr = ApprovalManager()
        result = mgr.request_approval(_action("systemctl stop firewalld"))
        assert result is False
