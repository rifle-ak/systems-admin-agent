'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

/**
 * Manages rollback snapshots for destructive server operations.
 * Before any destructive action, a snapshot of relevant state is captured.
 * If something goes wrong, the snapshot can be restored.
 */
class RollbackManager {
  constructor(sshManager, options = {}) {
    this.ssh = sshManager;
    this.snapshotDir = options.snapshotDir || path.join(process.cwd(), '.sysadmin-snapshots');
    this.snapshots = new Map();

    // Ensure local snapshot directory exists
    if (!fs.existsSync(this.snapshotDir)) {
      fs.mkdirSync(this.snapshotDir, { recursive: true });
    }

    this._loadSnapshots();
  }

  /**
   * Create a rollback snapshot before a destructive action.
   * @param {string} command - The command about to be executed.
   * @param {string} description - Human-readable description.
   * @param {object} options - Additional snapshot options.
   * @returns {Promise<string>} The snapshot ID.
   */
  async createSnapshot(command, description, options = {}) {
    const id = crypto.randomUUID();
    const timestamp = new Date().toISOString();

    const snapshot = {
      id,
      timestamp,
      command,
      description,
      status: 'pending',
      backups: [],
    };

    // Determine what to back up based on the command
    const filesToBackup = this._identifyAffectedFiles(command);

    for (const remoteFile of filesToBackup) {
      try {
        const backupData = await this._backupRemoteFile(id, remoteFile);
        snapshot.backups.push(backupData);
      } catch (err) {
        snapshot.backups.push({ file: remoteFile, error: err.message });
      }
    }

    // Capture service states if the command involves services
    if (command.includes('systemctl') || command.includes('service')) {
      try {
        const { stdout } = await this.ssh.execute('systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null');
        snapshot.serviceState = stdout;
      } catch { /* ignore */ }
    }

    // Capture package state if command involves package management
    if (command.includes('apt') || command.includes('yum') || command.includes('dnf')) {
      try {
        const { stdout } = await this.ssh.execute(
          'dpkg --get-selections 2>/dev/null || rpm -qa 2>/dev/null'
        );
        snapshot.packageState = stdout;
      } catch { /* ignore */ }
    }

    snapshot.status = 'ready';
    this.snapshots.set(id, snapshot);
    this._saveSnapshots();

    return id;
  }

  /**
   * Rollback a specific snapshot - restore state to before the action.
   * @param {string} snapshotId - The snapshot to roll back.
   * @returns {Promise<object>} Rollback result.
   */
  async rollback(snapshotId) {
    const snapshot = this.snapshots.get(snapshotId);
    if (!snapshot) {
      throw new Error(`Snapshot ${snapshotId} not found`);
    }

    const results = [];

    // Restore backed-up files
    for (const backup of snapshot.backups) {
      if (backup.error) {
        results.push({ file: backup.file, status: 'skipped', reason: backup.error });
        continue;
      }

      try {
        await this._restoreRemoteFile(backup);
        results.push({ file: backup.file, status: 'restored' });
      } catch (err) {
        results.push({ file: backup.file, status: 'failed', error: err.message });
      }
    }

    // Restore service states if applicable
    if (snapshot.serviceState && snapshot.command.includes('systemctl')) {
      try {
        const serviceName = snapshot.command.match(/systemctl\s+\w+\s+(\S+)/);
        if (serviceName) {
          // Determine if the service was running before
          const wasRunning = snapshot.serviceState.includes(serviceName[1]);
          if (wasRunning) {
            await this.ssh.executeSudo(`systemctl start ${serviceName[1]}`);
          } else {
            await this.ssh.executeSudo(`systemctl stop ${serviceName[1]}`);
          }
          results.push({ service: serviceName[1], status: 'restored' });
        }
      } catch (err) {
        results.push({ service: 'unknown', status: 'failed', error: err.message });
      }
    }

    snapshot.status = 'rolled_back';
    snapshot.rollbackTime = new Date().toISOString();
    snapshot.rollbackResults = results;
    this._saveSnapshots();

    return { snapshotId, results };
  }

  /**
   * List all snapshots with their current status.
   */
  listSnapshots() {
    return Array.from(this.snapshots.values()).map((s) => ({
      id: s.id,
      timestamp: s.timestamp,
      command: s.command,
      description: s.description,
      status: s.status,
      backupCount: s.backups.length,
    }));
  }

  /**
   * Get details of a specific snapshot.
   */
  getSnapshot(snapshotId) {
    return this.snapshots.get(snapshotId) || null;
  }

