"""
Flask + Flask-SocketIO web application for the systems admin agent.

Provides a browser-based UI for connecting to servers, running diagnostics,
chatting with the AI agent, and managing approval/rollback workflows.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import uuid
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_socketio import SocketIO, emit

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
CONFIG_PATH = PROJECT_ROOT / "config.json"
TOKEN_USAGE_PATH = PROJECT_ROOT / "token_usage.json"

# ---------------------------------------------------------------------------
# .env loading (manual, no hard dependency on python-dotenv)
# ---------------------------------------------------------------------------

def _load_dotenv(path: Path) -> dict:
    """Parse a .env file into a dict and inject into os.environ."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            values[key] = value
            os.environ.setdefault(key, value)
    return values


_env_values = _load_dotenv(ENV_PATH)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


server_profiles: dict = _load_config()

# ---------------------------------------------------------------------------
# Flask + SocketIO setup
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=str(Path(__file__).resolve().parent / "templates"),
    static_folder=str(Path(__file__).resolve().parent / "static"),
)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Persistent token usage tracker
# ---------------------------------------------------------------------------
from sysadmin_agent.web.token_tracker import TokenTracker

_token_tracker = TokenTracker(storage_path=str(TOKEN_USAGE_PATH))

# ---------------------------------------------------------------------------
# In-memory session stores (keyed by Flask session id)
# ---------------------------------------------------------------------------

# sid -> SSHManager
_ssh_connections: dict[str, object] = {}
# sid -> dict with server_info, os_info, apps, etc.
_session_data: dict[str, dict] = {}
# sid -> list of conversation messages for AI brain
_conversations: dict[str, list] = {}
# approval_id -> threading.Event + result dict
_pending_approvals: dict[str, dict] = {}
# sid -> AgentBrain (persistent per session for token tracking)
_brains: dict[str, object] = {}
# sid -> RconClient
_rcon_connections: dict[str, object] = {}
# sid -> PterodactylAPI
_ptero_connections: dict[str, object] = {}

APPROVAL_TIMEOUT = 60  # seconds

# ---------------------------------------------------------------------------
# Lazy imports of agent modules (avoids import-time failures during setup)
# ---------------------------------------------------------------------------

def _import_agent_modules():
    """Import heavy agent modules on demand so the setup wizard can run
    even when dependencies are not yet installed."""
    from sysadmin_agent.connection import SSHManager
    from sysadmin_agent.discovery import OSDetector, AppDiscovery
    from sysadmin_agent.diagnostics import DiagnosticEngine
    from sysadmin_agent.approval import ApprovalManager
    from sysadmin_agent.rollback import RollbackManager
    from sysadmin_agent.ai import AgentBrain
    from sysadmin_agent.knowledge import DocFetcher
    return {
        "SSHManager": SSHManager,
        "OSDetector": OSDetector,
        "AppDiscovery": AppDiscovery,
        "DiagnosticEngine": DiagnosticEngine,
        "ApprovalManager": ApprovalManager,
        "RollbackManager": RollbackManager,
        "AgentBrain": AgentBrain,
        "DocFetcher": DocFetcher,
    }


# ---------------------------------------------------------------------------
# WebApprovalManager
# ---------------------------------------------------------------------------

class WebApprovalManager:
    """ApprovalManager replacement that asks the browser user via SocketIO
    instead of a terminal prompt.  Uses threading.Event to block the
    diagnostic/fix thread until the user responds (or timeout)."""

    def __init__(self, sid: str):
        self.auto_approve = False
        self._history: list[dict] = []
        self._sid = sid

    # Same interface as ApprovalManager.request_approval
    def request_approval(self, action: dict) -> bool:
        if self.auto_approve:
            self._record(action, "auto_approved")
            return True

        approval_id = str(uuid.uuid4())
        event = threading.Event()
        result_holder: dict = {"approved": False}

        _pending_approvals[approval_id] = {
            "event": event,
            "result": result_holder,
            "action": action,
            "sid": self._sid,
        }

        # Emit to the specific client
        socketio.emit(
            "approval_required",
            {
                "approval_id": approval_id,
                "command": action.get("command", ""),
                "description": action.get("description", ""),
                "destructive": action.get("destructive", False),
                "snapshot_id": action.get("snapshot_id"),
            },
            to=self._sid,
        )

        # Block until user responds or timeout
        answered = event.wait(timeout=APPROVAL_TIMEOUT)
        _pending_approvals.pop(approval_id, None)

        if not answered:
            socketio.emit(
                "approval_timeout",
                {"approval_id": approval_id},
                to=self._sid,
            )
            self._record(action, "denied_timeout")
            return False

        approved = result_holder["approved"]
        self._record(action, "approved" if approved else "denied")
        return approved

    def get_history(self) -> list:
        return list(self._history)

    def get_stats(self) -> dict:
        total = len(self._history)
        approved = sum(
            1 for h in self._history
            if h["decision"] in ("approved", "auto_approved")
        )
        denied = sum(
            1 for h in self._history
            if h["decision"] in ("denied", "denied_timeout", "denied_non_interactive")
        )
        return {"total": total, "approved": approved, "denied": denied}

    def _record(self, action, decision):
        from datetime import datetime, timezone
        self._history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "decision": decision,
        })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _needs_setup() -> bool:
    """Return True when the app has not been configured yet."""
    if not ENV_PATH.is_file():
        return True
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return True
    return False


def _get_sid() -> str:
    """Return a stable session identifier (stored in Flask session)."""
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return session["sid"]


def _get_flask_sid() -> str:
    """Get session id from Flask request context (for HTTP routes)."""
    return _get_sid()


