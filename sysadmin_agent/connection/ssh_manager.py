import threading
import time
import paramiko


class SSHManager:
    # Most SSH servers allow 10 concurrent channels; stay safely under that.
    MAX_CONCURRENT_CHANNELS = 8

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

    @property
    def is_connected(self):
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    def connect(self):
        if not self.username:
            raise ValueError("Username is required")
        if not self.password and not self.private_key_path:
            raise ValueError("Either password or private_key_path is required")

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
        except Exception:
            self._client = None
            raise

        return self

    def execute(self, command, timeout=30):
        if not self.is_connected:
            raise ConnectionError("Not connected. Call connect() first.")

        with self._channel_semaphore:
            stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()

            return {
                "stdout": stdout.read().decode("utf-8", errors="replace"),
                "stderr": stderr.read().decode("utf-8", errors="replace"),
                "exit_code": exit_code,
            }

    def execute_sudo(self, command):
        """Run a command with sudo, feeding the password to the prompt via a channel."""
        if not self.is_connected:
            raise ConnectionError("Not connected. Call connect() first.")
        if not self.password:
            raise ValueError("Password is required for sudo commands")

        with self._channel_semaphore:
            transport = self._client.get_transport()
            channel = transport.open_session()
            channel.get_pty()
            channel.exec_command(f"sudo -S -p '' {command}")

            # Wait briefly for the password prompt, then send password
            time.sleep(0.5)
            channel.send(self.password + "\n")

            # Read output after command completes
            stdout_chunks = []
            stderr_chunks = []

            while not channel.exit_status_ready():
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

    def upload_file(self, local_path, remote_path):
        if not self.is_connected:
            raise ConnectionError("Not connected. Call connect() first.")

        sftp = self._client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
        finally:
            sftp.close()

    def download_file(self, remote_path, local_path):
        if not self.is_connected:
            raise ConnectionError("Not connected. Call connect() first.")

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
