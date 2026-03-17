"""Tests for the SSH manager module."""

import pytest
from unittest.mock import patch, MagicMock

from sysadmin_agent.connection.ssh_manager import SSHManager


class TestSSHManagerDefaults:
    """Test default configuration options."""

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_default_port_is_22(self, _mock_paramiko):
        mgr = SSHManager(host="192.168.1.1", username="admin", password="secret")
        assert mgr.port == 22

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_default_timeout_is_15(self, _mock_paramiko):
        mgr = SSHManager(host="192.168.1.1", username="admin", password="secret")
        assert mgr.timeout == 15

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_stores_host_and_username(self, _mock_paramiko):
        mgr = SSHManager(host="10.0.0.5", username="deploy", password="pw")
        assert mgr.host == "10.0.0.5"
        assert mgr.username == "deploy"

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_private_key_path_defaults_to_none(self, _mock_paramiko):
        mgr = SSHManager(host="10.0.0.1", username="admin", password="pw")
        assert mgr.private_key_path is None


class TestSSHManagerCustomOptions:
    """Test custom port and timeout."""

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_custom_port(self, _mock_paramiko):
        mgr = SSHManager(host="10.0.0.1", username="admin", password="pw", port=2222)
        assert mgr.port == 2222

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_custom_timeout(self, _mock_paramiko):
        mgr = SSHManager(host="10.0.0.1", username="admin", password="pw", timeout=60)
        assert mgr.timeout == 60

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_custom_port_and_timeout_together(self, _mock_paramiko):
        mgr = SSHManager(host="10.0.0.1", username="admin", password="pw", port=8022, timeout=5)
        assert mgr.port == 8022
        assert mgr.timeout == 5


class TestSSHManagerCredentials:
    """Test credential validation on connect."""

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_rejects_connect_without_credentials(self, _mock_paramiko):
        mgr = SSHManager(host="10.0.0.1", username="admin")
        with pytest.raises(ValueError, match="password or private_key_path"):
            mgr.connect()

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_connect_with_password_succeeds(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.AutoAddPolicy.return_value = MagicMock()

        mgr = SSHManager(host="10.0.0.1", username="admin", password="secret")
        mgr.connect()
        mock_client.connect.assert_called_once()

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_connect_with_key_succeeds(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.AutoAddPolicy.return_value = MagicMock()
        mock_paramiko.RSAKey.from_private_key_file.return_value = MagicMock()

        mgr = SSHManager(
            host="10.0.0.1",
            username="admin",
            private_key_path="/home/admin/.ssh/id_rsa",
        )
        mgr.connect()
        mock_client.connect.assert_called_once()


class TestSSHManagerExecute:
    """Test command execution guards."""

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_rejects_execute_when_not_connected(self, _mock_paramiko):
        mgr = SSHManager(host="10.0.0.1", username="admin", password="pw")
        with pytest.raises(ConnectionError):
            mgr.execute("whoami")

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_is_connected_false_initially(self, _mock_paramiko):
        mgr = SSHManager(host="10.0.0.1", username="admin", password="pw")
        assert mgr.is_connected is False

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_execute_returns_expected_format(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.AutoAddPolicy.return_value = MagicMock()

        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"root\n"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

        mgr = SSHManager(host="10.0.0.1", username="admin", password="pw")
        mgr.connect()
        result = mgr.execute("whoami")

        assert result["stdout"] == "root\n"
        assert result["stderr"] == ""
        assert result["exit_code"] == 0


class TestSSHManagerContextManager:
    """Test context manager usage."""

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_context_manager_calls_connect_and_disconnect(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.AutoAddPolicy.return_value = MagicMock()

        with SSHManager(host="10.0.0.1", username="admin", password="pw") as mgr:
            assert mgr is not None

        mock_client.close.assert_called()

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_context_manager_disconnects_on_exception(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.AutoAddPolicy.return_value = MagicMock()

        with pytest.raises(RuntimeError):
            with SSHManager(host="10.0.0.1", username="admin", password="pw"):
                raise RuntimeError("something broke")

        mock_client.close.assert_called()


class TestSSHManagerDisconnect:
    """Test disconnect safety."""

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_disconnect_when_not_connected_is_safe(self, _mock_paramiko):
        mgr = SSHManager(host="10.0.0.1", username="admin", password="pw")
        # Should not raise any exception
        mgr.disconnect()

    @patch("sysadmin_agent.connection.ssh_manager.paramiko", create=True)
    def test_disconnect_twice_is_safe(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.AutoAddPolicy.return_value = MagicMock()

        mgr = SSHManager(host="10.0.0.1", username="admin", password="pw")
        mgr.connect()
        mgr.disconnect()
        mgr.disconnect()  # second call should not raise
