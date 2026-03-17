"""Tests for the OS detector module."""

import pytest
from unittest.mock import MagicMock

from sysadmin_agent.discovery.os_detector import OSDetector


def make_mock_ssh(responses):
    """Create a mock SSH manager that returns canned responses for commands.

    Args:
        responses: dict mapping command substrings to (stdout, stderr, exit_code).
                   The first matching substring wins.
    """
    mock_ssh = MagicMock()
    mock_ssh.is_connected = True

    def fake_execute(command, timeout=30):
        for key, (stdout, stderr, code) in responses.items():
            if key in command:
                return {"stdout": stdout, "stderr": stderr, "exit_code": code}
        return {"stdout": "", "stderr": "command not found", "exit_code": 127}

    mock_ssh.execute = MagicMock(side_effect=fake_execute)
    return mock_ssh


UBUNTU_RESPONSES = {
    "cat /etc/os-release": (
        'NAME="Ubuntu"\nVERSION="22.04.3 LTS (Jammy Jellyfish)"\n'
        'ID=ubuntu\nID_LIKE=debian\nVERSION_ID="22.04"\n',
        "",
        0,
    ),
    "uname -a": ("Linux web-server-01 5.15.0-91-generic #101 SMP x86_64 GNU/Linux\n", "", 0),
    "uname -m": ("x86_64\n", "", 0),
    "hostname": ("web-server-01\n", "", 0),
    "uptime": (" 10:32:01 up 45 days,  3:12,  2 users,  load average: 0.08, 0.12, 0.09\n", "", 0),
    "cat /proc/version": ("Linux version 5.15.0-91-generic (buildd@bos03-amd64-016)\n", "", 0),
    "lsb_release": (
        "Distributor ID:\tUbuntu\nRelease:\t22.04\nCodename:\tjammy\n",
        "",
        0,
    ),
}

CENTOS_RESPONSES = {
    "cat /etc/os-release": (
        'NAME="CentOS Linux"\nVERSION="7 (Core)"\n'
        'ID="centos"\nID_LIKE="rhel fedora"\nVERSION_ID="7"\n',
        "",
        0,
    ),
    "uname -a": ("Linux db-server-01 3.10.0-1160.el7.x86_64 #1 SMP x86_64 GNU/Linux\n", "", 0),
    "uname -m": ("x86_64\n", "", 0),
    "hostname": ("db-server-01\n", "", 0),
    "uptime": (" 14:00:01 up 120 days,  1:05,  1 user,  load average: 0.50, 0.40, 0.30\n", "", 0),
    "cat /proc/version": ("Linux version 3.10.0-1160.el7.x86_64\n", "", 0),
    "lsb_release": ("", "command not found", 127),
}


class TestOSDetectorUbuntu:
    """Test Ubuntu detection."""

    def test_detects_ubuntu_correctly(self):
        mock_ssh = make_mock_ssh(UBUNTU_RESPONSES)
        detector = OSDetector(mock_ssh)
        info = detector.detect()

        assert info is not None
        assert info["type"].lower() == "linux"
        assert info["distribution"].lower() == "ubuntu"
        assert "22.04" in info["version"]
        assert "x86_64" in info["architecture"]
        assert info["hostname"] == "web-server-01"


class TestOSDetectorCentOS:
    """Test CentOS detection."""

    def test_detects_centos_correctly(self):
        mock_ssh = make_mock_ssh(CENTOS_RESPONSES)
        detector = OSDetector(mock_ssh)
        info = detector.detect()

        assert info is not None
        assert info["type"].lower() == "linux"
        assert info["distribution"].lower() == "centos"
        assert "7" in info["version"]
        assert "x86_64" in info["architecture"]
        assert info["hostname"] == "db-server-01"


class TestOSDetectorUnknown:
    """Test unknown OS handling."""

    def test_handles_unknown_os_gracefully(self):
        unknown_responses = {
            "cat /etc/os-release": ("", "No such file or directory", 1),
            "uname -a": ("UnknownOS mystery-box 1.0.0\n", "", 0),
            "uname -m": ("mystery_arch\n", "", 0),
            "hostname": ("mystery-box\n", "", 0),
            "uptime": ("", "", 1),
            "cat /proc/version": ("", "No such file or directory", 1),
            "lsb_release": ("", "command not found", 127),
        }
        mock_ssh = make_mock_ssh(unknown_responses)
        detector = OSDetector(mock_ssh)
        info = detector.detect()

        # Should not crash; should return a dict with sensible defaults
        assert info is not None
        assert isinstance(info, dict)
        assert "hostname" in info


class TestOSDetectorSSHErrors:
    """Test SSH error handling in OS detection."""

    def test_handles_ssh_errors_gracefully(self):
        mock_ssh = MagicMock()
        mock_ssh.is_connected = True
        mock_ssh.execute = MagicMock(side_effect=Exception("SSH connection lost"))

        detector = OSDetector(mock_ssh)
        # The ThreadPoolExecutor catches exceptions and returns fallback values,
        # so detect() should still return a dict (with Unknown/empty fields)
        info = detector.detect()
        assert info is not None
        assert isinstance(info, dict)

    def test_handles_timeout_errors(self):
        mock_ssh = MagicMock()
        mock_ssh.is_connected = True
        mock_ssh.execute = MagicMock(side_effect=TimeoutError("Command timed out"))

        detector = OSDetector(mock_ssh)
        info = detector.detect()
        assert info is not None
        assert isinstance(info, dict)
