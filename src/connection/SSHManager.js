'use strict';

const { Client } = require('ssh2');
const fs = require('fs');
const { EventEmitter } = require('events');

/**
 * Manages SSH connections to remote servers.
 * Supports password and private key authentication.
 */
class SSHManager extends EventEmitter {
  constructor(options = {}) {
    super();
    this.host = options.host;
    this.port = options.port || 22;
    this.username = options.username;
    this.password = options.password || null;
    this.privateKeyPath = options.privateKeyPath || null;
    this.passphrase = options.passphrase || null;
    this.timeout = options.timeout || 15000;
    this.client = null;
    this.connected = false;
  }

  /**
   * Establish an SSH connection to the remote server.
   */
  async connect() {
    return new Promise((resolve, reject) => {
      this.client = new Client();

      const config = {
        host: this.host,
        port: this.port,
        username: this.username,
        readyTimeout: this.timeout,
      };

      if (this.privateKeyPath) {
        try {
          config.privateKey = fs.readFileSync(this.privateKeyPath);
          if (this.passphrase) {
            config.passphrase = this.passphrase;
          }
        } catch (err) {
          return reject(new Error(`Failed to read private key at ${this.privateKeyPath}: ${err.message}`));
        }
      } else if (this.password) {
        config.password = this.password;
      } else {
        return reject(new Error('Either password or privateKeyPath must be provided'));
      }

      this.client.on('ready', () => {
        this.connected = true;
        this.emit('connected', { host: this.host, port: this.port });
        resolve(this);
      });

      this.client.on('error', (err) => {
        this.connected = false;
        this.emit('error', err);
        reject(new Error(`SSH connection failed to ${this.host}:${this.port}: ${err.message}`));
      });

      this.client.on('close', () => {
        this.connected = false;
        this.emit('disconnected', { host: this.host });
      });

      this.client.connect(config);
    });
  }

  /**
   * Execute a command on the remote server and return stdout/stderr.
   * @param {string} command - The command to execute.
   * @param {object} options - Execution options.
   * @param {number} options.timeout - Command timeout in ms (default 30000).
   * @returns {Promise<{stdout: string, stderr: string, code: number}>}
   */
  async execute(command, options = {}) {
    if (!this.connected || !this.client) {
      throw new Error('Not connected. Call connect() first.');
    }

    const cmdTimeout = options.timeout || 30000;

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`Command timed out after ${cmdTimeout}ms: ${command}`));
      }, cmdTimeout);

      this.client.exec(command, (err, stream) => {
        if (err) {
          clearTimeout(timer);
          return reject(new Error(`Failed to execute command: ${err.message}`));
        }

        let stdout = '';
        let stderr = '';

        stream.on('data', (data) => {
          stdout += data.toString();
        });

        stream.stderr.on('data', (data) => {
          stderr += data.toString();
        });

        stream.on('close', (code) => {
          clearTimeout(timer);
          resolve({ stdout: stdout.trim(), stderr: stderr.trim(), code: code || 0 });
        });
      });
    });
  }

  /**
   * Execute a command with sudo. Handles password prompt automatically.
   * @param {string} command - The command to execute with sudo.
   * @returns {Promise<{stdout: string, stderr: string, code: number}>}
   */
  async executeSudo(command) {
    if (!this.password) {
      // Try passwordless sudo first
      return this.execute(`sudo -n ${command}`);
    }
    // Use sudo with password piped via stdin
    return this.execute(`echo '${this.password.replace(/'/g, "'\\''")}' | sudo -S ${command}`);
  }

  /**
   * Upload a file to the remote server via SFTP.
   */
  async uploadFile(localPath, remotePath) {
    if (!this.connected || !this.client) {
      throw new Error('Not connected. Call connect() first.');
    }

    return new Promise((resolve, reject) => {
      this.client.sftp((err, sftp) => {
        if (err) return reject(new Error(`SFTP session failed: ${err.message}`));

        sftp.fastPut(localPath, remotePath, (err) => {
          if (err) return reject(new Error(`File upload failed: ${err.message}`));
          resolve({ localPath, remotePath });
        });
      });
    });
  }

  /**
   * Download a file from the remote server via SFTP.
   */
  async downloadFile(remotePath, localPath) {
    if (!this.connected || !this.client) {
      throw new Error('Not connected. Call connect() first.');
    }

    return new Promise((resolve, reject) => {
      this.client.sftp((err, sftp) => {
        if (err) return reject(new Error(`SFTP session failed: ${err.message}`));

        sftp.fastGet(remotePath, localPath, (err) => {
          if (err) return reject(new Error(`File download failed: ${err.message}`));
          resolve({ remotePath, localPath });
        });
      });
    });
  }

  /**
   * Disconnect from the remote server.
   */
  disconnect() {
    if (this.client) {
      this.client.end();
      this.connected = false;
      this.client = null;
    }
  }
}

module.exports = SSHManager;
