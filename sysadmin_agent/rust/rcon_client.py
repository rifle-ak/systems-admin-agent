"""Source RCON protocol client for Rust game servers.

Implements the Valve Source RCON protocol used by Rust (via RustDedicated).
Supports authentication, command execution, and multi-packet responses.

Protocol reference:
  https://developer.valvesoftware.com/wiki/Source_RCON_Protocol
"""

import logging
import select
import socket
import struct
import threading
import time

logger = logging.getLogger(__name__)

# Packet types
SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


class RCONError(Exception):
    """Base RCON error."""


class RCONAuthError(RCONError):
    """Authentication failed."""


class RCONConnectionError(RCONError):
    """Connection failed or lost."""


class RCONClient:
    """Source RCON client for Rust servers.

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
        self._sock = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._authenticated = False

    @property
    def is_connected(self):
        return self._sock is not None and self._authenticated

    def connect(self):
        """Connect and authenticate with the RCON server."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            self._sock.connect((self.host, self.port))
        except (socket.error, OSError) as e:
            self._sock = None
            raise RCONConnectionError(f"Failed to connect to {self.host}:{self.port}: {e}")

        # Authenticate
        req_id = self._next_id()
        self._send_packet(req_id, SERVERDATA_AUTH, self.password)

        # Read auth response — Rust may send an empty RESPONSE_VALUE first
        for _ in range(3):
            resp_id, resp_type, body = self._recv_packet()
            if resp_type == SERVERDATA_AUTH_RESPONSE:
                if resp_id == -1:
                    self.disconnect()
                    raise RCONAuthError("RCON authentication failed — wrong password")
                self._authenticated = True
                return self
            # Some implementations send SERVERDATA_RESPONSE_VALUE before the auth response

        self.disconnect()
        raise RCONAuthError("RCON authentication failed — no valid response")

    def command(self, cmd, timeout=None):
        """Execute an RCON command and return the response string.

        Handles multi-packet responses by sending a trailing empty
        SERVERDATA_RESPONSE_VALUE as a sentinel.
        """
        if not self.is_connected:
            raise RCONConnectionError("Not connected. Call connect() first.")

        with self._lock:
            old_timeout = self._sock.gettimeout()
            if timeout:
                self._sock.settimeout(timeout)
            try:
                return self._do_command(cmd)
            finally:
                self._sock.settimeout(old_timeout)

    def disconnect(self):
        """Close the RCON connection."""
        self._authenticated = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

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
    # Protocol internals
    # ------------------------------------------------------------------

    def _do_command(self, cmd):
        """Send a command and collect the full multi-packet response."""
        cmd_id = self._next_id()
        sentinel_id = self._next_id()

        # Send the command
        self._send_packet(cmd_id, SERVERDATA_EXECCOMMAND, cmd)

        # Send an empty command as a sentinel — the response to this tells us
        # the real response is complete (Valve multi-packet trick)
        self._send_packet(sentinel_id, SERVERDATA_RESPONSE_VALUE, "")

        # Collect response packets until we see the sentinel response
        response_parts = []
        deadline = time.monotonic() + self.timeout

        while time.monotonic() < deadline:
            try:
                resp_id, resp_type, body = self._recv_packet()
            except socket.timeout:
                break
            except (OSError, struct.error) as e:
                raise RCONConnectionError(f"Connection lost: {e}")

            if resp_id == sentinel_id:
                # Got the sentinel — we have the complete response
                break
            if resp_id == cmd_id:
                response_parts.append(body)

        return "".join(response_parts)

    def _send_packet(self, request_id, packet_type, body):
        """Build and send a Source RCON packet."""
        body_bytes = body.encode("utf-8") + b"\x00"
        # Packet: size (4) + id (4) + type (4) + body + null terminator
        packet = struct.pack("<iii", request_id, packet_type, 0)[:8]
        packet = struct.pack("<ii", request_id, packet_type) + body_bytes + b"\x00"
        size = len(packet)
        packet = struct.pack("<i", size) + packet

        try:
            self._sock.sendall(packet)
        except (socket.error, OSError) as e:
            self._authenticated = False
            raise RCONConnectionError(f"Failed to send: {e}")

    def _recv_packet(self):
        """Receive and parse a single Source RCON packet.

        Returns (request_id, packet_type, body_string).
        """
        # Read 4-byte size prefix
        raw_size = self._recv_exact(4)
        (size,) = struct.unpack("<i", raw_size)

        if size < 10 or size > 65536:
            raise RCONError(f"Invalid packet size: {size}")

        # Read the rest of the packet
        raw = self._recv_exact(size)
        request_id, packet_type = struct.unpack("<ii", raw[:8])
        # Body is everything after id+type, minus the two null terminators
        body = raw[8:].rstrip(b"\x00").decode("utf-8", errors="replace")

        return request_id, packet_type, body

    def _recv_exact(self, count):
        """Read exactly `count` bytes from the socket."""
        buf = b""
        while len(buf) < count:
            try:
                chunk = self._sock.recv(count - len(buf))
            except socket.timeout:
                raise
            except (socket.error, OSError) as e:
                self._authenticated = False
                raise RCONConnectionError(f"Connection lost: {e}")
            if not chunk:
                self._authenticated = False
                raise RCONConnectionError("Connection closed by server")
            buf += chunk
        return buf

    def _next_id(self):
        self._request_id += 1
        if self._request_id > 2147483647:
            self._request_id = 1
        return self._request_id
