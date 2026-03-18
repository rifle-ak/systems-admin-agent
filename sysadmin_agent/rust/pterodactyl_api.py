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
        # Application API is for admin panel management
        # Client API is for server-level operations (resources, console, files, etc.)
        # Both share the same Bearer token auth — a ptla_ key can also hit client endpoints
        self._app_base = f"{self.panel_url}/api/application"
        self._client_base = f"{self.panel_url}/api/client"
        # Default base depends on key type
        self._base = self._app_base if self._is_application else self._client_base
        # Detected at runtime by list_servers()
        self._client_api_available = not self._is_application

    # ------------------------------------------------------------------
    # Generic request helpers
    # ------------------------------------------------------------------

    def _request(self, method, endpoint, data=None, raw_response=False,
                 use_client=False):
        """Make an authenticated API request."""
        # Build URL: strip slashes to avoid double-slash issues
        base = self._client_base if use_client else self._base
        endpoint = endpoint.strip("/")
        if endpoint:
            url = f"{base}/{endpoint}"
        else:
            url = base

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        body = json.dumps(data).encode("utf-8") if data else None
        req = Request(url, data=body, headers=headers, method=method)

        logger.debug("Pterodactyl %s %s", method, url)

        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                if raw_response:
                    return raw
                if raw:
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        return {"_raw": raw}
                return {}
        except HTTPError as e:
            resp_body = ""
            try:
                resp_body = e.read().decode("utf-8")
            except Exception:
                pass
            raise PterodactylAPIError(
                f"API {method} {url} failed: HTTP {e.code}",
                status_code=e.code,
                response_body=resp_body,
            )
        except URLError as e:
            raise PterodactylAPIError(f"Connection to {self.panel_url} failed: {e.reason}")
        except Exception as e:
            raise PterodactylAPIError(f"Request failed: {e}")

    def _get(self, endpoint, raw_response=False, use_client=False):
        return self._request("GET", endpoint, raw_response=raw_response,
                             use_client=use_client)

    def _post(self, endpoint, data=None, use_client=False):
        return self._request("POST", endpoint, data, use_client=use_client)

    def _put(self, endpoint, data=None, use_client=False):
        return self._request("PUT", endpoint, data, use_client=use_client)

    def _delete(self, endpoint, use_client=False):
        return self._request("DELETE", endpoint, use_client=use_client)

    # ------------------------------------------------------------------
    # Server management (Client API)
    # ------------------------------------------------------------------

    def list_servers(self) -> list:
        """List all servers the API key has access to.

        Tries Client API first (returns short identifiers needed for most
        operations).  Falls back to Application API if the key doesn't have
        client scope.
        """
        # Try Client API first
        try:
            resp = self._get("", use_client=True)
            self._client_api_available = True
        except PterodactylAPIError as e:
            if e.status_code in (401, 403):
                # Client API not available with this key — use Application API
                resp = self._get("servers")
                self._client_api_available = False
            else:
                raise

        data = resp.get("data", [])
        servers = []
        for item in data:
            attrs = item.get("attributes", {})
            servers.append({
                # Short identifier (e.g. "dd4f6272") — use this for Client API calls
                "identifier": attrs.get("identifier", ""),
                "uuid": attrs.get("uuid", ""),
                # Application API uses numeric "id" instead of "identifier"
                "internal_id": attrs.get("id", ""),
                "name": attrs.get("name", ""),
                "description": attrs.get("description", ""),
                "status": attrs.get("status"),
                "is_suspended": attrs.get("is_suspended", False),
                "limits": attrs.get("limits", {}),
                "feature_limits": attrs.get("feature_limits", {}),
            })
        return servers

    def _require_client_api(self, action="this operation"):
        """Raise a helpful error if Client API is not available."""
        if not self._client_api_available:
            raise PterodactylAPIError(
                f"Cannot {action}: requires a Client API key (ptlc_...). "
                f"Your Application API key (ptla_) can list servers but cannot "
                f"manage them. Create a Client API key in the Pterodactyl panel "
                f"under your Account > API Credentials.",
                status_code=403,
            )

    def get_server(self, server_id) -> dict:
        """Get details for a specific server (Client API)."""
        self._require_client_api("get server details")
        resp = self._get(f"/servers/{server_id}", use_client=True)
        return resp.get("attributes", resp)

    def get_resources(self, server_id) -> dict:
        """Get current resource usage (CPU, memory, disk, network, uptime).
        This is a Client API endpoint — always routes through /api/client."""
        self._require_client_api("get server resources")
        resp = self._get(f"/servers/{server_id}/resources", use_client=True)
        attrs = resp.get("attributes", resp)
        return {
            "current_state": attrs.get("current_state", "unknown"),
            "is_suspended": attrs.get("is_suspended", False),
            "resources": attrs.get("resources", {}),
        }

    def send_command(self, server_id, command) -> dict:
        """Send a console command to the server (Client API)."""
        self._require_client_api("send console command")
        return self._post(f"/servers/{server_id}/command",
                          {"command": command}, use_client=True)

    def set_power_state(self, server_id, signal) -> dict:
        """Change server power state (Client API). Signal: start, stop, restart, kill."""
        self._require_client_api("change power state")
        if signal not in ("start", "stop", "restart", "kill"):
            raise ValueError(f"Invalid power signal: {signal}")
        return self._post(f"/servers/{server_id}/power",
                          {"signal": signal}, use_client=True)

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def list_files(self, server_id, directory="/") -> list:
        """List files in a server directory (Client API)."""
        self._require_client_api("list files")
        from urllib.parse import quote
        encoded_dir = quote(directory, safe="/")
        resp = self._get(f"/servers/{server_id}/files/list?directory={encoded_dir}",
                         use_client=True)
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
        from urllib.parse import quote
        encoded_path = quote(file_path, safe="/")
        resp = self._get(
            f"servers/{server_id}/files/contents?file={encoded_path}",
            raw_response=True, use_client=True,
        )
        return resp if isinstance(resp, str) else str(resp)

    def write_file(self, server_id, file_path, content) -> dict:
        """Write content to a file on the server (Client API)."""
        from urllib.parse import quote
        encoded_path = quote(file_path, safe="/")
        url = f"{self._client_base}/servers/{server_id}/files/write?file={encoded_path}"
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
        """Get server startup variables (Client API)."""
        resp = self._get(f"/servers/{server_id}/startup", use_client=True)
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
        """Update a startup variable (Client API)."""
        return self._put(f"/servers/{server_id}/startup/variable", {
            "key": key,
            "value": str(value),
        }, use_client=True)

    # ------------------------------------------------------------------
    # Databases
    # ------------------------------------------------------------------

    def list_databases(self, server_id) -> list:
        """List databases for a server (Client API)."""
        resp = self._get(f"/servers/{server_id}/databases", use_client=True)
        data = resp.get("data", [])
        return [item.get("attributes", {}) for item in data]

    # ------------------------------------------------------------------
    # Schedules
    # ------------------------------------------------------------------

    def list_schedules(self, server_id) -> list:
        """List scheduled tasks for a server (Client API)."""
        resp = self._get(f"/servers/{server_id}/schedules", use_client=True)
        data = resp.get("data", [])
        return [item.get("attributes", {}) for item in data]

    # ------------------------------------------------------------------
    # Backups
    # ------------------------------------------------------------------

    def list_backups(self, server_id) -> list:
        """List server backups (Client API)."""
        resp = self._get(f"/servers/{server_id}/backups", use_client=True)
        data = resp.get("data", [])
        return [item.get("attributes", {}) for item in data]

    def create_backup(self, server_id, name=None) -> dict:
        """Create a new server backup (Client API)."""
        payload = {}
        if name:
            payload["name"] = name
        return self._post(f"/servers/{server_id}/backups", payload,
                          use_client=True)

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
                }, use_client=True)
            except PterodactylAPIError:
                logger.warning("Failed to delete map file: %s", name)

        return {"deleted": map_files}

    def rust_get_server_cfg(self, server_id) -> str:
        """Read the Rust server configuration file.

        Checks multiple paths: server.cfg, serverauto.cfg, and alternate
        directory layouts.
        """
        cfg_paths = [
            "/server/rust/cfg/server.cfg",
            "/server/rust/server.cfg",
            "/server/rust/cfg/serverauto.cfg",
        ]
        for path in cfg_paths:
            try:
                content = self.get_file_contents(server_id, path)
                if content and content.strip():
                    return content
            except PterodactylAPIError:
                continue
        return ""

    def _discover_oxide_root(self, server_id) -> str | None:
        """Discover the actual Oxide root directory by listing /server/rust/.

        Oxide can be installed as 'oxide', 'Oxide', or other casings.
        Returns the full path like '/server/rust/oxide' or None.
        """
        try:
            files = self.list_files(server_id, "/server/rust")
            for f in files:
                if not f["is_file"] and f["name"].lower() == "oxide":
                    return f"/server/rust/{f['name']}"
        except PterodactylAPIError:
            pass
        return None

    def _discover_oxide_subdirs(self, server_id, oxide_root) -> dict:
        """List subdirectories of the Oxide root and map them by purpose.

        Returns a dict like {'plugins': '/server/rust/oxide/plugins',
                             'logs': '/server/rust/oxide/logs', ...}
        """
        result = {}
        try:
            files = self.list_files(server_id, oxide_root)
            for f in files:
                if f["is_file"]:
                    continue
                name_lower = f["name"].lower()
                full_path = f"{oxide_root}/{f['name']}"
                if name_lower == "plugins":
                    result["plugins"] = full_path
                elif name_lower == "logs":
                    result["logs"] = full_path
                elif name_lower == "config":
                    result["config"] = full_path
                elif name_lower == "data":
                    result["data"] = full_path
                elif name_lower == "lang":
                    result["lang"] = full_path
        except PterodactylAPIError:
            pass
        return result

    def rust_list_oxide_plugins(self, server_id) -> list:
        """List Oxide plugin files on disk.

        Discovers the actual Oxide directory structure dynamically.
        """
        # Dynamic discovery
        oxide_root = self._discover_oxide_root(server_id)
        if oxide_root:
            subdirs = self._discover_oxide_subdirs(server_id, oxide_root)
            if "plugins" in subdirs:
                try:
                    files = self.list_files(server_id, subdirs["plugins"])
                    plugins = [f for f in files if f["is_file"] and f["name"].endswith(".cs")]
                    if plugins:
                        return plugins
                except PterodactylAPIError:
                    pass

        # Fallback to hardcoded paths
        plugin_paths = [
            "/server/rust/oxide/plugins",
            "/server/rust/Oxide/Plugins",
            "/server/rust/Oxide/plugins",
            "/server/rust/oxide/Plugins",
        ]
        for path in plugin_paths:
            try:
                files = self.list_files(server_id, path)
                plugins = [f for f in files if f["is_file"] and f["name"].endswith(".cs")]
                if plugins:
                    return plugins
            except PterodactylAPIError:
                continue
        return []

    def rust_get_oxide_config(self, server_id, plugin_name) -> str:
        """Read an Oxide plugin config file."""
        # Dynamic discovery
        oxide_root = self._discover_oxide_root(server_id)
        if oxide_root:
            subdirs = self._discover_oxide_subdirs(server_id, oxide_root)
            if "config" in subdirs:
                try:
                    content = self.get_file_contents(
                        server_id, f"{subdirs['config']}/{plugin_name}.json")
                    if content:
                        return content
                except PterodactylAPIError:
                    pass

        # Fallback
        config_paths = [
            f"/server/rust/oxide/config/{plugin_name}.json",
            f"/server/rust/Oxide/Config/{plugin_name}.json",
            f"/server/rust/Oxide/config/{plugin_name}.json",
        ]
        for path in config_paths:
            try:
                content = self.get_file_contents(server_id, path)
                if content:
                    return content
            except PterodactylAPIError:
                continue
        return ""

    def rust_write_oxide_config(self, server_id, plugin_name, config) -> dict:
        """Write an Oxide plugin config file."""
        # Dynamic discovery for the right config path
        write_path = None
        oxide_root = self._discover_oxide_root(server_id)
        if oxide_root:
            subdirs = self._discover_oxide_subdirs(server_id, oxide_root)
            if "config" in subdirs:
                write_path = f"{subdirs['config']}/{plugin_name}.json"

        if not write_path:
            # Fallback: try to find the existing config path
            config_paths = [
                f"/server/rust/oxide/config/{plugin_name}.json",
                f"/server/rust/Oxide/Config/{plugin_name}.json",
                f"/server/rust/Oxide/config/{plugin_name}.json",
            ]
            write_path = config_paths[0]
            for path in config_paths:
                try:
                    existing = self.get_file_contents(server_id, path)
                    if existing:
                        write_path = path
                        break
                except PterodactylAPIError:
                    continue

        if isinstance(config, dict):
            config = json.dumps(config, indent=2)
        return self.write_file(server_id, write_path, config)

    def rust_get_oxide_logs(self, server_id, limit=50) -> list:
        """List recent Oxide log files.

        Discovers the actual Oxide directory structure dynamically,
        then falls back to hardcoded path variations.
        """
        # Dynamic discovery
        oxide_root = self._discover_oxide_root(server_id)
        if oxide_root:
            subdirs = self._discover_oxide_subdirs(server_id, oxide_root)
            if "logs" in subdirs:
                try:
                    files = self.list_files(server_id, subdirs["logs"])
                    log_files = sorted(
                        [f for f in files if f["is_file"]],
                        key=lambda f: f.get("modified_at", ""),
                        reverse=True,
                    )
                    if log_files:
                        return log_files[:limit]
                except PterodactylAPIError:
                    pass

        # Fallback to hardcoded paths
        log_paths = [
            "/server/rust/oxide/logs",
            "/server/rust/Oxide/Logs",
            "/server/rust/Oxide/logs",
            "/server/rust/oxide/Logs",
        ]
        for path in log_paths:
            try:
                files = self.list_files(server_id, path)
                log_files = sorted(
                    [f for f in files if f["is_file"]],
                    key=lambda f: f.get("modified_at", ""),
                    reverse=True,
                )
                if log_files:
                    return log_files[:limit]
            except PterodactylAPIError:
                continue
        return []