def _require_auth(fn):
    """Decorator: enforce simple password auth when WEB_PASSWORD is set."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        web_password = os.environ.get("WEB_PASSWORD")
        if web_password:
            if not session.get("authenticated"):
                return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def _get_ssh(sid: str):
    return _ssh_connections.get(sid)


def _get_session_data(sid: str) -> dict:
    if sid not in _session_data:
        _session_data[sid] = {}
    return _session_data[sid]


def _get_conversation(sid: str) -> list:
    if sid not in _conversations:
        _conversations[sid] = []
    return _conversations[sid]


def _get_brain(sid: str):
    """Return a session-persistent AgentBrain so token usage accumulates.
    Also hooks into the global persistent token tracker."""
    if sid not in _brains:
        mods = _import_agent_modules()
        brain = mods["AgentBrain"](
            usage_callback=lambda inp, out: _token_tracker.add_usage(inp, out),
        )
        _brains[sid] = brain
    return _brains[sid]


def _build_server_context(sid: str) -> dict:
    """Build a context dict from what we know about the connected server.

    Mirrors the structured format used by cli._build_server_context so the
    AI brain gets clean, readable context rather than raw JSON dumps.
    """
    data = _get_session_data(sid)
    ctx: dict = {}

    os_info = data.get("os_info")
    if os_info:
        ctx["os"] = (
            f"{os_info.get('distribution', 'unknown')} "
            f"{os_info.get('version', '')}".strip()
        )
        ctx["kernel"] = os_info.get("kernel", "unknown")
        ctx["architecture"] = os_info.get("architecture", "unknown")
        ctx["hostname"] = os_info.get("hostname", "unknown")

    apps = data.get("apps")
    if apps:
        web = [w.get("name", "") for w in apps.get("web_servers", [])]
        if web:
            ctx["web_servers"] = ", ".join(web)
        dbs = [d.get("name", "") for d in apps.get("databases", [])]
        if dbs:
            ctx["databases"] = ", ".join(dbs)
        panels = [p.get("name", "") for p in apps.get("control_panels", [])]
        if panels:
            ctx["control_panels"] = ", ".join(panels)
        cms = [c.get("name", "") for c in apps.get("cms", [])]
        if cms:
            ctx["cms"] = ", ".join(cms)
        langs = [l.get("name", "") for l in apps.get("languages", [])]
        if langs:
            ctx["languages"] = ", ".join(langs)
        containers = [c.get("name", "") for c in apps.get("containers", [])]
        if containers:
            ctx["container_runtimes"] = ", ".join(containers)
        services = apps.get("services", [])
        running = sum(1 for s in services if s.get("status") == "running")
        ctx["services"] = f"{running} running out of {len(services)} total"

    if "diagnostics" in data:
        summary = [
            f"{d['name']}: {d['status']}"
            for d in data["diagnostics"]
            if d.get("status") != "ok"
        ]
        if summary:
            ctx["diagnostic_issues"] = "; ".join(summary)

    # Enrich with documentation context for discovered software
    try:
        mods = _import_agent_modules()
        fetcher = mods["DocFetcher"]()
        software_names = set()
        if os_info:
            dist = os_info.get("distribution", "")
            if dist:
                software_names.add(dist.lower())
        if apps:
            for category in ("web_servers", "databases", "control_panels",
                             "cms", "languages", "containers"):
                for item in apps.get(category, []):
                    name = item.get("name", "")
                    if name:
                        software_names.add(name.lower())
        for name in software_names:
            doc = fetcher.get_context(name)
            if doc:
                ctx[f"docs_{name}"] = json.dumps(doc) if not isinstance(doc, str) else doc
    except Exception:
        pass

    return ctx


# ---------------------------------------------------------------------------
# Required packages for full operation
# ---------------------------------------------------------------------------

REQUIRED_PACKAGES = [
    ("flask", "Flask"),
    ("flask_socketio", "flask-socketio"),
    ("paramiko", "paramiko"),
    ("anthropic", "anthropic"),
    ("rich", "rich"),
    ("click", "click"),
]


def _check_package(import_name: str) -> dict:
    """Check whether a Python package is importable and return info."""
    try:
        mod = __import__(import_name)
        version = getattr(mod, "__version__", "unknown")
        return {"name": import_name, "installed": True, "version": version}
    except ImportError:
        return {"name": import_name, "installed": False, "version": None}


# ---------------------------------------------------------------------------
# HTTP Routes — Setup wizard
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if _needs_setup():
        return redirect(url_for("setup"))
    web_password = os.environ.get("WEB_PASSWORD")
    if web_password and not session.get("authenticated"):
        return redirect(url_for("login"))
    return redirect(url_for("dashboard"))


@app.route("/setup")
def setup():
    return render_template("setup.html")


@app.route("/api/setup/check", methods=["POST"])
def setup_check():
    deps = [_check_package(imp) for imp, _ in REQUIRED_PACKAGES]
    all_installed = all(d["installed"] for d in deps)
    api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return jsonify({
        "python_ok": True,
        "python_version": sys.version,
        "pip_ok": True,
        "dependencies": deps,
        "all_deps_installed": all_installed,
        "env_configured": ENV_PATH.is_file(),
        "api_key_set": api_key_set,
    })


@app.route("/api/setup/install", methods=["POST"])
def setup_install():
    """Install missing pip packages.  Streams progress via SocketIO if a
    socket_sid is provided, otherwise returns JSON when done."""
    missing = []
    for import_name, pip_name in REQUIRED_PACKAGES:
        info = _check_package(import_name)
        if not info["installed"]:
            missing.append(pip_name)

    if not missing:
        return jsonify({"status": "ok", "message": "All packages already installed"})

    socket_sid = request.json.get("socket_sid") if request.is_json else None

    errors = []
    for pkg in missing:
        if socket_sid:
            socketio.emit("setup_progress", {"message": f"Installing {pkg}..."}, to=socket_sid)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--user", pkg],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if socket_sid:
                socketio.emit("setup_progress", {"message": f"{pkg} installed."}, to=socket_sid)
        except subprocess.CalledProcessError as exc:
            msg = f"Failed to install {pkg}: {exc}"
            errors.append(msg)
            if socket_sid:
                socketio.emit("setup_progress", {"message": msg, "error": True}, to=socket_sid)

    if errors:
        return jsonify({"status": "error", "errors": errors}), 500
    return jsonify({"status": "ok", "installed": missing})


@app.route("/api/setup/configure", methods=["POST"])
def setup_configure():
    """Save .env and optional config.json profiles."""
    data = request.get_json(force=True)

    # Build .env content
    env_lines = []
    api_key = data.get("anthropic_api_key", "").strip()
    if api_key:
        env_lines.append(f"ANTHROPIC_API_KEY={api_key}")
        os.environ["ANTHROPIC_API_KEY"] = api_key

    secret = data.get("secret_key", "").strip() or secrets.token_hex(32)
    env_lines.append(f"SECRET_KEY={secret}")
    os.environ["SECRET_KEY"] = secret
    app.secret_key = secret

    web_password = data.get("web_password", "").strip()
    if web_password:
        env_lines.append(f"WEB_PASSWORD={web_password}")
        os.environ["WEB_PASSWORD"] = web_password

    # Extra arbitrary env vars
    for key, value in data.get("extra_env", {}).items():
        env_lines.append(f"{key}={value}")
        os.environ[key] = value

    ENV_PATH.write_text("\n".join(env_lines) + "\n")

    # Server profiles in config.json
    profiles = data.get("server_profiles", {})
    if profiles:
        global server_profiles
        server_profiles = profiles
        _save_config(profiles)

    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# HTTP Routes — Server profiles
# ---------------------------------------------------------------------------

@app.route("/api/profiles", methods=["GET"])
@_require_auth
def list_profiles():
    """Return all saved server profiles as a JSON array.
    Never expose the obfuscated password to the frontend listing."""
    result = []
    for name, prof in server_profiles.items():
        entry = dict(prof) if isinstance(prof, dict) else {}
        entry.setdefault("name", name)
        # Remove obfuscated secrets from the listing
        entry.pop("password_obf", None)
        entry.pop("rcon_password_obf", None)
        entry.pop("ptero_api_key_obf", None)
        # Tell the frontend which Rust fields are saved
        if "rcon_password_obf" in prof:
            entry["rcon_password_saved"] = True
        if "ptero_api_key_obf" in prof:
            entry["ptero_api_key_saved"] = True
        result.append(entry)
    return jsonify(result)


@app.route("/api/profiles", methods=["POST"])
@_require_auth
def save_profile():
    """Create or update a server profile."""
    global server_profiles
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Profile name is required"}), 400

    # Build profile data, handling password obfuscation
    profile_data = {}
    for k, v in data.items():
        if k in ("name", "password"):
            continue
        profile_data[k] = v

    # Handle password saving (obfuscated, not plaintext)
    if data.get("save_password") and data.get("password"):
        import base64
        profile_data["password_saved"] = True
        profile_data["password_obf"] = base64.b64encode(
            data["password"].encode("utf-8")
        ).decode("ascii")
    elif data.get("auth_type") == "password":
        profile_data["password_required"] = True

    # Handle Rust admin credentials (RCON + Pterodactyl)
    import base64 as _b64

    # Get existing profile for preserving saved secrets
    existing_profile = server_profiles.get(name, {})

    if data.get("rcon_password"):
        profile_data["rcon_password_obf"] = _b64.b64encode(
            data["rcon_password"].encode("utf-8")
        ).decode("ascii")
        profile_data.pop("rcon_password", None)
    elif data.get("preserve_rcon_password") and existing_profile.get("rcon_password_obf"):
        # Keep the existing saved RCON password
        profile_data["rcon_password_obf"] = existing_profile["rcon_password_obf"]
    profile_data.pop("preserve_rcon_password", None)

    if data.get("ptero_api_key"):
        profile_data["ptero_api_key_obf"] = _b64.b64encode(
            data["ptero_api_key"].encode("utf-8")
        ).decode("ascii")
        profile_data.pop("ptero_api_key", None)
    elif data.get("preserve_ptero_api_key") and existing_profile.get("ptero_api_key_obf"):
        # Keep the existing saved Pterodactyl API key
        profile_data["ptero_api_key_obf"] = existing_profile["ptero_api_key_obf"]
    profile_data.pop("preserve_ptero_api_key", None)

    server_profiles[name] = profile_data
    _save_config(server_profiles)
    return jsonify({"status": "ok", "name": name})


@app.route("/api/profiles/setup-ssh-key", methods=["POST"])
@_require_auth
def setup_ssh_key():
    """Generate an SSH key pair and install the public key on the remote server.
    Requires an active SSH connection (password-based) to copy the key."""
    data = request.get_json(force=True)
    profile_name = data.get("profile_name", "").strip()
    if not profile_name:
        return jsonify({"error": "Profile name is required"}), 400

    try:
        import tempfile
        import paramiko

        # Generate a new RSA key pair
        key = paramiko.RSAKey.generate(4096)

        # Save private key
        key_dir = PROJECT_ROOT / "ssh_keys"
        key_dir.mkdir(exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile_name)
        key_path = key_dir / f"id_rsa_{safe_name}"
        key.write_private_key_file(str(key_path))
        key_path.chmod(0o600)

        # Get public key string
        pub_key = f"ssh-rsa {key.get_base64()} sysadmin-agent-{safe_name}"

        # Try to install the key on the remote server using existing connection
        # Find the profile to get connection details
        prof = server_profiles.get(profile_name)
        if not prof or not isinstance(prof, dict):
            return jsonify({"error": "Profile not found"}), 404

        host = prof.get("host")
        port = int(prof.get("port", 22))
        username = prof.get("username", "")

        # Get password from the request or from saved profile
        password = data.get("password")
        if not password:
            import base64 as b64
            obf = prof.get("password_obf")
            if obf:
                password = b64.b64decode(obf.encode("ascii")).decode("utf-8")

        if not password:
            return jsonify({"error": "Password required to install SSH key"}), 400

        # Connect and install the key
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=host, port=port, username=username,
                       password=password, timeout=15)

        install_cmd = (
            f'mkdir -p ~/.ssh && chmod 700 ~/.ssh && '
            f'echo "{pub_key}" >> ~/.ssh/authorized_keys && '
            f'chmod 600 ~/.ssh/authorized_keys'
        )
        stdin, stdout, stderr = client.exec_command(install_cmd)
        exit_code = stdout.channel.recv_exit_status()
        client.close()

        if exit_code != 0:
            err = stderr.read().decode("utf-8", errors="replace")
            return jsonify({"error": f"Failed to install key: {err}"}), 500

        # Update profile to use key auth
        prof["auth_type"] = "key"
        prof["key_path"] = str(key_path)
        prof.pop("password_obf", None)
        prof.pop("password_saved", None)
        prof.pop("password_required", None)
        server_profiles[profile_name] = prof
        _save_config(server_profiles)

        return jsonify({
            "status": "ok",
            "key_path": str(key_path),
            "message": f"SSH key generated and installed. Profile updated to key auth.",
        })

    except Exception as exc:
        logger.exception("setup_ssh_key failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/profiles/<name>", methods=["DELETE"])
@_require_auth
def delete_profile(name):
    """Delete a server profile by name."""
    global server_profiles
    if name not in server_profiles:
        return jsonify({"error": "Profile not found"}), 404

    del server_profiles[name]
    _save_config(server_profiles)
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# HTTP Routes — Auth
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    web_password = os.environ.get("WEB_PASSWORD")
    if not web_password:
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == web_password:
            session["authenticated"] = True
            return redirect(url_for("dashboard"))
        error = "Invalid password."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# HTTP Routes — Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@_require_auth
def dashboard():
    return render_template("dashboard.html")


# ---------------------------------------------------------------------------
# SocketIO events
# ---------------------------------------------------------------------------

@socketio.on("connect")
def handle_connect():
    logger.info("Client connected: %s", request.sid)


@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    logger.info("Client disconnected: %s", sid)
    # Clean up SSH connection if any
    ssh = _ssh_connections.pop(sid, None)
    if ssh:
        try:
            ssh.disconnect()
        except Exception:
            pass
    _session_data.pop(sid, None)
    _conversations.pop(sid, None)
    _brains.pop(sid, None)
    # Clean up Rust connections
    rcon = _rcon_connections.pop(sid, None)
    if rcon:
        try:
            rcon.disconnect()
        except Exception:
            pass
    _ptero_connections.pop(sid, None)


@socketio.on("connect_server")
def handle_connect_server(data):
    """Open an SSH connection, detect OS and applications."""
    sid = request.sid
    try:
        mods = _import_agent_modules()
        SSHManager = mods["SSHManager"]
        OSDetector = mods["OSDetector"]
        AppDiscovery = mods["AppDiscovery"]

        host = data["host"]
        port = int(data.get("port", 22))
        username = data["username"]
        password = data.get("password")
        private_key_path = data.get("private_key_path") or data.get("key_path")
        passphrase = data.get("passphrase")

        # If no password provided, check if there's a saved one in profiles
        if not password and not private_key_path:
            import base64
            for _pname, prof in server_profiles.items():
                if (isinstance(prof, dict)
                        and prof.get("host") == host
                        and prof.get("username", "") == username
                        and prof.get("password_obf")):
                    try:
                        password = base64.b64decode(
                            prof["password_obf"].encode("ascii")
                        ).decode("utf-8")
                    except Exception:
                        pass
                    break

        emit("status", {"message": f"Connecting to {host}:{port}..."})

        ssh = SSHManager(
            host=host,
            port=port,
            username=username,
            password=password,
            private_key_path=private_key_path,
            passphrase=passphrase,
        )
        ssh.connect()

        # Store connection
        # Disconnect any prior connection for this socket
        old = _ssh_connections.pop(sid, None)
        if old:
            try:
                old.disconnect()
            except Exception:
                pass

        _ssh_connections[sid] = ssh
        sdata = _get_session_data(sid)

        emit("status", {"message": "Detecting OS..."})
        os_info = OSDetector(ssh).detect()
        sdata["os_info"] = os_info

        emit("status", {"message": "Discovering applications..."})
        apps = AppDiscovery(ssh).discover()
        sdata["apps"] = apps

        sdata["host"] = host
        sdata["port"] = port
        sdata["username"] = username

        emit("server_connected", {
            "host": host,
            "port": port,
            "username": username,
            "os_info": os_info,
            "apps": apps,
        })
    except Exception as exc:
        logger.exception("connect_server failed")
        emit("error", {"message": f"Connection failed: {exc}"})


@socketio.on("disconnect_server")
def handle_disconnect_server(data=None):
    sid = request.sid
    ssh = _ssh_connections.pop(sid, None)
    if ssh:
        try:
            ssh.disconnect()
        except Exception:
            pass
    _session_data.pop(sid, None)
    _conversations.pop(sid, None)
    _brains.pop(sid, None)
    rcon = _rcon_connections.pop(sid, None)
    if rcon:
        try:
            rcon.disconnect()
        except Exception:
            pass
    _ptero_connections.pop(sid, None)
    emit("server_disconnected", {"message": "Disconnected from server."})


@socketio.on("run_scan")
def handle_run_scan(data=None):
    """Full scan: OS detection + app discovery + diagnostics."""
    sid = request.sid
    ssh = _get_ssh(sid)
    if not ssh:
        emit("error", {"message": "Not connected to a server."})
        return

    try:
        mods = _import_agent_modules()
        sdata = _get_session_data(sid)

        emit("status", {"message": "Detecting OS..."})
        os_info = mods["OSDetector"](ssh).detect()
        sdata["os_info"] = os_info
        emit("scan_os", {"os_info": os_info})

        emit("status", {"message": "Discovering applications..."})
        apps = mods["AppDiscovery"](ssh).discover()
        sdata["apps"] = apps
        emit("scan_apps", {"apps": apps})

        emit("status", {"message": "Running diagnostics..."})
        approval = WebApprovalManager(sid)
        rollback = mods["RollbackManager"](ssh)
        engine = mods["DiagnosticEngine"](ssh, approval, rollback)
        results = engine.run_all()
        sdata["diagnostics"] = results
        emit("scan_diagnostics", {"diagnostics": results})

        emit("scan_complete", {
            "os_info": os_info,
            "apps": apps,
            "diagnostics": results,
        })
    except Exception as exc:
        logger.exception("run_scan failed")
        emit("error", {"message": f"Scan failed: {exc}"})


@socketio.on("run_diagnostics")
def handle_run_diagnostics(data=None):
    """Run diagnostics only."""
    sid = request.sid
    ssh = _get_ssh(sid)
    if not ssh:
        emit("error", {"message": "Not connected to a server."})
        return

    try:
        mods = _import_agent_modules()
        sdata = _get_session_data(sid)

        emit("status", {"message": "Running diagnostics..."})
        approval = WebApprovalManager(sid)
        rollback = mods["RollbackManager"](ssh)
        engine = mods["DiagnosticEngine"](ssh, approval, rollback)
        results = engine.run_all()
        sdata["diagnostics"] = results
        emit("diagnostics_result", {"diagnostics": results})
    except Exception as exc:
        logger.exception("run_diagnostics failed")
        emit("error", {"message": f"Diagnostics failed: {exc}"})


@socketio.on("ask_agent")
def handle_ask_agent(data):
    """Send a plain-English request to the AgentBrain, then optionally
    execute the returned plan step by step."""
    sid = request.sid
    ssh = _get_ssh(sid)
    if not ssh:
        emit("error", {"message": "Not connected to a server."})
        return

    user_request = (data.get("message") or data.get("request") or "").strip()
    if not user_request:
        emit("error", {"message": "Empty request."})
        return

    try:
        mods = _import_agent_modules()
        sdata = _get_session_data(sid)
        conversation = _get_conversation(sid)

        # Track conversation
        conversation.append({"role": "user", "content": user_request})

        emit("status", {"message": "Thinking..."})

        brain = _get_brain(sid)
        ctx = _build_server_context(sid)

        # If there is prior conversation context, include it
        if len(conversation) > 1:
            ctx["conversation_history"] = json.dumps(conversation[-10:])

        plan = brain.interpret(user_request, server_context=ctx)

        # Store assistant reply in conversation
        conversation.append({"role": "assistant", "content": json.dumps(plan)})

        emit("agent_plan", {
            "plan": plan,
            "token_usage": brain.get_token_usage(),
            "token_breakdown": _token_tracker.get_usage(),
        })

        # If the brain asked questions instead of providing a plan, stop here
        if plan.get("questions"):
            return

        # Always auto-execute plans: non-destructive steps run automatically,
        # destructive steps require approval via the web UI.
        steps = plan.get("plan", [])
        if not steps:
            return

        approval = WebApprovalManager(sid)
        rollback = mods["RollbackManager"](ssh)

        for step in steps:
            command = step.get("command")
            if not command:
                emit("step_result", {
                    "step": step["step"],
                    "description": step["description"],
                    "skipped": True,
                    "reason": "No command (manual step)",
                })
                continue

            emit("step_executing", {
                "step": step["step"],
                "description": step["description"],
                "command": command,
            })

            if step.get("needs_approval") or step.get("destructive"):
                approved = approval.request_approval({
                    "command": command,
                    "description": step["description"],
                    "destructive": step.get("destructive", False),
                })
                if not approved:
                    emit("step_result", {
                        "step": step["step"],
                        "skipped": True,
                        "reason": "Approval denied",
                    })
                    continue

            if step.get("destructive"):
                result = ssh.execute_sudo(command)
            else:
                result = ssh.execute(command)

            # AI analysis of the output
            analysis = brain.analyze_results(
                command,
                result["stdout"],
                result["stderr"],
                result["exit_code"],
                context=ctx,
            )

            emit("step_result", {
                "step": step["step"],
                "command": command,
                "exit_code": result["exit_code"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "analysis": analysis,
            })

        emit("agent_done", {
            "token_usage": brain.get_token_usage(),
            "token_breakdown": _token_tracker.get_usage(),
        })

    except Exception as exc:
        logger.exception("ask_agent failed")
        emit("error", {"message": f"Agent error: {exc}"})


@socketio.on("approve_action")
def handle_approve_action(data):
    """User approves or denies a pending action."""
    approval_id = data.get("approval_id") or data.get("id")
    approved = data.get("approved", False)

    pending = _pending_approvals.get(approval_id)
    if not pending:
        emit("error", {"message": f"No pending approval with id {approval_id}"})
        return

    pending["result"]["approved"] = approved
    pending["event"].set()

    emit("approval_resolved", {
        "approval_id": approval_id,
        "approved": approved,
    })


@socketio.on("run_fix")
def handle_run_fix(data=None):
    """Run diagnostics and auto-apply fixes (with web-based approval)."""
    sid = request.sid
    ssh = _get_ssh(sid)
    if not ssh:
        emit("error", {"message": "Not connected to a server."})
        return

    try:
        mods = _import_agent_modules()
        sdata = _get_session_data(sid)

        emit("status", {"message": "Running diagnostics..."})
        approval = WebApprovalManager(sid)
        rollback_mgr = mods["RollbackManager"](ssh)
        engine = mods["DiagnosticEngine"](ssh, approval, rollback_mgr)
        results = engine.run_all()
        sdata["diagnostics"] = results

        emit("diagnostics_result", {"diagnostics": results})

        fixes_applied = []
        for check in results:
            if not check.get("fix"):
                continue
            for fix_action in check["fix"]:
                emit("fix_attempting", {
                    "check": check["name"],
                    "action": fix_action,
                })
                result = engine.apply_fix(fix_action)
                fixes_applied.append({
                    "check": check["name"],
                    "action": fix_action,
                    "result": result,
                })
                emit("fix_result", {
                    "check": check["name"],
                    "action": fix_action,
                    "result": result,
                })

        emit("fix_complete", {"fixes": fixes_applied})
    except Exception as exc:
        logger.exception("run_fix failed")
        emit("error", {"message": f"Fix run failed: {exc}"})


@socketio.on("rollback")
def handle_rollback(data=None):
    """List snapshots or execute a rollback."""
    sid = request.sid
    ssh = _get_ssh(sid)
    if not ssh:
        emit("error", {"message": "Not connected to a server."})
        return

    data = data or {}

    try:
        mods = _import_agent_modules()
        rollback_mgr = mods["RollbackManager"](ssh)

        action = data.get("action", "list")

        if action == "list":
            snapshots = rollback_mgr.list_snapshots()
            emit("rollback_list", {"snapshots": snapshots})

        elif action == "execute":
            snapshot_id = data.get("snapshot_id")
            if not snapshot_id:
                emit("error", {"message": "No snapshot_id provided."})
                return
            emit("status", {"message": f"Rolling back snapshot {snapshot_id}..."})
            results = rollback_mgr.rollback(snapshot_id)
            emit("rollback_result", {
                "snapshot_id": snapshot_id,
                "results": results,
            })

        elif action == "remove":
            snapshot_id = data.get("snapshot_id")
            if not snapshot_id:
                emit("error", {"message": "No snapshot_id provided."})
                return
            removed = rollback_mgr.remove_snapshot(snapshot_id)
            emit("rollback_removed", {
                "snapshot_id": snapshot_id,
                "removed": removed,
            })

        else:
            emit("error", {"message": f"Unknown rollback action: {action}"})

    except Exception as exc:
        logger.exception("rollback failed")
        emit("error", {"message": f"Rollback error: {exc}"})


@socketio.on("rollback_execute")
def handle_rollback_execute(data):
    """Shorthand for rollback with action=execute, used by the frontend."""
    if not data:
        data = {}
    data["action"] = "execute"
    return handle_rollback(data)


@socketio.on("exec_command")
def handle_exec_command(data):
    """Execute a raw command on the connected server."""
    sid = request.sid
    ssh = _get_ssh(sid)
    if not ssh:
        emit("error", {"message": "Not connected to a server."})
        return

    command = data.get("command", "").strip()
    if not command:
        emit("error", {"message": "Empty command."})
        return

    use_sudo = data.get("sudo", False)

    try:
        if use_sudo:
            result = ssh.execute_sudo(command)
        else:
            result = ssh.execute(command)

        emit("command_result", {
            "command": command,
            "exit_code": result["exit_code"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        })
    except Exception as exc:
        logger.exception("exec_command failed")
        emit("error", {"message": f"Command execution failed: {exc}"})


# ---------------------------------------------------------------------------
# HTTP Routes — Upgrade
# ---------------------------------------------------------------------------

@app.route("/api/version", methods=["GET"])
@_require_auth
def get_version():
    """Return current version and check if updates are available."""
    from sysadmin_agent import __version__

    result = {
        "current_version": __version__,
        "update_available": False,
        "remote_version": None,
    }

    # Check for updates via git
    try:
        # Fetch latest from remote without merging
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=15,
        )

        # Compare local HEAD with remote
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()

        # Get the default branch name
        default_branch = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().replace("refs/remotes/origin/", "")
        if not default_branch:
            default_branch = "main"

        remote = subprocess.run(
            ["git", "rev-parse", f"origin/{default_branch}"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()

        if local != remote and remote:
            result["update_available"] = True

            # Try to get version from remote
            remote_ver = subprocess.run(
                ["git", "show", f"origin/{default_branch}:sysadmin_agent/__init__.py"],
                cwd=str(PROJECT_ROOT),
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in remote_ver.splitlines():
                if "__version__" in line:
                    result["remote_version"] = line.split("=")[1].strip().strip("\"'")
                    break

        # Count commits behind
        behind = subprocess.run(
            ["git", "rev-list", "--count", f"HEAD..origin/{default_branch}"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        result["commits_behind"] = int(behind) if behind.isdigit() else 0

    except Exception as exc:
        logger.warning("Version check failed: %s", exc)

    return jsonify(result)


@app.route("/api/upgrade", methods=["POST"])
@_require_auth
def do_upgrade():
    """Pull latest changes from git and signal that a restart is needed."""
    try:
        # Get current branch
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=5,
        )
        current_branch = branch_result.stdout.strip() or "main"

        # Stash any local changes
        subprocess.run(
            ["git", "stash"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, timeout=10,
        )

        # Pull latest
        pull_result = subprocess.run(
            ["git", "pull", "origin", current_branch],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=60,
        )

        if pull_result.returncode != 0:
            return jsonify({
                "status": "error",
                "message": f"Git pull failed: {pull_result.stderr}",
            }), 500

        # Install any new/updated dependencies
        pip_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--user", "-e", "."],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=120,
        )

        # Get new version
        new_version = None
        try:
            init_file = PROJECT_ROOT / "sysadmin_agent" / "__init__.py"
            for line in init_file.read_text().splitlines():
                if "__version__" in line:
                    new_version = line.split("=")[1].strip().strip("\"'")
                    break
        except Exception:
            pass

        return jsonify({
            "status": "ok",
            "message": "Update complete. Please restart the application.",
            "git_output": pull_result.stdout,
            "new_version": new_version,
            "restart_required": True,
        })

    except Exception as exc:
        logger.exception("Upgrade failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/restart", methods=["POST"])
@_require_auth
def do_restart():
    """Restart the application process.

    Properly shuts down the SocketIO server and closes the listening socket
    before exec-ing a new process to avoid 'Address already in use' errors.
    """
    def _restart():
        import time as _time
        import signal
        import socket as _socket
        _time.sleep(0.5)

        # Close all SSH connections
        for sid, ssh in list(_ssh_connections.items()):
            try:
                ssh.disconnect()
            except Exception:
                pass
        _ssh_connections.clear()

        # Close all RCON connections
        for sid, rcon in list(_rcon_connections.items()):
            try:
                rcon.close()
            except Exception:
                pass
        _rcon_connections.clear()

        # Force-close the listening socket(s) to free the port.
        # werkzeug stores the socket on the server object; walk up from
        # the WSGI server to find it.
        _freed = False
        try:
            # Flask-SocketIO with werkzeug uses socketio.server.eio
            # but the actual TCP socket is on the werkzeug server.
            # Find it via the werkzeug shutdown mechanism.
            func = request.environ.get("werkzeug.server.shutdown")
            if func:
                func()
                _freed = True
        except Exception:
            pass

        # Stop SocketIO server
        try:
            socketio.stop()
        except Exception:
            pass

        # Brute-force: find and close any socket bound to our port.
        # This handles the case where socketio.stop() doesn't release
        # the port in time.
        port = int(os.environ.get("WEB_PORT", "5000"))
        try:
            # Try to close the fd by scanning /proc/self for the listening socket
            import glob as _glob
            for fd_path in _glob.glob("/proc/self/fd/*"):
                try:
                    fd_num = int(os.path.basename(fd_path))
                    sock = _socket.fromfd(fd_num, _socket.AF_INET, _socket.SOCK_STREAM)
                    try:
                        addr = sock.getsockname()
                        if addr[1] == port:
                            sock.close()
                            os.close(fd_num)
                            _freed = True
                    except (OSError, _socket.error):
                        sock.detach()  # don't close if not our socket
                except (ValueError, OSError):
                    pass
        except Exception:
            pass

        # Wait for port to actually be free
        for _attempt in range(20):
            try:
                probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                probe.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                probe.bind(("0.0.0.0", port))
                probe.close()
                break  # Port is free
            except OSError:
                _time.sleep(0.5)
        else:
            logger.warning("Port %d still in use after 10s, exec-ing anyway", port)

        # Re-exec the process
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({"status": "ok", "message": "Restarting..."})


# ---------------------------------------------------------------------------
# SocketIO Events — Rust Server Administration
# ---------------------------------------------------------------------------

@socketio.on("rust_connect_rcon")
def handle_rust_connect_rcon(data):
    """Connect to a Rust server via RCON."""
    sid = request.sid
    host = (data.get("host") or "").strip()
    port = data.get("port", 28016)
    password = (data.get("password") or "").strip()

    # Look up saved RCON password from profile if requested
    if not password and data.get("use_saved_password"):
        profile_name = data.get("profile_name", "")
        profile = server_profiles.get(profile_name, {})
        obf = profile.get("rcon_password_obf", "")
        if obf:
            import base64
            password = base64.b64decode(obf).decode("utf-8")

    if not host or not password:
        emit("error", {"message": "RCON host and password are required."})
        return

    try:
        port = int(port)
    except (ValueError, TypeError):
        emit("error", {"message": "Invalid RCON port."})
        return

    try:
        from sysadmin_agent.rust.rcon_client import RCONClient
        rcon = RCONClient(host, port, password)
        rcon.connect()
        _rcon_connections[sid] = rcon

        # Get initial server info
        info = rcon.server_info()
        emit("rust_rcon_connected", {"server_info": info})
    except Exception as exc:
        logger.exception("RCON connect failed")
        emit("error", {"message": f"RCON connection failed: {exc}"})


@socketio.on("rust_disconnect_rcon")
def handle_rust_disconnect_rcon(data=None):
    """Disconnect RCON."""
    sid = request.sid
    rcon = _rcon_connections.pop(sid, None)
    if rcon:
        try:
            rcon.disconnect()
        except Exception:
            pass
    emit("rust_rcon_disconnected", {})


@socketio.on("rust_connect_pterodactyl")
def handle_rust_connect_pterodactyl(data):
    """Connect to Pterodactyl Panel API."""
    sid = request.sid
    base_url = (data.get("base_url") or "").strip().rstrip("/")
    api_key = (data.get("api_key") or "").strip()
    server_id = (data.get("server_id") or "").strip()

    # Look up saved API key from profile if requested
    if not api_key and data.get("use_saved_key"):
        profile_name = data.get("profile_name", "")
        profile = server_profiles.get(profile_name, {})
        obf = profile.get("ptero_api_key_obf", "")
        if obf:
            import base64
            api_key = base64.b64decode(obf).decode("utf-8")

    if not base_url or not api_key:
        emit("error", {"message": "Panel URL and API key are required."})
        return

    try:
        from sysadmin_agent.rust.pterodactyl_api import PterodactylAPI
        ptero = PterodactylAPI(base_url, api_key)

        # Verify connection by listing servers
        servers = ptero.list_servers()

        # If user gave a UUID, try to resolve it to the short identifier
        # that the Client API requires
        resolved_id = server_id
        if server_id and "-" in server_id:
            for s in servers:
                if s.get("uuid", "").startswith(server_id) or \
                   s.get("uuid") == server_id:
                    resolved_id = s["identifier"]
                    logger.info("Resolved UUID %s to identifier %s",
                                server_id, resolved_id)
                    break

        # If no server_id given but only one server exists, auto-select it
        if not resolved_id and len(servers) == 1:
            resolved_id = servers[0]["identifier"]

        # Store server limits (CPU, memory) for diagnostics
        server_limits = {}
        for s in servers:
            if s.get("identifier") == resolved_id:
                server_limits = s.get("limits", {})
                break

        _ptero_connections[sid] = {
            "api": ptero,
            "server_id": resolved_id,
            "limits": server_limits,
        }

        result = {
            "servers": servers,
            "selected_server": resolved_id,
            "client_api": ptero._client_api_available,
        }
        if not ptero._client_api_available:
            result["warning"] = (
                "Connected with Application API key (ptla_). "
                "Server management (files, console, power) requires a "
                "Client API key (ptlc_). Create one under Account > API Credentials."
            )
        emit("rust_ptero_connected", result)
    except Exception as exc:
        logger.exception("Pterodactyl connect failed")
        emit("error", {"message": f"Pterodactyl connection failed: {exc}"})


@socketio.on("rust_disconnect_pterodactyl")
def handle_rust_disconnect_pterodactyl(data=None):
    """Disconnect Pterodactyl."""
    sid = request.sid
    _ptero_connections.pop(sid, None)
    emit("rust_ptero_disconnected", {})


@socketio.on("rust_rcon_command")
def handle_rust_rcon_command(data):
    """Send an arbitrary RCON command."""
    sid = request.sid
    rcon = _rcon_connections.get(sid)
    if not rcon:
        emit("error", {"message": "Not connected to RCON."})
        return

    command = (data.get("command") or "").strip()
    if not command:
        emit("error", {"message": "Empty command."})
        return

    try:
        response = rcon.command(command)
        emit("rust_rcon_response", {"command": command, "response": response})
    except Exception as exc:
        logger.warning("RCON command failed: %s", exc)
        # Try to reconnect
        try:
            rcon.disconnect()
        except Exception:
            pass
        _rcon_connections.pop(sid, None)
        emit("error", {"message": f"RCON command failed (disconnected): {exc}"})


@socketio.on("rust_quick_action")
def handle_rust_quick_action(data):
    """Execute a Rust server quick action via RCON."""
    sid = request.sid
    rcon = _rcon_connections.get(sid)
    if not rcon:
        emit("error", {"message": "Not connected to RCON."})
        return

    action = (data.get("action") or "").strip()
    actions_map = {
        "server_info": lambda: rcon.server_info(),
        "status": lambda: rcon.status(),
        "fps": lambda: rcon.get_fps(),
        "entity_count": lambda: rcon.entity_count(),
        "player_list": lambda: rcon.player_list(),
        "performance": lambda: rcon.performance_report(),
        "force_save": lambda: rcon.force_save(),
        "gc_collect": lambda: rcon.gc_collect(),
        "oxide_plugins": lambda: rcon.oxide_plugins(),
        "oxide_version": lambda: rcon.oxide_version(),
        "pool_status": lambda: rcon.pool_status(),
    }

    handler = actions_map.get(action)
    if not handler:
        emit("error", {"message": f"Unknown quick action: {action}"})
        return

    try:
        result = handler()
        emit("rust_action_result", {"action": action, "result": result})
    except Exception as exc:
        logger.warning("Rust quick action '%s' failed: %s", action, exc)
        emit("error", {"message": f"Action '{action}' failed: {exc}"})


@socketio.on("rust_run_diagnostics")
def handle_rust_run_diagnostics(data=None):
    """Run comprehensive Rust server diagnostics."""
    sid = request.sid
    rcon = _rcon_connections.get(sid)
    ptero_data = _ptero_connections.get(sid)
    ssh = _get_ssh(sid)

    if not rcon:
        emit("error", {"message": "RCON connection required for Rust diagnostics."})
        return

    ptero = ptero_data["api"] if ptero_data else None
    server_id = ptero_data.get("server_id") if ptero_data else None

    try:
        from sysadmin_agent.rust.rust_diagnostics import RustServerDiagnostics

        emit("status", {"message": "Running Rust server diagnostics..."})

        def progress_cb(msg):
            emit("status", {"message": msg})

        server_limits = ptero_data.get("limits", {}) if ptero_data else {}
        diag = RustServerDiagnostics(rcon, ptero=ptero, server_id=server_id,
                                     ssh=ssh, on_progress=progress_cb,
                                     server_limits=server_limits)
        results = diag.run_all()

        emit("rust_diagnostics_result", {"diagnostics": results})
    except Exception as exc:
        logger.exception("Rust diagnostics failed")
        emit("error", {"message": f"Rust diagnostics failed: {exc}"})


@socketio.on("rust_diagnose_lag")
def handle_rust_diagnose_lag(data=None):
    """Run focused lag/rubber-banding diagnosis."""
    sid = request.sid
    rcon = _rcon_connections.get(sid)
    ptero_data = _ptero_connections.get(sid)
    ssh = _get_ssh(sid)

    if not rcon:
        emit("error", {"message": "RCON connection required for lag diagnosis."})
        return

    ptero = ptero_data["api"] if ptero_data else None
    server_id = ptero_data.get("server_id") if ptero_data else None

    try:
        from sysadmin_agent.rust.rust_diagnostics import RustServerDiagnostics

        emit("status", {"message": "Diagnosing lag and rubber-banding..."})

        def progress_cb(msg):
            emit("status", {"message": msg})

        server_limits = ptero_data.get("limits", {}) if ptero_data else {}
        diag = RustServerDiagnostics(rcon, ptero=ptero, server_id=server_id,
                                     ssh=ssh, on_progress=progress_cb,
                                     server_limits=server_limits)
        results = diag.run_lag_diagnosis()

        emit("rust_lag_result", {"diagnosis": results})
    except Exception as exc:
        logger.exception("Lag diagnosis failed")
        emit("error", {"message": f"Lag diagnosis failed: {exc}"})


@socketio.on("rust_plugin_action")
def handle_rust_plugin_action(data):
    """Manage Oxide plugins: reload, get config, update config."""
    sid = request.sid
    rcon = _rcon_connections.get(sid)
    ptero_data = _ptero_connections.get(sid)

    if not rcon:
        emit("error", {"message": "RCON connection required."})
        return

    action = (data.get("action") or "").strip()
    plugin_name = (data.get("plugin") or "").strip()

    if not action or not plugin_name:
        emit("error", {"message": "Action and plugin name are required."})
        return

    try:
        from sysadmin_agent.rust.rust_diagnostics import RustServerDiagnostics

        ptero = ptero_data["api"] if ptero_data else None
        server_id = ptero_data.get("server_id") if ptero_data else None
        server_limits = ptero_data.get("limits", {}) if ptero_data else {}
        diag = RustServerDiagnostics(rcon, ptero=ptero, server_id=server_id,
                                     server_limits=server_limits)

        if action == "reload":
            result = diag.reload_plugin(plugin_name)
            emit("rust_plugin_result", {
                "action": "reload",
                "plugin": plugin_name,
                "result": result,
            })
        elif action == "get_config":
            config = diag.get_plugin_config(plugin_name)
            emit("rust_plugin_result", {
                "action": "get_config",
                "plugin": plugin_name,
                "config": config,
            })
        elif action == "update_config":
            config_data = data.get("config", {})
            result = diag.update_plugin_config(plugin_name, config_data)
            emit("rust_plugin_result", {
                "action": "update_config",
                "plugin": plugin_name,
                "result": result,
            })
        else:
            emit("error", {"message": f"Unknown plugin action: {action}"})
    except Exception as exc:
        logger.warning("Plugin action failed: %s", exc)
        emit("error", {"message": f"Plugin action failed: {exc}"})


@socketio.on("rust_ptero_action")
def handle_rust_ptero_action(data):
    """Pterodactyl server management actions."""
    sid = request.sid
    ptero_data = _ptero_connections.get(sid)
    if not ptero_data:
        emit("error", {"message": "Not connected to Pterodactyl."})
        return

    ptero = ptero_data["api"]
    server_id = data.get("server_id") or ptero_data.get("server_id")
    if not server_id:
        emit("error", {"message": "No server selected."})
        return

    action = (data.get("action") or "").strip()

    try:
        if action == "resources":
            result = ptero.get_resources(server_id)
        elif action == "start":
            result = ptero.set_power_state(server_id, "start")
        elif action == "stop":
            result = ptero.set_power_state(server_id, "stop")
        elif action == "restart":
            result = ptero.set_power_state(server_id, "restart")
        elif action == "kill":
            result = ptero.set_power_state(server_id, "kill")
        elif action == "list_files":
            directory = data.get("directory", "/")
            result = ptero.list_files(server_id, directory)
        elif action == "get_file":
            file_path = data.get("file_path", "")
            result = ptero.get_file_contents(server_id, file_path)
        elif action == "oxide_plugins":
            result = ptero.rust_list_oxide_plugins(server_id)
        elif action == "oxide_config":
            plugin = data.get("plugin", "")
            result = ptero.rust_get_oxide_config(server_id, plugin)
        elif action == "oxide_logs":
            result = ptero.rust_get_oxide_logs(server_id)
        elif action == "server_cfg":
            result = ptero.rust_get_server_cfg(server_id)
        elif action == "backups":
            result = ptero.list_backups(server_id)
        else:
            emit("error", {"message": f"Unknown Pterodactyl action: {action}"})
            return

        emit("rust_ptero_result", {"action": action, "result": result})
    except Exception as exc:
        logger.warning("Pterodactyl action '%s' failed: %s", action, exc)
        emit("error", {"message": f"Pterodactyl action failed: {exc}"})


# ---------------------------------------------------------------------------
# HTTP Routes — Token usage
# ---------------------------------------------------------------------------

@app.route("/api/tokens", methods=["GET"])
@_require_auth
def get_token_usage():
    """Return full token usage breakdown."""
    return jsonify(_token_tracker.get_usage())


@app.route("/api/tokens/billing-cycle", methods=["POST"])
@_require_auth
def set_billing_cycle():
    """Set the billing cycle day (1-28)."""
    data = request.get_json(force=True)
    day = data.get("day", 1)
    try:
        day = int(day)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid day"}), 400
    _token_tracker.set_billing_cycle_day(day)
    return jsonify({"status": "ok", "billing_cycle_day": day})


# ---------------------------------------------------------------------------
# Security: rate limiting, scan protection, and access control
# ---------------------------------------------------------------------------

# Track failed/suspicious requests per IP for auto-blocking
_ip_fail_counts: dict[str, list] = {}  # ip -> list of timestamps
_ip_blocked: dict[str, float] = {}     # ip -> block_until timestamp
_IP_FAIL_WINDOW = 60       # seconds to track failures
_IP_FAIL_THRESHOLD = 15    # failures within window to trigger block
_IP_BLOCK_DURATION = 600   # block for 10 minutes
_ALLOWED_IPS: set[str] | None = None   # populated from WEB_ALLOWED_IPS env


def _get_allowed_ips() -> set[str] | None:
    """Parse WEB_ALLOWED_IPS env var (comma-separated) into a set."""
    global _ALLOWED_IPS
    if _ALLOWED_IPS is not None:
        return _ALLOWED_IPS
    raw = os.environ.get("WEB_ALLOWED_IPS", "").strip()
    if raw:
        _ALLOWED_IPS = {ip.strip() for ip in raw.split(",") if ip.strip()}
    else:
        _ALLOWED_IPS = set()
    return _ALLOWED_IPS


def _record_suspicious(ip: str):
    """Record a suspicious request from an IP.  Auto-blocks after threshold."""
    import time as _time
    now = _time.time()
    if ip not in _ip_fail_counts:
        _ip_fail_counts[ip] = []
    _ip_fail_counts[ip].append(now)
    # Prune old entries
    _ip_fail_counts[ip] = [t for t in _ip_fail_counts[ip] if now - t < _IP_FAIL_WINDOW]
    if len(_ip_fail_counts[ip]) >= _IP_FAIL_THRESHOLD:
        _ip_blocked[ip] = now + _IP_BLOCK_DURATION
        _ip_fail_counts.pop(ip, None)
        logger.warning("Auto-blocked IP %s for %d seconds (scan/abuse detected)", ip, _IP_BLOCK_DURATION)


@app.before_request
def _security_gate():
    """Multi-layer security: localhost-only, IP allowlist, auto-block scanners."""
    import time as _time
    remote = request.remote_addr
    is_local = remote in ("127.0.0.1", "::1", "localhost")

    # Always allow localhost
    if is_local:
        return None

    # Check if remote access is enabled
    if os.environ.get("WEB_ALLOW_REMOTE", "").lower() not in ("1", "true", "yes"):
        return "", 403

    # Check IP allowlist (if configured, only those IPs can access)
    allowed = _get_allowed_ips()
    if allowed and remote not in allowed:
        return "", 403

    # Check auto-block list
    block_until = _ip_blocked.get(remote, 0)
    if block_until > _time.time():
        return "", 403
    elif block_until:
        # Block expired, clean up
        _ip_blocked.pop(remote, None)

    return None


@app.after_request
def _track_bad_requests(response):
    """Track 400 errors to detect scanners and auto-block them."""
    if response.status_code == 400:
        remote = request.remote_addr
        if remote not in ("127.0.0.1", "::1", "localhost"):
            _record_suspicious(remote)
    return response


# ---------------------------------------------------------------------------
# HTTP Routes — Security management
# ---------------------------------------------------------------------------

@app.route("/api/security/blocked-ips", methods=["GET"])
@_require_auth
def get_blocked_ips():
    """List currently blocked IPs."""
    import time as _time
    now = _time.time()
    blocked = {
        ip: {"blocked_until": ts, "remaining_seconds": int(ts - now)}
        for ip, ts in _ip_blocked.items()
        if ts > now
    }
    return jsonify({"blocked_ips": blocked, "count": len(blocked)})


@app.route("/api/security/block-ip", methods=["POST"])
@_require_auth
def block_ip():
    """Manually block an IP address."""
    import time as _time
    data = request.get_json(force=True)
    ip = (data.get("ip") or "").strip()
    duration = int(data.get("duration", 3600))  # default 1 hour
    if not ip:
        return jsonify({"error": "IP address required"}), 400
    _ip_blocked[ip] = _time.time() + duration
    logger.info("Manually blocked IP %s for %d seconds", ip, duration)
    return jsonify({"status": "ok", "ip": ip, "duration": duration})


@app.route("/api/security/unblock-ip", methods=["POST"])
@_require_auth
def unblock_ip():
    """Unblock an IP address."""
    data = request.get_json(force=True)
    ip = (data.get("ip") or "").strip()
    if not ip:
        return jsonify({"error": "IP address required"}), 400
    _ip_blocked.pop(ip, None)
    _ip_fail_counts.pop(ip, None)
    return jsonify({"status": "ok", "ip": ip})


# ---------------------------------------------------------------------------
# Entry-point helper
# ---------------------------------------------------------------------------

def create_app() -> tuple[Flask, SocketIO]:
    """Factory that returns the (app, socketio) pair for external runners."""
    return app, socketio


def main():
    """Run the development server."""
    import socket as _socket

    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "5000"))
    debug = os.environ.get("WEB_DEBUG", "0").lower() in ("1", "true", "yes")
    logger.info("Starting sysadmin-agent web UI on %s:%s", host, port)

    # Patch werkzeug to set SO_REUSEADDR+SO_REUSEPORT so restarts don't
    # fail with "Address already in use" during TIME_WAIT.
    try:
        from werkzeug.serving import BaseWSGIServer
        _orig_init = BaseWSGIServer.server_bind

        def _patched_bind(self):
            self.socket.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            try:
                self.socket.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass  # SO_REUSEPORT not available on all platforms
            return _orig_init(self)

        BaseWSGIServer.server_bind = _patched_bind
    except Exception:
        pass

    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
