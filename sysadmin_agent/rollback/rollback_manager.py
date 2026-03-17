import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path


COMMON_CONFIG_PATTERNS = [
    r"/etc/[\w./-]+",
    r"/var/[\w./-]+\.conf",
    r"/opt/[\w./-]+\.conf",
    r"~?/\.[\w./-]+",
]

REDIRECT_PATTERN = re.compile(r">{1,2}\s*(\S+)")
FILE_CMD_PATTERN = re.compile(r"\b(?:cp|mv|rm|install)\s+.*?(\S+)\s*$")
ECHO_TEE_PATTERN = re.compile(r"\btee\s+(?:-a\s+)?(\S+)")
SYSTEMCTL_PATTERN = re.compile(r"\bsystemctl\s+\w+\s+([\w@.-]+)")
PACKAGE_PATTERN = re.compile(r"\b(?:apt|apt-get|yum|dnf)\s+(?:install|remove|purge|update|upgrade)\b")


class RollbackManager:
    def __init__(self, ssh_manager, snapshot_dir=".sysadmin-snapshots"):
        self._ssh = ssh_manager
        self._snapshot_dir = Path(snapshot_dir)
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._registry_path = self._snapshot_dir / "snapshots.json"
        self._snapshots = self._load_registry()

    def create_snapshot(self, command, description=""):
        snapshot_id = str(uuid.uuid4())
        snapshot_path = self._snapshot_dir / snapshot_id
        snapshot_path.mkdir(parents=True, exist_ok=True)

        affected_files = self._analyze_affected_files(command)
        backed_up_files = []

        for remote_path in affected_files:
            try:
                safe_name = remote_path.replace("/", "_").lstrip("_")
                local_path = snapshot_path / safe_name
                self._ssh.download_file(remote_path, str(local_path))

                # Capture permissions
                result = self._ssh.execute(f"stat -c '%a %U %G' {remote_path}")
                perms = result["stdout"].strip() if result["exit_code"] == 0 else None

                backed_up_files.append({
                    "remote_path": remote_path,
                    "local_file": safe_name,
                    "permissions": perms,
                })
            except Exception:
                continue

        service_states = self._capture_service_state(command)
        package_state = self._capture_package_state(command)

        metadata = {
            "id": snapshot_id,
            "command": command,
            "description": description,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "active",
            "files": backed_up_files,
            "service_states": service_states,
            "package_state": package_state,
        }

        self._snapshots[snapshot_id] = metadata
        self._save_registry()
        return snapshot_id

    def rollback(self, snapshot_id):
        snapshot = self._snapshots.get(snapshot_id)
        if not snapshot:
            raise ValueError(f"Snapshot {snapshot_id} not found")

        results = []
        snapshot_path = self._snapshot_dir / snapshot_id

        for file_info in snapshot.get("files", []):
            remote_path = file_info["remote_path"]
            local_file = snapshot_path / file_info["local_file"]

            if not local_file.exists():
                results.append({"file": remote_path, "status": "missing_backup"})
                continue

            try:
                self._ssh.upload_file(str(local_file), remote_path)

                if file_info.get("permissions"):
                    parts = file_info["permissions"].split()
                    if len(parts) == 3:
                        mode, owner, group = parts
                        self._ssh.execute(f"chmod {mode} {remote_path}")
                        self._ssh.execute(f"chown {owner}:{group} {remote_path}")

                results.append({"file": remote_path, "status": "restored"})
            except Exception as e:
                results.append({"file": remote_path, "status": "error", "error": str(e)})

        for svc in snapshot.get("service_states", []):
            try:
                name = svc["name"]
                was_active = svc.get("active", False)
                target_action = "start" if was_active else "stop"
                self._ssh.execute(f"systemctl {target_action} {name}")
                results.append({"service": name, "status": f"{target_action}ed"})
            except Exception as e:
                results.append({"service": svc.get("name"), "status": "error", "error": str(e)})

        snapshot["status"] = "rolled_back"
        self._save_registry()
        return results

    def list_snapshots(self):
        return [
            {
                "id": s["id"],
                "command": s["command"],
                "description": s["description"],
                "timestamp": s["timestamp"],
                "status": s["status"],
                "file_count": len(s.get("files", [])),
            }
            for s in self._snapshots.values()
        ]

    def get_snapshot(self, snapshot_id):
        return self._snapshots.get(snapshot_id)

    def remove_snapshot(self, snapshot_id):
        if snapshot_id not in self._snapshots:
            return False

        snapshot_path = self._snapshot_dir / snapshot_id
        if snapshot_path.exists():
            shutil.rmtree(snapshot_path)

        del self._snapshots[snapshot_id]
        self._save_registry()
        return True

    def _analyze_affected_files(self, command):
        files = set()

        for pattern in COMMON_CONFIG_PATTERNS:
            for match in re.finditer(pattern, command):
                files.add(match.group(0))

        for match in REDIRECT_PATTERN.finditer(command):
            path = match.group(1)
            if path.startswith("/"):
                files.add(path)

        for match in ECHO_TEE_PATTERN.finditer(command):
            path = match.group(1)
            if path.startswith("/"):
                files.add(path)

        for match in FILE_CMD_PATTERN.finditer(command):
            path = match.group(1)
            if path.startswith("/"):
                files.add(path)

        return sorted(files)

    def _capture_service_state(self, command):
        states = []
        for match in SYSTEMCTL_PATTERN.finditer(command):
            service_name = match.group(1)
            try:
                result = self._ssh.execute(f"systemctl is-active {service_name}")
                active = result["stdout"].strip() == "active"
                states.append({"name": service_name, "active": active})
            except Exception:
                states.append({"name": service_name, "active": None})
        return states

    def _capture_package_state(self, command):
        if not PACKAGE_PATTERN.search(command):
            return None

        try:
            result = self._ssh.execute("dpkg --get-selections 2>/dev/null || rpm -qa 2>/dev/null")
            if result["exit_code"] == 0:
                return result["stdout"]
        except Exception:
            pass
        return None

    def _load_registry(self):
        if self._registry_path.exists():
            try:
                data = json.loads(self._registry_path.read_text())
                return {s["id"]: s for s in data}
            except (json.JSONDecodeError, KeyError):
                return {}
        return {}

    def _save_registry(self):
        data = list(self._snapshots.values())
        self._registry_path.write_text(json.dumps(data, indent=2))
