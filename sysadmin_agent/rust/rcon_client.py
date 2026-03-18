"""WebSocket RCON client for Rust game servers.

Rust uses a WebSocket-based RCON protocol (not the Valve Source RCON TCP
protocol).  The server listens on ws://<host>:<rcon_port>/<password> and
exchanges JSON messages of the form:

    Send:    {"Identifier": <int>, "Message": "<command>", "Name": "WebRcon"}
    Receive: {"Identifier": <int>, "Message": "<output>", "Type": "Generic"|"Warning"|...}

Reference:
  https://wiki.facepunch.com/rust/rcon
"""

import json
import logging
import threading
import time

logger = logging.getLogger(__name__)


class RCONError(Exception):
    """Base RCON error."""


class RCONAuthError(RCONError):
    """Authentication failed."""


class RCONConnectionError(RCONError):
    """Connection failed or lost."""


class RCONClient:
    """WebSocket RCON client for Rust servers.

    Usage::

        rcon = RCONClient("127.0.0.1", 28016, "mypassword")
        rcon.connect()
        result = rcon.command("status")
        print(result)
        rcon.disconnect()

    Or as a context manager::

        with RCONClient("127.0.0.1", 28016, "mypassword") as rcon:
            print(rcon.command("status"))
    """

    def __init__(self, host, port=28016, password="", timeout=10):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._ws = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._responses = {}   # id -> list of message strings
        self._events = {}      # id -> threading.Event
        self._listener = None
        self._connected = False
        self._closing = False

    @property
    def is_connected(self):
        return self._connected and self._ws is not None

    def connect(self):
        """Connect to the Rust RCON WebSocket server."""
        try:
            import websocket
        except ImportError:
            raise RCONError(
                "The 'websocket-client' package is required for Rust RCON. "
                "Install it with: pip install websocket-client"
            )

        url = f"ws://{self.host}:{self.port}/{self.password}"
        logger.info("Connecting to Rust RCON at %s:%s", self.host, self.port)

        try:
            self._ws = websocket.WebSocket()
            self._ws.settimeout(self.timeout)
            self._ws.connect(url)
        except websocket.WebSocketBadStatusException as e:
            self._ws = None
            if "401" in str(e) or "403" in str(e):
                raise RCONAuthError(
                    f"RCON authentication failed — wrong password: {e}"
                )
            raise RCONConnectionError(f"Failed to connect: {e}")
        except Exception as e:
            self._ws = None
            raise RCONConnectionError(
                f"Failed to connect to {self.host}:{self.port}: {e}"
            )

        self._connected = True
        self._closing = False

        # Start background listener thread to collect responses
        self._listener = threading.Thread(
            target=self._listen_loop, daemon=True
        )
        self._listener.start()

        logger.info("RCON connected to %s:%s", self.host, self.port)
        return self

    def command(self, cmd, timeout=None):
        """Execute an RCON command and return the response string."""
        if not self.is_connected:
            raise RCONConnectionError("Not connected. Call connect() first.")

        timeout = timeout or self.timeout
        req_id = self._next_id()

        # Prepare to receive
        event = threading.Event()
        self._responses[req_id] = []
        self._events[req_id] = event

        # Send command
        payload = json.dumps({
            "Identifier": req_id,
            "Message": cmd,
            "Name": "WebRcon",
        })

        with self._lock:
            try:
                self._ws.send(payload)
            except Exception as e:
                self._cleanup_request(req_id)
                self._connected = False
                raise RCONConnectionError(f"Failed to send command: {e}")

        # Wait for response
        if not event.wait(timeout=timeout):
            result = "".join(self._responses.get(req_id, []))
            self._cleanup_request(req_id)
            if result:
                return result
            raise RCONError(f"Command timed out after {timeout}s: {cmd}")

        result = "".join(self._responses.get(req_id, []))
        self._cleanup_request(req_id)
        return result

    def disconnect(self):
        """Close the RCON connection."""
        self._closing = True
        self._connected = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        # Wake up any waiting commands
        for event in self._events.values():
            event.set()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()
        return False

    # ------------------------------------------------------------------
    # Rust-specific convenience commands
    # ------------------------------------------------------------------

    def server_info(self) -> str:
        """Get detailed server information."""
        return self.command("serverinfo")

    def status(self) -> str:
        """Get server status including player list."""
        return self.command("status")

    def get_fps(self) -> str:
        """Get server FPS / tick rate."""
        return self.command("fps")

    def entity_count(self) -> str:
        """Get total entity count."""
        return self.command("entity.count")

    def player_list(self) -> str:
        """Get connected player list with details."""
        return self.command("players")

    def force_save(self) -> str:
        """Force a server save."""
        return self.command("server.save")

    def gc_collect(self) -> str:
        """Force garbage collection."""
        return self.command("gc.collect")

    def oxide_plugins(self) -> str:
        """List all Oxide/uMod plugins."""
        return self.command("oxide.plugins")

    def oxide_reload(self, plugin=None) -> str:
        """Reload an Oxide plugin (or all if no name given)."""
        if plugin:
            return self.command(f"oxide.reload {plugin}")
        return self.command("oxide.reload *")

    def oxide_version(self) -> str:
        """Get Oxide/uMod version."""
        return self.command("oxide.version")

    def server_say(self, message) -> str:
        """Broadcast a message to all players."""
        return self.command(f'say "{message}"')

    def kick_player(self, player_id, reason="") -> str:
        """Kick a player by Steam ID."""
        return self.command(f'kick {player_id} "{reason}"')

    def ban_player(self, player_id, reason="") -> str:
        """Ban a player by Steam ID."""
        return self.command(f'ban {player_id} "{reason}"')

    def env_weather(self) -> str:
        """Get current weather state."""
        return self.command("weather")

    def env_time(self) -> str:
        """Get current server time."""
        return self.command("env.time")

    def performance_report(self) -> str:
        """Get perf command output (performance counters)."""
        return self.command("perf")

    def pool_status(self) -> str:
        """Get object pool status (memory management)."""
        return self.command("pool.status")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _listen_loop(self):
        """Background thread that reads WebSocket messages and dispatches
        them to the correct waiting command by Identifier."""
        while self._connected and not self._closing:
            try:
                raw = self._ws.recv()
                if not raw:
                    continue
            except Exception:
                if not self._closing:
                    self._connected = False
                    # Wake all waiters
                    for event in list(self._events.values()):
                        event.set()
                break

            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.debug("Non-JSON RCON message: %s", raw[:200])
                continue

            req_id = msg.get("Identifier", -1)
            message = msg.get("Message", "")

            if req_id in self._responses:
                self._responses[req_id].append(message)
                # Rust sends one response per command, signal immediately
                event = self._events.get(req_id)
                if event:
                    event.set()
            else:
                # Unsolicited server message (broadcasts, etc.)
                msg_type = msg.get("Type", "Generic")
                logger.debug("RCON broadcast [%s]: %s", msg_type, message[:200])

    def _cleanup_request(self, req_id):
        """Remove tracking data for a completed request."""
        self._responses.pop(req_id, None)
        self._events.pop(req_id, None)

    def _next_id(self):
        self._request_id += 1
        if self._request_id > 2147483647:
            self._request_id = 1
        return self._request_id
