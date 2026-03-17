"""Tests for the application discovery module."""

import pytest
from unittest.mock import MagicMock

from sysadmin_agent.discovery.app_discovery import AppDiscovery


def make_mock_ssh(responses):
    """Create a mock SSH manager with canned responses.

    Args:
        responses: dict mapping command substrings to (stdout, stderr, exit_code).
    """
    mock_ssh = MagicMock()
    mock_ssh.is_connected = True

    def fake_execute(command, timeout=15):
        for key, (stdout, stderr, code) in responses.items():
            if key in command:
                return {"stdout": stdout, "stderr": stderr, "exit_code": code}
        return {"stdout": "", "stderr": "", "exit_code": 1}

    mock_ssh.execute = MagicMock(side_effect=fake_execute)
    mock_ssh.execute_sudo = MagicMock(side_effect=fake_execute)
    return mock_ssh


WORDPRESS_RESPONSES = {
    "find": (
        "/var/www/html/wp-config.php\n/var/www/blog/wp-config.php\n",
        "",
        0,
    ),
    "wp_version": ("$wp_version = '6.4.2';\n", "", 0),
    "nginx -v": ("nginx version: nginx/1.24.0\n", "", 0),
    "apache2 -v": ("", "command not found", 127),
    "httpd -v": ("", "command not found", 127),
    "litespeed": ("", "command not found", 127),
    "caddy": ("", "command not found", 127),
    "docker": ("", "command not found", 127),
    "podman": ("", "command not found", 127),
    "systemctl list-units": (
        "nginx.service loaded active running A high performance web server\n",
        "",
        0,
    ),
    "mysql --version": ("mysql  Ver 8.0.35\n", "", 0),
    "psql": ("", "command not found", 127),
    "mongod": ("", "command not found", 127),
    "redis": ("", "command not found", 127),
    "php --version": ("PHP 8.2.13\n", "", 0),
    "python": ("Python 3.11.6\n", "", 0),
    "node": ("", "command not found", 127),
    "ruby": ("", "command not found", 127),
    "java": ("", "command not found", 127),
    "go version": ("", "command not found", 127),
    "perl": ("", "command not found", 127),
    "which": ("", "", 1),
    "cpanel": ("", "command not found", 127),
    "plesk": ("", "command not found", 127),
    "webmin": ("", "", 1),
    "directadmin": ("", "", 1),
    "cyberpanel": ("", "", 1),
    "vesta": ("", "", 1),
    "hestia": ("", "", 1),
    "ispconfig": ("", "", 1),
    "clp": ("", "", 1),
    "joomla": ("", "", 1),
    "core.services.yml": ("", "", 0),
    "magento": ("", "", 0),
    "service --status-all": ("", "", 1),
    "pgrep": ("", "", 1),
    "sqlite3": ("", "command not found", 127),
    "memcached": ("", "command not found", 127),
    "mariadb": ("", "command not found", 127),
}

DOCKER_RESPONSES = {
    "docker --version": ("Docker version 24.0.7, build afdd53b\n", "", 0),
    "docker ps": (
        "abc123\tweb-proxy\tnginx:latest\tUp 3 hours\n"
        "def456\tcache-01\tredis:7\tUp 3 hours\n"
        "ghi789\tmain-db\tpostgres:15\tUp 3 hours\n",
        "",
        0,
    ),
    "podman": ("", "command not found", 127),
    "find": ("", "", 0),
    "nginx": ("", "command not found", 127),
    "apache2": ("", "command not found", 127),
    "httpd": ("", "command not found", 127),
    "litespeed": ("", "command not found", 127),
    "caddy": ("", "command not found", 127),
    "systemctl list-units": ("", "", 0),
    "mysql": ("", "command not found", 127),
    "psql": ("", "command not found", 127),
    "mongod": ("", "command not found", 127),
    "redis": ("", "command not found", 127),
    "php": ("", "command not found", 127),
    "python": ("", "command not found", 127),
    "node": ("", "command not found", 127),
    "ruby": ("", "command not found", 127),
    "java": ("", "command not found", 127),
    "go version": ("", "command not found", 127),
    "perl": ("", "command not found", 127),
    "which": ("/usr/bin/docker\n", "", 0),
    "cpanel": ("", "", 1),
    "plesk": ("", "", 1),
    "webmin": ("", "", 1),
    "directadmin": ("", "", 1),
    "cyberpanel": ("", "", 1),
    "vesta": ("", "", 1),
    "hestia": ("", "", 1),
    "ispconfig": ("", "", 1),
    "clp": ("", "", 1),
    "joomla": ("", "", 1),
    "core.services.yml": ("", "", 0),
    "magento": ("", "", 0),
    "service --status-all": ("", "", 1),
    "pgrep": ("", "", 1),
    "sqlite3": ("", "command not found", 127),
    "memcached": ("", "command not found", 127),
    "mariadb": ("", "command not found", 127),
}

