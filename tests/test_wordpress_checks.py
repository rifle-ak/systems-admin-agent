"""Tests for the WordPressChecker module."""

import pytest
from unittest.mock import MagicMock

from sysadmin_agent.diagnostics.wordpress_checks import WordPressChecker


def _make_ssh_mock(responses):
    """Build a mock SSHManager whose .execute() returns canned responses keyed by substring."""
    ssh = MagicMock()

    def _execute(cmd, **kwargs):
        for pattern, response in responses.items():
            if pattern in cmd:
                return response
        # Default: command succeeded with empty output
        return {"stdout": "", "stderr": "", "exit_code": 0}

    ssh.execute.side_effect = _execute
    return ssh


class TestWpVersionOutdated:
    def test_wp_version_outdated(self):
        responses = {
            "version.php": {
                "stdout": "<?php\n$wp_version = '5.9.0';\n?>",
                "stderr": "",
                "exit_code": 0,
            },
        }
        ssh = _make_ssh_mock(responses)
        checker = WordPressChecker(ssh_manager=ssh, site_path="/var/www/html")

        result = checker.check_wp_version()

        assert result["name"] == "check_wp_version"
        assert result["status"] == "warning"
        assert result["severity"] == "medium"
        assert "outdated" in result["details"].lower() or "5.9.0" in result["details"]
        assert result["fix"] is not None


class TestWpDebugEnabled:
    def test_wp_debug_enabled(self):
        responses = {
            "wp-config.php": {
                "stdout": "<?php\ndefine('WP_DEBUG', true);\ndefine('DB_NAME', 'wp');\n?>",
                "stderr": "",
                "exit_code": 0,
            },
        }
        ssh = _make_ssh_mock(responses)
        checker = WordPressChecker(ssh_manager=ssh, site_path="/var/www/html")

        result = checker.check_wp_debug_mode()

        assert result["name"] == "check_wp_debug_mode"
        assert result["status"] == "warning"
        assert result["severity"] == "medium"
        assert "WP_DEBUG" in result["details"]
        assert result["fix"] is not None


class TestOpcacheDisabled:
    def test_opcache_disabled(self):
        responses = {
            "json_encode(opcache_get_configuration())": {
                "stdout": "",
                "stderr": "Error",
                "exit_code": 1,
            },
            "php -i": {
                "stdout": "opcache.enable => Off => Off\nopcache.enable_cli => Off => Off\n",
                "stderr": "",
                "exit_code": 0,
            },
        }
        ssh = _make_ssh_mock(responses)
        checker = WordPressChecker(ssh_manager=ssh, site_path="/var/www/html")

        result = checker.check_opcache()

        assert result["name"] == "check_opcache"
        assert result["status"] == "warning"
        assert result["severity"] == "medium"
        assert "not enabled" in result["details"].lower() or "opcache" in result["details"].lower()


class TestFilePermissions777:
    def test_file_permissions_777(self):
        site = "/var/www/html"
        responses = {
            f"stat -c '%a' {site}/wp-config.php": {
                "stdout": "777\n",
                "stderr": "",
                "exit_code": 0,
            },
            f"stat -c '%a' {site}/wp-content 2": {
                "stdout": "755\n",
                "stderr": "",
                "exit_code": 0,
            },
            f"stat -c '%a' {site}/wp-content/uploads": {
                "stdout": "755\n",
                "stderr": "",
                "exit_code": 0,
            },
            "find": {
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
            },
        }
        ssh = _make_ssh_mock(responses)
        checker = WordPressChecker(ssh_manager=ssh, site_path=site)

        result = checker.check_file_permissions()

        assert result["name"] == "check_file_permissions"
        assert result["status"] == "warning"
        assert result["severity"] == "high"
        assert "777" in result["details"]
        assert result["fix"] is not None


class TestNoObjectCache:
    def test_no_object_cache(self):
        responses = {
            "test -f": {
                "stdout": "missing\n",
                "stderr": "",
                "exit_code": 0,
            },
            "systemctl is-active redis": {
                "stdout": "inactive\n",
                "stderr": "",
                "exit_code": 3,
            },
            "systemctl is-active memcached": {
                "stdout": "inactive\n",
                "stderr": "",
                "exit_code": 3,
            },
        }
        ssh = _make_ssh_mock(responses)
        checker = WordPressChecker(ssh_manager=ssh, site_path="/var/www/html")

        result = checker.check_object_cache()

        assert result["name"] == "check_object_cache"
        assert result["status"] in ("warning", "info")
        assert result["severity"] in ("medium", "low")
        assert "no" in result["details"].lower() or "object" in result["details"].lower()


class TestRunAllReturnsList:
    def test_run_all_returns_list(self):
        ssh = MagicMock()
        # Return a generic success/failure response for any command
        ssh.execute.return_value = {
            "stdout": "",
            "stderr": "not found",
            "exit_code": 1,
        }

        checker = WordPressChecker(ssh_manager=ssh, site_path="/var/www/html")
        results = checker.run_all()

        assert isinstance(results, list)
        assert len(results) > 0

        expected_keys = {"name", "status", "severity", "details", "fix"}
        for r in results:
            assert isinstance(r, dict)
            assert expected_keys.issubset(r.keys()), (
                f"Result {r.get('name', '?')} missing keys: {expected_keys - set(r.keys())}"
            )