  /**
   * Remove a snapshot (e.g., when action was not approved).
   */
  async removeSnapshot(snapshotId) {
    const snapshot = this.snapshots.get(snapshotId);
    if (!snapshot) return;

    // Clean up local backup files
    for (const backup of snapshot.backups) {
      if (backup.localPath && fs.existsSync(backup.localPath)) {
        fs.unlinkSync(backup.localPath);
      }
    }

    this.snapshots.delete(snapshotId);
    this._saveSnapshots();
  }

  /**
   * Identify files that may be affected by a command.
   */
  _identifyAffectedFiles(command) {
    const files = [];

    // Common config files affected by various commands
    const patterns = [
      { regex: /resolv\.conf/, file: '/etc/resolv.conf' },
      { regex: /hosts/, file: '/etc/hosts' },
      { regex: /sshd/, file: '/etc/ssh/sshd_config' },
      { regex: /nginx/, file: '/etc/nginx/nginx.conf' },
      { regex: /apache|httpd/, file: '/etc/apache2/apache2.conf' },
      { regex: /mysql|mariadb/, file: '/etc/mysql/my.cnf' },
      { regex: /php/, file: '/etc/php.ini' },
      { regex: /cron/, file: '/etc/crontab' },
      { regex: /fstab/, file: '/etc/fstab' },
      { regex: /iptables/, file: '/etc/iptables/rules.v4' },
    ];

    for (const pattern of patterns) {
      if (pattern.regex.test(command)) {
        files.push(pattern.file);
      }
    }

    // Extract explicit file paths from the command
    const filePathMatch = command.match(/(?:>|>>|cp|mv|rm|echo.*>)\s+(\S+)/g);
    if (filePathMatch) {
      for (const match of filePathMatch) {
        const filePath = match.replace(/^(>|>>|cp|mv|rm|echo.*>)\s+/, '').trim();
        if (filePath.startsWith('/')) {
          files.push(filePath);
        }
      }
    }

    return [...new Set(files)];
  }

  async _backupRemoteFile(snapshotId, remotePath) {
    const localDir = path.join(this.snapshotDir, snapshotId);
    if (!fs.existsSync(localDir)) {
      fs.mkdirSync(localDir, { recursive: true });
    }

    const safeFileName = remotePath.replace(/\//g, '_');
    const localPath = path.join(localDir, safeFileName);

    try {
      // Check if file exists on remote first
      const { code } = await this.ssh.execute(`test -f ${remotePath} && echo exists`);
      if (code !== 0) {
        return { file: remotePath, existed: false, localPath: null };
      }

      await this.ssh.downloadFile(remotePath, localPath);

      // Also capture file permissions
      const { stdout: perms } = await this.ssh.execute(`stat -c '%a %U %G' ${remotePath} 2>/dev/null`);

      return {
        file: remotePath,
        localPath,
        existed: true,
        permissions: perms.trim(),
      };
    } catch (err) {
      return { file: remotePath, error: err.message };
    }
  }

  async _restoreRemoteFile(backup) {
    if (!backup.existed) {
      // File didn't exist before, remove it if it was created
      await this.ssh.executeSudo(`rm -f ${backup.file}`);
      return;
    }

    if (!backup.localPath || !fs.existsSync(backup.localPath)) {
      throw new Error(`Local backup file not found: ${backup.localPath}`);
    }

    // Upload the backup
    const tempPath = `/tmp/.sysadmin-restore-${Date.now()}`;
    await this.ssh.uploadFile(backup.localPath, tempPath);
    await this.ssh.executeSudo(`cp ${tempPath} ${backup.file}`);

    // Restore permissions
    if (backup.permissions) {
      const [mode, owner, group] = backup.permissions.split(' ');
      await this.ssh.executeSudo(`chmod ${mode} ${backup.file}`);
      await this.ssh.executeSudo(`chown ${owner}:${group} ${backup.file}`);
    }

    // Clean up temp file
    await this.ssh.execute(`rm -f ${tempPath}`);
  }

  _saveSnapshots() {
    const dataFile = path.join(this.snapshotDir, 'snapshots.json');
    const data = Object.fromEntries(this.snapshots);
    fs.writeFileSync(dataFile, JSON.stringify(data, null, 2));
  }

  _loadSnapshots() {
    const dataFile = path.join(this.snapshotDir, 'snapshots.json');
    if (fs.existsSync(dataFile)) {
      try {
        const data = JSON.parse(fs.readFileSync(dataFile, 'utf-8'));
        for (const [id, snapshot] of Object.entries(data)) {
          this.snapshots.set(id, snapshot);
        }
      } catch { /* ignore corrupt file */ }
    }
  }
}

module.exports = RollbackManager;
