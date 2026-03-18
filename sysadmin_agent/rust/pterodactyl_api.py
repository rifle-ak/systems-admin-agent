"""Pterodactyl Panel API client.

Supports both the Application API (admin) and Client API (server owner).
Used for managing Rust game servers hosted on Pterodactyl/Wings.

API reference:
  https://dashflo.net/docs/api/pterodactyl/v1/
"""

import json
import logging
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class PterodactylAPIError(Exception):
    """API request failed."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class PterodactylAPI:
    """Client for the Pterodactyl Panel API.

    Supports both Application API (admin key) and Client API (user key).

    Usage::

        api = PterodactylAPI("https://panel.example.com", "ptlc_xxxxx")
        servers = api.list_servers()
        api.send_command("abc123", "say Hello")
        api.set_power_state("abc123", "restart")
    """

    def __init__(self, panel_url, api_key, timeout=15):
        self.panel_url = panel_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # Determine API type from key prefix
        self._is_application = api_key.startswith("ptla_")
        self._base = f"{self.panel_url}/api/{'application' if self._is_application else 'client'}"

    # ------------------------------------------------------------------
    # Generic request helpers
    # ------------------------------------------------------------------

    def _request(self, method, endpoint, data=None):
        """Make an authenticated API request."""
        url = f"{self._base}/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        body = json.dumps(data).encode("utf-8") if data else None
        req = Request(url, data=body, headers=headers, method=method)

        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                if raw:
                    return json.loads(raw)
                return {}
        except HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")
            except Exception:
                pass
            raise PterodactylAPIError(
                f"API {method} {endpoint} failed: HTTP {e.code}",
                status_code=e.code,
                response_body=body,
            )
        except URLError as e:
            raise PterodactylAPIError(f"Connection failed: {e.reason}")
        except Exception as e:
            raise PterodactylAPIError(f"Request failed: {e}")

    def _get(self, endpoint):
        return self._request("GET", endpoint)

    def _post(self, endpoint, data=None):
        return self._request("POST", endpoint, data)

    def _put(self, endpoint, data=None):
        return self._request("PUT", endpoint, data)

    def _delete(self, endpoint):
        return self._request("DELETE", endpoint)

    # ------------------------------------------------------------------
    # Server management (Client API)
    # ------------------------------------------------------------------

    def list_servers(self) -> list:
        """List all servers the API key has access to."""
        resp = self._get("/")
        data = resp.get("data", [])
        servers = []
        for item in data:
            attrs = item.get("attributes", {})
            servers.append({
                "identifier": attrs.get("identifier", ""),
                "uuid": attrs.get("uuid", ""),
                "name": attrs.get("name", ""),
                "description": attrs.get("description", ""),
                "status": attrs.get("status"),
                "is_suspended": attrs.get("is_suspended", False),
                "limits": attrs.get("limits", {}),
                "feature_limits": attrs.get("feature_limits", {}),
            })
        return servers

    def get_server(self, server_id) -> dict:
        """Get details for a specific server."""
        resp = self._get(f"/servers/{server_id}")
        return resp.get("attributes", resp)

    def get_resources(self, server_id) -> dict:
        """Get current resource usage (CPU, memory, disk, network, uptime)."""
        resp = self._get(f"/servers/{server_id}/resources")
        attrs = resp.get("attributes", resp)
        return {
            "current_state": attrs.get("current_state", "unknown"),
            "is_suspended": attrs.get("is_suspended", False),
            "resources": attrs.get("resources", {}),
        }

    def send_command(self, server_id, command) -> dict:
        """Send a console command to the server."""
        return self._post(f"/servers/{server_id}/command", {"command": command})

    def set_power_state(self, server_id, signal) -> dict:
        """Change server power state. Signal: start, stop, restart, kill."""
        if signal not in ("start", "stop", "restart", "kill"):
            raise ValueError(f"Invalid power signal: {signal}")
        return self._post(f"/servers/{server_id}/power", {"signal": signal})

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def list_files(self, server_id, directory="/") -> list:
        """List files in a server directory."""
        resp = self._get(f"/servers/{server_id}/files/list?directory={directory}")
        data = resp.get("data", [])
        files = []
        for item in data:
            attrs = item.get("attributes", {})
            files.append({
                "name": attrs.get("name", ""),
                "mode": attrs.get("mode", ""),
                "size": attrs.get("size", 0),
                "is_file": attrs.get("is_file", True),
                "is_symlink": attrs.get("is_symlink", False),
                "mimetype": attrs.get("mimetype", ""),
                "created_at": attrs.get("created_at", ""),
                "modified_at": attrs.get("modified_at", ""),
            })
        return files

    def get_file_contents(self, server_id, file_path) -> str:
        """Read a file from the server."""
        resp = self._get(f"/servers/{server_id}/files/contents?file={file_path}")
        # This endpoint returns raw text, not JSON
        if isinstance(resp, dict):
            return json.dumps(resp)
        return str(resp)

    def write_file(self, server_id, file_path, content) -> dict:
        """Write content to a file on the server."""
        url = f"{self._base}/servers/{server_id}/files/write?file={file_path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "text/plain",
        }
        body = content.encode("utf-8")
        req = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return {"status": "ok"}
        except HTTPError as e:
            raise PterodactylAPIError(
                f"Write file failed: HTTP {e.code}",
                status_code=e.code,
            )

    # ------------------------------------------------------------------
    # Server startup / variables
    # ------------------------------------------------------------------

    def get_startup(self, server_id) -> dict:
        """Get server startup variables."""
        resp = self._get(f"/servers/{server_id}/startup")
        data = resp.get("data", [])
        variables = {}
        for item in data:
            attrs = item.get("attributes", {})
            variables[attrs.get("env_variable", "")] = {
                "name": attrs.get("name", ""),
                "description": attrs.get("description", ""),
                "value": attrs.get("server_value", attrs.get("default_value", "")),
                "default": attrs.get("default_value", ""),
                "rules": attrs.get("rules", ""),
            }
        return variables

    def update_startup_variable(self, server_id, key, value) -> dict:
        """Update a startup variable."""
        return self._put(f"/servers/{server_id}/startup/variable", {
            "key": key,
            "value": str(value),
        })

    # ------------------------------------------------------------------
    # Databases
    # ------------------------------------------------------------------

    def list_databases(self, server_id) -> list:
        """List databases for a server."""
        resp = self._get(f"/servers/{server_id}/databases")
        data = resp.get("data", [])
        return [item.get("attributes", {}) for item in data]

    # ------------------------------------------------------------------
    # Schedules
    # ------------------------------------------------------------------

    def list_schedules(self, server_id) -> list:
        """List scheduled tasks for a server."""
        resp = self._get(f"/servers/{server_id}/schedules")
        data = resp.get("data", [])
        return [item.get("attributes", {}) for item in data]

    # ------------------------------------------------------------------
    # Backups
    # ------------------------------------------------------------------

    def list_backups(self, server_id) -> list:
        """List server backups."""
        resp = self._get(f"/servers/{server_id}/backups")
        data = resp.get("data", [])
        return [item.get("attributes", {}) for item in data]

    def create_backup(self, server_id, name=None) -> dict:
        """Create a new server backup."""
        payload = {}
        if name:
            payload["name"] = name
        return self._post(f"/servers/{server_id}/backups", payload)

    # ------------------------------------------------------------------
    # Convenience: Rust-specific helpers
    # ------------------------------------------------------------------

    def rust_wipe_map(self, server_id) -> dict:
        """Delete map files to force a map wipe on next restart."""
        files = self.list_files(server_id, "/server/rust")
        map_files = [
            f["name"] for f in files
            if f["is_file"] and (
                f["name"].endswith(".map") or
                f["name"].endswith(".sav") or
                f["name"].startswith("proceduralmap")
            )
        ]
        for name in map_files:
            try:
                self._post(f"/servers/{server_id}/files/delete", {
                    "root": "/server/rust",
                    "files": [name],
                })
            except PterodactylAPIError:
                logger.warning("Failed to delete map file: %s", name)

        return {"deleted": map_files}

    def rust_get_server_cfg(self, server_id) -> str:
        """Read the Rust server.cfg file."""
        try:
            return self.get_file_contents(server_id, "/server/rust/cfg/server.cfg")
        except PterodactylAPIError:
            # Try alternate path
            try:
                return self.get_file_contents(server_id, "/server/rust/server.cfg")
            except PterodactylAPIError:
                return ""

    def rust_list_oxide_plugins(self, server_id) -> list:
        """List Oxide plugin files on disk."""
        try:
            files = self.list_files(server_id, "/server/rust/oxide/plugins")
            return [f for f in files if f["is_file"] and f["name"].endswith(".cs")]
        except PterodactylAPIError:
            return []

    def rust_get_oxide_config(self, server_id, plugin_name) -> str:
        """Read an Oxide plugin config file."""
        path = f"/server/rust/oxide/config/{plugin_name}.json"
        try:
            return self.get_file_contents(server_id, path)
        except PterodactylAPIError:
            return ""

    def rust_write_oxide_config(self, server_id, plugin_name, config) -> dict:
        """Write an Oxide plugin config file."""
        path = f"/server/rust/oxide/config/{plugin_name}.json"
        if isinstance(config, dict):
            config = json.dumps(config, indent=2)
        return self.write_file(server_id, path, config)

    def rust_get_oxide_logs(self, server_id, limit=50) -> list:
        """List recent Oxide log files."""
        try:
            files = self.list_files(server_id, "/server/rust/oxide/logs")
            log_files = sorted(
                [f for f in files if f["is_file"]],
                key=lambda f: f.get("modified_at", ""),
                reverse=True,
            )
            return log_files[:limit]
        except PterodactylAPIError:
            return []
