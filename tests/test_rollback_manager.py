"""Tests for the rollback manager module."""

import pytest
import uuid
from unittest.mock import MagicMock

from sysadmin_agent.rollback.rollback_manager import RollbackManager


def _make_ssh_mock():
    """Create a basic mock SSH manager for snapshot operations."""
    mock_ssh = MagicMock()
    mock_ssh.is_connected = True
    mock_ssh.execute.return_value = {
        "stdout": "644 root root\n",
        "stderr": "",
        "exit_code": 0,
    }
    mock_ssh.download_file = MagicMock()
    mock_ssh.upload_file = MagicMock()
    return mock_ssh


class TestCreateSnapshot:
    """Test snapshot creation."""

    def test_creates_snapshot_with_uuid(self, tmp_path):
        ssh = _make_ssh_mock()
        mgr = RollbackManager(ssh, snapshot_dir=str(tmp_path))
        snapshot_id = mgr.create_snapshot(
            command="echo 'test' > /etc/test.conf",
            description="before config change",
        )

        # Verify snapshot ID is a valid UUID
        parsed = uuid.UUID(snapshot_id)
        assert str(parsed) == snapshot_id

    def test_snapshot_stored_with_description(self, tmp_path):
        ssh = _make_ssh_mock()
        mgr = RollbackManager(ssh, snapshot_dir=str(tmp_path))
        mgr.create_snapshot(
            command="cp /tmp/a /etc/a",
            description="pre-upgrade checkpoint",
        )

        snapshots = mgr.list_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0]["description"] == "pre-upgrade checkpoint"


class TestListSnapshots:
    """Test snapshot listing."""

    def test_lists_multiple_snapshots(self, tmp_path):
        ssh = _make_ssh_mock()
        mgr = RollbackManager(ssh, snapshot_dir=str(tmp_path))
        mgr.create_snapshot(command="cmd1", description="snapshot 1")
        mgr.create_snapshot(command="cmd2", description="snapshot 2")

        snapshots = mgr.list_snapshots()
        assert len(snapshots) == 2

    def test_list_empty_when_no_snapshots(self, tmp_path):
        ssh = MagicMock()
        mgr = RollbackManager(ssh, snapshot_dir=str(tmp_path))
        snapshots = mgr.list_snapshots()
        assert snapshots == []


class TestRemoveSnapshot:
    """Test snapshot removal."""

    def test_removes_snapshot(self, tmp_path):
        ssh = _make_ssh_mock()
        mgr = RollbackManager(ssh, snapshot_dir=str(tmp_path))
        snapshot_id = mgr.create_snapshot(command="cmd", description="temp")

        result = mgr.remove_snapshot(snapshot_id)
        assert result is True
        assert len(mgr.list_snapshots()) == 0

    def test_remove_nonexistent_returns_false(self, tmp_path):
        ssh = MagicMock()
        mgr = RollbackManager(ssh, snapshot_dir=str(tmp_path))
        result = mgr.remove_snapshot("nonexistent-id-12345")
        assert result is False


class TestRollback:
    """Test rollback behavior."""

    def test_rollback_nonexistent_snapshot_raises_error(self, tmp_path):
        ssh = MagicMock()
        ssh.is_connected = True
        mgr = RollbackManager(ssh, snapshot_dir=str(tmp_path))

        fake_id = str(uuid.uuid4())
        with pytest.raises(ValueError, match="not found"):
            mgr.rollback(fake_id)


class TestIdentifyAffectedFiles:
    """Test identification of affected files from commands."""

    def test_identifies_redirect_target(self, tmp_path):
        ssh = MagicMock()
        mgr = RollbackManager(ssh, snapshot_dir=str(tmp_path))
        files = mgr._analyze_affected_files("echo 'nameserver 8.8.8.8' > /etc/resolv.conf")
        assert "/etc/resolv.conf" in files

    def test_identifies_append_target(self, tmp_path):
        ssh = MagicMock()
        mgr = RollbackManager(ssh, snapshot_dir=str(tmp_path))
        files = mgr._analyze_affected_files("echo '192.168.1.1 myhost' >> /etc/hosts")
        assert "/etc/hosts" in files

    def test_identifies_cp_target(self, tmp_path):
        ssh = MagicMock()
        mgr = RollbackManager(ssh, snapshot_dir=str(tmp_path))
        files = mgr._analyze_affected_files("cp /tmp/nginx.conf /etc/nginx/nginx.conf")
        assert "/etc/nginx/nginx.conf" in files

    def test_identifies_tee_target(self, tmp_path):
        ssh = MagicMock()
        mgr = RollbackManager(ssh, snapshot_dir=str(tmp_path))
        files = mgr._analyze_affected_files("echo 'data' | tee /etc/myapp.conf")
        assert "/etc/myapp.conf" in files
