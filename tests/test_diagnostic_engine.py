"""Tests for the diagnostic engine module."""

import pytest
from unittest.mock import MagicMock

from sysadmin_agent.diagnostics.diagnostic_engine import DiagnosticEngine


def make_mock_ssh(responses):
    """Create a mock SSH manager with canned responses.

    Args:
        responses: dict mapping command substrings to (stdout, stderr, exit_code).
    """
    mock_ssh = MagicMock()
    mock_ssh.is_connected = True

    def fake_execute(command, timeout=30):
        for key, (stdout, stderr, code) in responses.items():
            if key in command:
                return {"stdout": stdout, "stderr": stderr, "exit_code": code}
        return {"stdout": "", "stderr": "", "exit_code": 0}

    mock_ssh.execute = MagicMock(side_effect=fake_execute)
    mock_ssh.execute_sudo = MagicMock(side_effect=fake_execute)
    return mock_ssh


class TestDiskUsage:
    """Test disk usage diagnostics."""

    def test_disk_usage_warning_at_90_percent(self):
        responses = {
            "df": (
                "Filesystem     Size  Used Avail Use% Mounted on\n"
                "/dev/sda1       99G   89G  4.9G  90% /\n"
                "/dev/sdb1       50G   10G   38G  22% /data\n",
                "",
                0,
            ),
        }
        ssh = make_mock_ssh(responses)
        approval = MagicMock()
        rollback = MagicMock()
        engine = DiagnosticEngine(ssh, approval, rollback)

        result = engine.check_disk_usage()

        assert result["status"] == "critical"
        assert result["severity"] == "high"
        assert "90%" in result["details"]

    def test_disk_usage_healthy(self):
        responses = {
            "df": (
                "Filesystem     Size  Used Avail Use% Mounted on\n"
                "/dev/sda1       99G   30G   64G  32% /\n",
                "",
                0,
            ),
        }
        ssh = make_mock_ssh(responses)
        approval = MagicMock()
        rollback = MagicMock()
        engine = DiagnosticEngine(ssh, approval, rollback)

        result = engine.check_disk_usage()

        assert result["status"] == "ok"


class TestMemoryUsage:
    """Test memory usage diagnostics."""

    def test_memory_usage_detection_high(self):
        # ~93% used: 16325 total, 1087 available
        responses = {
            "free": (
                "              total        used        free      shared  buff/cache   available\n"
                "Mem:          16325       14892         245         123        1186        1087\n"
                "Swap:          2048        1024        1024\n",
                "",
                0,
            ),
        }
        ssh = make_mock_ssh(responses)
        approval = MagicMock()
        rollback = MagicMock()
        engine = DiagnosticEngine(ssh, approval, rollback)

        result = engine.check_memory_usage()

        assert result["status"] == "warning"
        assert result["severity"] == "medium"
        assert "%" in result["details"]

    def test_memory_usage_healthy(self):
        # ~20% used: 16325 total, 12528 available
        responses = {
            "free": (
                "              total        used        free      shared  buff/cache   available\n"
                "Mem:          16325        3263       10789         524        2263       12528\n"
                "Swap:          2048           0        2048\n",
                "",
                0,
            ),
        }
        ssh = make_mock_ssh(responses)
        approval = MagicMock()
        rollback = MagicMock()
        engine = DiagnosticEngine(ssh, approval, rollback)

        result = engine.check_memory_usage()

        assert result["status"] == "ok"


class TestFailedServices:
    """Test failed services detection."""

    def test_failed_services_detection(self):
        responses = {
            "systemctl": (
                "mysql.service            loaded failed failed MySQL Community Server\n"
                "postfix.service          loaded failed failed Postfix Mail Transport Agent\n",
                "",
                0,
            ),
        }
        ssh = make_mock_ssh(responses)
        approval = MagicMock()
        rollback = MagicMock()
        engine = DiagnosticEngine(ssh, approval, rollback)

        result = engine.check_failed_services()

        assert result["status"] == "critical"
        assert result["severity"] == "high"
        assert "mysql" in result["details"].lower() or "postfix" in result["details"].lower()
        assert result["fix"] is not None
        assert len(result["fix"]) >= 1

    def test_no_failed_services(self):
        responses = {
            "systemctl": ("", "", 0),
        }
        ssh = make_mock_ssh(responses)
        approval = MagicMock()
        rollback = MagicMock()
        engine = DiagnosticEngine(ssh, approval, rollback)

        result = engine.check_failed_services()

        assert result["status"] == "ok"


class TestApplyFix:
    """Test fix application with approval flow."""

    def test_apply_fix_with_approval_granted(self):
        responses = {
            "systemctl restart": ("", "", 0),
        }
        ssh = make_mock_ssh(responses)
        approval = MagicMock()
        approval.request_approval = MagicMock(return_value=True)
        rollback = MagicMock()
        rollback.create_snapshot = MagicMock(return_value="snap-123")

        engine = DiagnosticEngine(ssh, approval, rollback)
        fix_action = {
            "command": "systemctl restart mysql",
            "description": "Restart MySQL service",
            "destructive": True,
        }
        result = engine.apply_fix(fix_action)

        approval.request_approval.assert_called_once()
        rollback.create_snapshot.assert_called_once()
        ssh.execute_sudo.assert_called_once_with("systemctl restart mysql")
        assert result["applied"] is True

    def test_apply_fix_with_approval_denied(self):
        ssh = make_mock_ssh({})
        approval = MagicMock()
        approval.request_approval = MagicMock(return_value=False)
        rollback = MagicMock()
        rollback.create_snapshot = MagicMock(return_value="snap-456")

        engine = DiagnosticEngine(ssh, approval, rollback)
        fix_action = {
            "command": "systemctl restart mysql",
            "description": "Restart MySQL service",
            "destructive": True,
        }
        result = engine.apply_fix(fix_action)

        approval.request_approval.assert_called_once()
        ssh.execute_sudo.assert_not_called()
        ssh.execute.assert_not_called()
        assert result["applied"] is False
        assert "denied" in result.get("reason", "").lower()

    def test_apply_fix_non_destructive_skips_approval(self):
        responses = {
            "ps aux": ("PID  %MEM COMMAND\n1234  5.2 mysqld\n", "", 0),
        }
        ssh = make_mock_ssh(responses)
        approval = MagicMock()
        rollback = MagicMock()

        engine = DiagnosticEngine(ssh, approval, rollback)
        fix_action = {
            "command": "ps aux --sort=-%mem | head -20",
            "description": "Show top memory-consuming processes",
            "destructive": False,
        }
        result = engine.apply_fix(fix_action)

        # Non-destructive commands do not go through approval
        approval.request_approval.assert_not_called()
        rollback.create_snapshot.assert_not_called()
        assert result["applied"] is True
