"""
Flask + Flask-SocketIO web application for the systems admin agent.

Provides a browser-based UI for connecting to servers, running diagnostics,
chatting with the AI agent, and managing approval/rollback workflows.
"""

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
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

logger = logging.getLogger(__name__)

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


def _build_server_context(sid: str) -> dict:
    """Build a context dict from what we know about the connected server."""
    data = _get_session_data(sid)
    ctx: dict = {}
    if "os_info" in data:
        ctx["os"] = json.dumps(data["os_info"])
    if "apps" in data:
        ctx["installed_software"] = json.dumps(data["apps"])
    if "diagnostics" in data:
        summary = [
            f"{d['name']}: {d['status']}"
            for d in data["diagnostics"]
            if d["status"] != "ok"
        ]
        if summary:
            ctx["diagnostic_issues"] = "; ".join(summary)
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
        private_key_path = data.get("private_key_path")
        passphrase = data.get("passphrase")

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

    user_request = data.get("message", "").strip()
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

        brain = mods["AgentBrain"]()
        ctx = _build_server_context(sid)

        # If there is prior conversation context, include it
        if len(conversation) > 1:
            ctx["conversation_history"] = json.dumps(conversation[-10:])

        plan = brain.interpret(user_request, server_context=ctx)

        # Store assistant reply in conversation
        conversation.append({"role": "assistant", "content": json.dumps(plan)})

        emit("agent_plan", {"plan": plan, "token_usage": brain.get_token_usage()})

        # If the brain asked questions instead of providing a plan, stop here
        if plan.get("questions"):
            return

        # Execute plan steps (non-destructive ones auto-run, destructive need approval)
        auto_execute = data.get("auto_execute", False)
        if not auto_execute:
            return

        approval = WebApprovalManager(sid)
        rollback = mods["RollbackManager"](ssh)

        for step in plan.get("plan", []):
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

        emit("agent_done", {"token_usage": brain.get_token_usage()})

    except Exception as exc:
        logger.exception("ask_agent failed")
        emit("error", {"message": f"Agent error: {exc}"})


@socketio.on("approve_action")
def handle_approve_action(data):
    """User approves or denies a pending action."""
    approval_id = data.get("approval_id")
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
def handle_run_fix(data):
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
def handle_rollback(data):
    """List snapshots or execute a rollback."""
    sid = request.sid
    ssh = _get_ssh(sid)
    if not ssh:
        emit("error", {"message": "Not connected to a server."})
        return

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
# Security: restrict to localhost by default
# ---------------------------------------------------------------------------

@app.before_request
def _restrict_remote():
    """Block non-local requests unless WEB_ALLOW_REMOTE is set."""
    if os.environ.get("WEB_ALLOW_REMOTE", "").lower() in ("1", "true", "yes"):
        return None
    remote = request.remote_addr
    if remote not in ("127.0.0.1", "::1", "localhost"):
        return "Forbidden: remote access is disabled. Set WEB_ALLOW_REMOTE=1 to allow.", 403
    return None


# ---------------------------------------------------------------------------
# Entry-point helper
# ---------------------------------------------------------------------------

def create_app() -> tuple[Flask, SocketIO]:
    """Factory that returns the (app, socketio) pair for external runners."""
    return app, socketio


def main():
    """Run the development server."""
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "5000"))
    debug = os.environ.get("WEB_DEBUG", "0").lower() in ("1", "true", "yes")
    logger.info("Starting sysadmin-agent web UI on %s:%s", host, port)
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
