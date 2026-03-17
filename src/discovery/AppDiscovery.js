'use strict';

/**
 * Discovers applications and services running on a remote server.
 */
class AppDiscovery {
  constructor(sshManager) {
    this.ssh = sshManager;
  }

  /**
   * Run full application discovery on the remote server.
   * @returns {Promise<object>} Discovered applications grouped by category.
   */
  async discover() {
    const [services, webServers, databases, controlPanels, cms, languages, containers] =
      await Promise.all([
        this._discoverSystemServices(),
        this._discoverWebServers(),
        this._discoverDatabases(),
        this._discoverControlPanels(),
        this._discoverCMS(),
        this._discoverLanguages(),
        this._discoverContainers(),
      ]);

    return {
      services,
      webServers,
      databases,
      controlPanels,
      cms,
      languages,
      containers,
    };
  }

  async _discoverSystemServices() {
    const found = [];
    try {
      // Try systemctl first (systemd)
      const { stdout, code } = await this.ssh.execute(
        'systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null'
      );
      if (code === 0 && stdout) {
        const lines = stdout.split('\n').filter(Boolean);
        for (const line of lines) {
          const match = line.match(/^\s*(\S+\.service)\s+loaded\s+active\s+running\s+(.*)/);
          if (match) {
            found.push({ name: match[1].replace('.service', ''), status: 'running', description: match[2].trim() });
          }
        }
        return found;
      }
    } catch { /* fall through */ }

    try {
      // Fallback: service --status-all
      const { stdout } = await this.ssh.execute('service --status-all 2>/dev/null');
      if (stdout) {
        const lines = stdout.split('\n').filter(Boolean);
        for (const line of lines) {
          const match = line.match(/\[\s*([+-?])\s*\]\s+(.+)/);
          if (match) {
            const status = match[1] === '+' ? 'running' : match[1] === '-' ? 'stopped' : 'unknown';
            found.push({ name: match[2].trim(), status, description: '' });
          }
        }
      }
    } catch { /* ignore */ }

    return found;
  }

  async _discoverWebServers() {
    const webServers = [];

    const checks = [
      { name: 'Apache', commands: ['apache2 -v 2>/dev/null || httpd -v 2>/dev/null'] },
      { name: 'Nginx', commands: ['nginx -v 2>&1'] },
      { name: 'LiteSpeed', commands: ['/usr/local/lsws/bin/lshttpd -v 2>/dev/null'] },
      { name: 'Caddy', commands: ['caddy version 2>/dev/null'] },
    ];

    const results = await Promise.allSettled(
      checks.map(async (check) => {
        for (const cmd of check.commands) {
          const { stdout, stderr, code } = await this.ssh.execute(cmd);
          const output = stdout || stderr;
          if (code === 0 && output) {
            return { name: check.name, version: output.split('\n')[0].trim(), installed: true };
          }
        }
        return { name: check.name, installed: false };
      })
    );

    for (const result of results) {
      if (result.status === 'fulfilled' && result.value.installed) {
        webServers.push(result.value);
      }
    }

    return webServers;
  }

  async _discoverDatabases() {
    const databases = [];

    const checks = [
      { name: 'MySQL', cmd: 'mysql --version 2>/dev/null' },
      { name: 'MariaDB', cmd: 'mariadb --version 2>/dev/null' },
      { name: 'PostgreSQL', cmd: 'psql --version 2>/dev/null' },
      { name: 'MongoDB', cmd: 'mongod --version 2>/dev/null' },
      { name: 'Redis', cmd: 'redis-server --version 2>/dev/null' },
      { name: 'SQLite', cmd: 'sqlite3 --version 2>/dev/null' },
      { name: 'Memcached', cmd: 'memcached -h 2>/dev/null | head -1' },
    ];

    const results = await Promise.allSettled(
      checks.map(async (check) => {
        const { stdout, code } = await this.ssh.execute(check.cmd);
        if (code === 0 && stdout) {
          return { name: check.name, version: stdout.split('\n')[0].trim(), installed: true };
        }
        return { name: check.name, installed: false };
      })
    );

    for (const result of results) {
      if (result.status === 'fulfilled' && result.value.installed) {
        databases.push(result.value);
      }
    }

    return databases;
  }

  async _discoverControlPanels() {
    const panels = [];

    const checks = [
      { name: 'cPanel/WHM', cmd: '/usr/local/cpanel/cpanel -V 2>/dev/null' },
      { name: 'Plesk', cmd: 'plesk version 2>/dev/null || /usr/local/psa/admin/bin/httpsdctl --version 2>/dev/null' },
      { name: 'Webmin', cmd: 'dpkg -l webmin 2>/dev/null || rpm -q webmin 2>/dev/null' },
      { name: 'DirectAdmin', cmd: '/usr/local/directadmin/directadmin v 2>/dev/null' },
      { name: 'CyberPanel', cmd: 'cyberpanel --version 2>/dev/null' },
      { name: 'VestaCP', cmd: 'v-list-sys-info 2>/dev/null' },
      { name: 'HestiaCP', cmd: 'v-list-sys-hestia-autoupdate 2>/dev/null' },
      { name: 'ISPConfig', cmd: 'ls /usr/local/ispconfig 2>/dev/null' },
      { name: 'CloudPanel', cmd: 'clpctl --version 2>/dev/null' },
    ];

    const results = await Promise.allSettled(
      checks.map(async (check) => {
        const { stdout, code } = await this.ssh.execute(check.cmd);
        if (code === 0 && stdout) {
          return { name: check.name, version: stdout.split('\n')[0].trim(), installed: true };
        }
        return { name: check.name, installed: false };
      })
    );

    for (const result of results) {
      if (result.status === 'fulfilled' && result.value.installed) {
        panels.push(result.value);
      }
    }

    return panels;
  }

