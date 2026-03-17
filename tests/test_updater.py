"""Tests for the Updater module."""

import json
import pytest
from unittest.mock import patch, MagicMock

from sysadmin_agent.updater.updater import Updater


class TestMergeEnvPreservesOldValues:
    def test_merge_env_preserves_old_values(self, tmp_path):
        old = tmp_path / "old.env"
        new = tmp_path / "new.env"
        output = tmp_path / "output.env"

        old.write_text("DB_HOST=mydb.local\nDB_PORT=5432\n")
        new.write_text("DB_HOST=localhost\nDB_PORT=3306\n")

        updater = Updater(repo_dir=str(tmp_path))
        updater.merge_env_files(str(old), str(new), str(output))

        content = output.read_text()
        assert "DB_HOST=mydb.local" in content
        assert "DB_PORT=5432" in content
        # New defaults should NOT override old values
        assert "DB_HOST=localhost" not in content
        assert "DB_PORT=3306" not in content


class TestMergeEnvAddsNewKeys:
    def test_merge_env_adds_new_keys(self, tmp_path):
        old = tmp_path / "old.env"
        new = tmp_path / "new.env"
        output = tmp_path / "output.env"

        old.write_text("DB_HOST=mydb.local\n")
        new.write_text("DB_HOST=localhost\nNEW_FEATURE_FLAG=enabled\n")

        updater = Updater(repo_dir=str(tmp_path))
        updater.merge_env_files(str(old), str(new), str(output))

        content = output.read_text()
        assert "NEW_FEATURE_FLAG=enabled" in content
        # The new key should have a comment indicating it's new
        assert "# NEW in update" in content


class TestMergeEnvCommentsRemovedKeys:
    def test_merge_env_comments_removed_keys(self, tmp_path):
        old = tmp_path / "old.env"
        new = tmp_path / "new.env"
        output = tmp_path / "output.env"

        old.write_text("DB_HOST=mydb.local\nOLD_KEY=deprecated_value\n")
        new.write_text("DB_HOST=localhost\n")

        updater = Updater(repo_dir=str(tmp_path))
        updater.merge_env_files(str(old), str(new), str(output))

        content = output.read_text()
        # OLD_KEY should be commented out with a REMOVED marker
        assert "# REMOVED" in content
        assert "OLD_KEY=deprecated_value" in content
        # It should not appear as an active key
        lines = [
            l for l in content.splitlines()
            if not l.startswith("#") and "OLD_KEY" in l
        ]
        assert len(lines) == 0


class TestMergeJsonPreservesUserValues:
    def test_merge_json_preserves_user_values(self, tmp_path):
        old = tmp_path / "old.json"
        new = tmp_path / "new.json"
        output = tmp_path / "output.json"

        old.write_text(json.dumps({
            "database": {"host": "mydb.local", "port": 5432},
            "log_level": "debug",
        }))
        new.write_text(json.dumps({
            "database": {"host": "localhost", "port": 3306},
            "log_level": "info",
        }))

        updater = Updater(repo_dir=str(tmp_path))
        updater.merge_json_configs(str(old), str(new), str(output))

        result = json.loads(output.read_text())
        # Old (user) values should take precedence
        assert result["database"]["host"] == "mydb.local"
        assert result["database"]["port"] == 5432
        assert result["log_level"] == "debug"


class TestMergeJsonAddsNewKeys:
    def test_merge_json_adds_new_keys(self, tmp_path):
        old = tmp_path / "old.json"
        new = tmp_path / "new.json"
        output = tmp_path / "output.json"

        old.write_text(json.dumps({
            "database": {"host": "mydb.local"},
        }))
        new.write_text(json.dumps({
            "database": {"host": "localhost", "pool_size": 10},
            "new_section": {"enabled": True},
        }))

        updater = Updater(repo_dir=str(tmp_path))
        updater.merge_json_configs(str(old), str(new), str(output))

        result = json.loads(output.read_text())
        # Old value preserved
        assert result["database"]["host"] == "mydb.local"
        # New key added from new config
        assert result["database"]["pool_size"] == 10
        # New top-level section added
        assert result["new_section"]["enabled"] is True


class TestBackupConfigs:
    def test_backup_configs(self, tmp_path):
        # Create config files in the repo dir
        env_file = tmp_path / ".env"
        json_file = tmp_path / "config.json"
        env_file.write_text("DB_HOST=localhost\n")
        json_file.write_text('{"key": "value"}\n')

        updater = Updater(
            repo_dir=str(tmp_path),
            config_files=[".env", "config.json"],
        )
        backed_up = updater.backup_configs()

        assert len(backed_up) == 2
        assert (tmp_path / ".env.backup").exists()
        assert (tmp_path / "config.json.backup").exists()
        assert (tmp_path / ".env.backup").read_text() == "DB_HOST=localhost\n"
        assert (tmp_path / "config.json.backup").read_text() == '{"key": "value"}\n'


class TestCheckForUpdates:
    @patch("sysadmin_agent.updater.updater.subprocess.run")
    def test_check_for_updates(self, mock_run, tmp_path):
        """Mock subprocess to simulate git commands for update checking."""

        def side_effect(*args, **kwargs):
            cmd = args[0]
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""

            if cmd[1] == "fetch":
                result.stdout = ""
            elif cmd[1] == "symbolic-ref":
                result.stdout = "refs/remotes/origin/main"
            elif cmd[1] == "rev-parse" and cmd[2] == "HEAD":
                result.stdout = "aabbccdd11223344"
            elif cmd[1] == "rev-parse" and "origin/" in cmd[2]:
                result.stdout = "eeffgg5566778899"
            elif cmd[1] == "rev-list":
                result.stdout = "3"
            elif cmd[1] == "log":
                result.stdout = "eeffgg55 Add new feature\naabbccdd Fix bug\n1122aabb Update docs\n"
            else:
                result.stdout = ""

            return result

        mock_run.side_effect = side_effect

        updater = Updater(repo_dir=str(tmp_path))
        info = updater.check_for_updates()

        assert info["update_available"] is True
        assert info["commits_behind"] == 3
        assert info["current_version"] == "aabbccdd"
        assert info["latest_version"] == "eeffgg55"
        assert len(info["changes_summary"]) == 3