WEBSERVER_RESPONSES = {
    "nginx -v": ("nginx version: nginx/1.24.0\n", "", 0),
    "apache2 -v": (
        "Server version: Apache/2.4.58 (Ubuntu)\nServer built: 2023-10-26\n",
        "",
        0,
    ),
    "httpd -v": ("", "command not found", 127),
    "litespeed": ("", "command not found", 127),
    "caddy": ("", "command not found", 127),
    "docker": ("", "command not found", 127),
    "podman": ("", "command not found", 127),
    "find": ("", "", 0),
    "systemctl list-units": (
        "nginx.service  loaded active running A high performance web server\n"
        "apache2.service loaded active running The Apache HTTP Server\n",
        "",
        0,
    ),
    "mysql": ("", "command not found", 127),
    "psql": ("", "command not found", 127),
    "mongod": ("", "command not found", 127),
    "redis": ("", "command not found", 127),
    "php": ("", "command not found", 127),
    "python": ("", "command not found", 127),
    "node": ("", "command not found", 127),
    "ruby": ("", "command not found", 127),
    "java": ("", "command not found", 127),
    "go version": ("", "command not found", 127),
    "perl": ("", "command not found", 127),
    "which": ("", "", 1),
    "cpanel": ("", "", 1),
    "plesk": ("", "", 1),
    "webmin": ("", "", 1),
    "directadmin": ("", "", 1),
    "cyberpanel": ("", "", 1),
    "vesta": ("", "", 1),
    "hestia": ("", "", 1),
    "ispconfig": ("", "", 1),
    "clp": ("", "", 1),
    "joomla": ("", "", 1),
    "core.services.yml": ("", "", 0),
    "magento": ("", "", 0),
    "service --status-all": ("", "", 1),
    "pgrep": ("", "", 1),
    "sqlite3": ("", "command not found", 127),
    "memcached": ("", "command not found", 127),
    "mariadb": ("", "command not found", 127),
}

EMPTY_RESPONSES = {
    "find": ("", "", 0),
    "docker": ("", "command not found", 127),
    "podman": ("", "command not found", 127),
    "nginx": ("", "command not found", 127),
    "apache": ("", "command not found", 127),
    "httpd": ("", "command not found", 127),
    "litespeed": ("", "command not found", 127),
    "caddy": ("", "command not found", 127),
    "systemctl": ("", "", 0),
    "which": ("", "", 1),
    "mysql": ("", "command not found", 127),
    "mariadb": ("", "command not found", 127),
    "psql": ("", "command not found", 127),
    "mongod": ("", "command not found", 127),
    "redis": ("", "command not found", 127),
    "php": ("", "command not found", 127),
    "python": ("", "command not found", 127),
    "node": ("", "command not found", 127),
    "ruby": ("", "command not found", 127),
    "java": ("", "command not found", 127),
    "go version": ("", "command not found", 127),
    "perl": ("", "command not found", 127),
    "cpanel": ("", "", 1),
    "plesk": ("", "", 1),
    "webmin": ("", "", 1),
    "directadmin": ("", "", 1),
    "cyberpanel": ("", "", 1),
    "vesta": ("", "", 1),
    "hestia": ("", "", 1),
    "ispconfig": ("", "", 1),
    "clp": ("", "", 1),
    "joomla": ("", "", 1),
    "core.services.yml": ("", "", 0),
    "magento": ("", "", 0),
    "service --status-all": ("", "", 1),
    "pgrep": ("", "", 1),
    "sqlite3": ("", "command not found", 127),
    "memcached": ("", "command not found", 127),
}


class TestWordPressDiscovery:
    """Test WordPress site discovery."""

    def test_discovers_wordpress_sites(self):
        mock_ssh = make_mock_ssh(WORDPRESS_RESPONSES)
        discovery = AppDiscovery(mock_ssh)
        results = discovery.discover()

        # WordPress sites show up under the "cms" key
        cms = results.get("cms", [])
        wp_entries = [c for c in cms if c.get("name", "").lower() == "wordpress"]
        assert len(wp_entries) >= 1


class TestDockerDiscovery:
    """Test Docker container discovery."""

    def test_discovers_docker_containers(self):
        mock_ssh = make_mock_ssh(DOCKER_RESPONSES)
        discovery = AppDiscovery(mock_ssh)
        results = discovery.discover()

        containers = results.get("containers", [])
        docker_entries = [c for c in containers if c.get("name") == "docker"]
        assert len(docker_entries) >= 1
        assert len(docker_entries[0].get("containers", [])) >= 1


class TestWebServerDiscovery:
    """Test web server discovery."""

    def test_discovers_nginx_and_apache(self):
        mock_ssh = make_mock_ssh(WEBSERVER_RESPONSES)
        discovery = AppDiscovery(mock_ssh)
        results = discovery.discover()

        web_servers = results.get("web_servers", [])
        names = [s["name"] for s in web_servers]
        assert "nginx" in names or "apache2" in names


class TestEmptyDiscovery:
    """Test discovery when nothing is found."""

    def test_handles_empty_results(self):
        mock_ssh = make_mock_ssh(EMPTY_RESPONSES)
        discovery = AppDiscovery(mock_ssh)
        results = discovery.discover()

        assert results is not None
        assert isinstance(results, dict)
        # All category values should be empty lists
        for category, items in results.items():
            assert isinstance(items, list)
            assert len(items) == 0, f"Expected empty list for '{category}', got {items}"