  async _discoverCMS() {
    const cmsList = [];

    // Check for WordPress installations
    try {
      const { stdout, code } = await this.ssh.execute(
        'find /var/www /home -maxdepth 5 -name "wp-config.php" -type f 2>/dev/null | head -20'
      );
      if (code === 0 && stdout) {
        const paths = stdout.split('\n').filter(Boolean);
        for (const path of paths) {
          const dir = path.replace('/wp-config.php', '');
          let version = 'unknown';
          try {
            const { stdout: vOut } = await this.ssh.execute(`grep "\\$wp_version" ${dir}/wp-includes/version.php 2>/dev/null`);
            const match = vOut.match(/wp_version\s*=\s*'([^']+)'/);
            if (match) version = match[1];
          } catch { /* ignore */ }
          cmsList.push({ name: 'WordPress', version, path: dir });
        }
      }
    } catch { /* ignore */ }

    // Check for Joomla
    try {
      const { stdout, code } = await this.ssh.execute(
        'find /var/www /home -maxdepth 5 -name "configuration.php" -path "*/joomla*" -type f 2>/dev/null || find /var/www /home -maxdepth 4 -name "joomla.xml" -type f 2>/dev/null | head -10'
      );
      if (code === 0 && stdout) {
        cmsList.push({ name: 'Joomla', path: stdout.split('\n')[0].trim() });
      }
    } catch { /* ignore */ }

    // Check for Drupal
    try {
      const { stdout, code } = await this.ssh.execute(
        'find /var/www /home -maxdepth 5 -name "core" -path "*/drupal*" -type d 2>/dev/null | head -10'
      );
      if (code === 0 && stdout) {
        cmsList.push({ name: 'Drupal', path: stdout.split('\n')[0].trim() });
      }
    } catch { /* ignore */ }

    // Check for Magento
    try {
      const { stdout, code } = await this.ssh.execute(
        'find /var/www /home -maxdepth 4 -name "magento" -type d 2>/dev/null || find /var/www /home -maxdepth 5 -path "*/app/etc/env.php" -type f 2>/dev/null | head -10'
      );
      if (code === 0 && stdout) {
        cmsList.push({ name: 'Magento', path: stdout.split('\n')[0].trim() });
      }
    } catch { /* ignore */ }

    return cmsList;
  }

  async _discoverLanguages() {
    const languages = [];

    const checks = [
      { name: 'PHP', cmd: 'php -v 2>/dev/null | head -1' },
      { name: 'Python', cmd: 'python3 --version 2>/dev/null || python --version 2>/dev/null' },
      { name: 'Node.js', cmd: 'node --version 2>/dev/null' },
      { name: 'Ruby', cmd: 'ruby --version 2>/dev/null' },
      { name: 'Java', cmd: 'java -version 2>&1 | head -1' },
      { name: 'Go', cmd: 'go version 2>/dev/null' },
      { name: 'Perl', cmd: 'perl -v 2>/dev/null | grep version' },
    ];

    const results = await Promise.allSettled(
      checks.map(async (check) => {
        const { stdout, stderr, code } = await this.ssh.execute(check.cmd);
        const output = stdout || stderr;
        if (code === 0 && output) {
          return { name: check.name, version: output.split('\n')[0].trim(), installed: true };
        }
        return { name: check.name, installed: false };
      })
    );

    for (const result of results) {
      if (result.status === 'fulfilled' && result.value.installed) {
        languages.push(result.value);
      }
    }

    return languages;
  }

  async _discoverContainers() {
    const containers = [];

    try {
      const { stdout, code } = await this.ssh.execute('docker ps --format "{{.Names}}\\t{{.Image}}\\t{{.Status}}" 2>/dev/null');
      if (code === 0 && stdout) {
        for (const line of stdout.split('\n').filter(Boolean)) {
          const [name, image, status] = line.split('\t');
          containers.push({ runtime: 'Docker', name, image, status });
        }
      }
    } catch { /* ignore */ }

    try {
      const { stdout, code } = await this.ssh.execute('podman ps --format "{{.Names}}\\t{{.Image}}\\t{{.Status}}" 2>/dev/null');
      if (code === 0 && stdout) {
        for (const line of stdout.split('\n').filter(Boolean)) {
          const [name, image, status] = line.split('\t');
          containers.push({ runtime: 'Podman', name, image, status });
        }
      }
    } catch { /* ignore */ }

    return containers;
  }
}

module.exports = AppDiscovery;
