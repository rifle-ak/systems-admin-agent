import logging
import threading
import time
import paramiko

logger = logging.getLogger(__name__)


class SSHManager:
    # Keep well under the SSH server's channel limit to avoid rejections.
    MAX_CONCURRENT_CHANNELS = 5
    # Number of times to retry an operation after reconnecting
    MAX_RETRIES = 3

    def __init__(self, host, port=22, username=None, password=None,
                 private_key_path=None, passphrase=None, timeout=15):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.private_key_path = private_key_path
        self.passphrase = passphrase
        self.timeout = timeout
        self._client = None
        self._channel_semaphore = threading.Semaphore(self.MAX_CONCURRENT_CHANNELS)
        self._connect_lock = threading.Lock()

    @property
    def is_connected(self):
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    def _ensure_connected(self):
        """Check connection health and reconnect if the transport is dead."""
        if self.is_connected:
            return
        logger.info("SSH connection lost to %s:%s, reconnecting...", self.host, self.port)
        with self._connect_lock:
            # Double-check after acquiring lock (another thread may have reconnected)
            if self.is_connected:
                return
            self._do_connect()

    def _do_connect(self):
        """Internal connect logic (no validation, assumes lock is held or init)."""
        # Close stale client if any
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": self.timeout,
        }

        if self.private_key_path:
            key = paramiko.RSAKey.from_private_key_file(
                self.private_key_path, password=self.passphrase
            )
            connect_kwargs["pkey"] = key
        else:
            connect_kwargs["password"] = self.password

        try:
            self._client.connect(**connect_kwargs)
            # Enable keepalive to prevent stale connections
            transport = self._client.get_transport()
            if transport:
                transport.set_keepalive(30)  # Send keepalive every 30 seconds
        except Exception:
            self._client = None
            raise

    def connect(self):
        if not self.username:
            raise ValueError("Username is required")
        if not self.password and not self.private_key_path:
            raise ValueError("Either password or private_key_path is required")

        with self._connect_lock:
            self._do_connect()
        return self

    def execute(self, command, timeout=30):
        """Execute a command, auto-reconnecting on stale connections."""
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                self._ensure_connected()
                with self._channel_semaphore:
                    stdin, stdout, stderr = self._client.exec_command(
                        command, timeout=timeout
                    )
                    exit_code = stdout.channel.recv_exit_status()
                    return {
                        "stdout": stdout.read().decode("utf-8", errors="replace"),
                        "stderr": stderr.read().decode("utf-8", errors="replace"),
                        "exit_code": exit_code,
                    }
            except (OSError, paramiko.SSHException, paramiko.ChannelException,
                    EOFError, ConnectionResetError) as e:
                if attempt < self.MAX_RETRIES:
                    backoff = 2 ** attempt  # 1s, 2s
                    logger.warning(
                        "SSH execute failed (attempt %d/%d): %s — reconnecting in %ds",
                        attempt + 1, self.MAX_RETRIES + 1, e, backoff,
                    )
                    # Force reconnect on next _ensure_connected
                    if self._client:
                        try:
                            self._client.close()
                        except Exception:
                            pass
                        self._client = None
                    time.sleep(backoff)
                else:
                    raise

    def execute_sudo(self, command):
        """Run a command with sudo, auto-reconnecting on stale connections."""
        if not self.password:
            raise ValueError("Password is required for sudo commands")

        last_error = None
        for attempt in range(self.MAX_RETRIES + 1):
            channel = None
            try:
                self._ensure_connected()
                with self._channel_semaphore:
                    transport = self._client.get_transport()
                    if not transport or not transport.is_active():
                        raise paramiko.SSHException("Transport is not active")

                    channel = transport.open_session()
                    channel.get_pty()
                    channel.settimeout(30)
                    channel.exec_command(f"sudo -S -p '' {command}")

                    # Wait for the channel to be ready for sending, then
                    # send the password.  Use a short recv loop instead of
                    # a blind sleep so we notice a dead socket immediately.
                    send_deadline = time.monotonic() + 3
                    while time.monotonic() < send_deadline:
                        if channel.recv_ready():
                            # Consume the (empty) password prompt
                            channel.recv(4096)
                            break
                        # Check the channel is still alive
                        if channel.closed or not transport.is_active():
                            raise paramiko.SSHException("Channel died before password send")
                        time.sleep(0.1)
                    channel.sendall((self.password + "\n").encode("utf-8"))

                    # Read output after command completes
                    stdout_chunks = []
                    stderr_chunks = []
                    deadline = time.monotonic() + 120  # 2-minute max

                    while not channel.exit_status_ready():
                        if time.monotonic() > deadline:
                            channel.close()
                            return {
                                "stdout": b"".join(stdout_chunks).decode("utf-8", errors="replace"),
                                "stderr": "Command timed out after 120 seconds",
                                "exit_code": -1,
                            }
                        if channel.recv_ready():
                            stdout_chunks.append(channel.recv(4096))
                        if channel.recv_stderr_ready():
                            stderr_chunks.append(channel.recv_stderr(4096))
                        time.sleep(0.1)

                    # Drain remaining data
                    while channel.recv_ready():
                        stdout_chunks.append(channel.recv(4096))
                    while channel.recv_stderr_ready():
                        stderr_chunks.append(channel.recv_stderr(4096))

                    exit_code = channel.recv_exit_status()
                    channel.close()

                    return {
                        "stdout": b"".join(stdout_chunks).decode("utf-8", errors="replace"),
                        "stderr": b"".join(stderr_chunks).decode("utf-8", errors="replace"),
                        "exit_code": exit_code,
                    }
            except (OSError, paramiko.SSHException, paramiko.ChannelException,
                    EOFError, ConnectionResetError) as e:
                last_error = e
                # Make sure the channel is cleaned up
                if channel:
                    try:
                        channel.close()
                    except Exception:
                        pass
                if attempt < self.MAX_RETRIES:
                    backoff = 2 ** attempt  # 1s, 2s
                    logger.warning(
                        "SSH execute_sudo failed (attempt %d/%d): %s — reconnecting in %ds",
                        attempt + 1, self.MAX_RETRIES + 1, e, backoff,
                    )
                    # Force full reconnect
                    if self._client:
                        try:
                            self._client.close()
                        except Exception:
                            pass
                        self._client = None
                    time.sleep(backoff)
                else:
                    raise

    def upload_file(self, local_path, remote_path):
        self._ensure_connected()
        sftp = self._client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
        finally:
            sftp.close()

    def download_file(self, remote_path, local_path):
        self._ensure_connected()
        sftp = self._client.open_sftp()
        try:
            sftp.get(remote_path, local_path)
        finally:
            sftp.close()

    def disconnect(self):
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
