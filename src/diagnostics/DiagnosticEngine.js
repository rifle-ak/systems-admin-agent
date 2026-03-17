'use strict';

/**
 * Diagnostic engine that checks server health and identifies issues.
 * Each diagnostic returns a result with severity and optional fix action.
 */
class DiagnosticEngine {
  constructor(sshManager, approvalManager, rollbackManager) {
    this.ssh = sshManager;
    this.approval = approvalManager;
    this.rollback = rollbackManager;
  }

  /**
   * Run all diagnostics and return a full health report.
   */
  async runAll() {
    const checks = [
      this.checkDiskUsage(),
      this.checkMemoryUsage(),
      this.checkCPULoad(),
      this.checkZombieProcesses(),
      this.checkFailedServices(),
      this.checkDNSResolution(),
      this.checkNTPSync(),
      this.checkOpenPorts(),
      this.checkDiskIOWait(),
      this.checkSwapUsage(),
      this.checkOOMKills(),
      this.checkSSLCertificates(),
      this.checkSecurityUpdates(),
      this.checkFirewall(),
    ];

    const results = await Promise.allSettled(checks);

    return results
      .filter((r) => r.status === 'fulfilled')
      .map((r) => r.value);
  }

  async checkDiskUsage() {
    const { stdout } = await this.ssh.execute("df -h --output=pcent,target -x tmpfs -x devtmpfs 2>/dev/null | tail -n +2");
    const issues = [];

    for (const line of stdout.split('\n').filter(Boolean)) {
      const match = line.match(/\s*(\d+)%\s+(.+)/);
      if (match) {
        const percent = parseInt(match[1], 10);
        const mount = match[2].trim();
        if (percent >= 90) {
          issues.push({ mount, percent, severity: percent >= 95 ? 'critical' : 'warning' });
        }
      }
    }

    return {
      name: 'Disk Usage',
      status: issues.length === 0 ? 'ok' : 'issue',
      severity: issues.some((i) => i.severity === 'critical') ? 'critical' : issues.length > 0 ? 'warning' : 'ok',
      details: issues.length === 0 ? 'All partitions below 90% usage' : issues,
      fix: issues.length > 0 ? {
        description: 'Clean up log files, temp files, and old packages',
        actions: [
          { command: 'journalctl --vacuum-time=3d', destructive: false },
          { command: 'apt-get clean 2>/dev/null || yum clean all 2>/dev/null', destructive: false },
          { command: 'find /tmp -type f -atime +7 -delete', destructive: true, rollbackNote: 'Old temp files will be removed' },
          { command: 'find /var/log -name "*.gz" -type f -mtime +30 -delete', destructive: true, rollbackNote: 'Compressed logs older than 30 days will be removed' },
        ],
      } : null,
    };
  }

  async checkMemoryUsage() {
    const { stdout } = await this.ssh.execute("free -m | grep Mem");
    const match = stdout.match(/Mem:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)/);

    if (!match) return { name: 'Memory Usage', status: 'error', details: 'Could not parse memory info' };

    const total = parseInt(match[1], 10);
    const used = parseInt(match[2], 10);
    const available = parseInt(match[6], 10);
    const usedPercent = Math.round((used / total) * 100);

    const severity = usedPercent >= 95 ? 'critical' : usedPercent >= 85 ? 'warning' : 'ok';

    return {
      name: 'Memory Usage',
      status: severity === 'ok' ? 'ok' : 'issue',
      severity,
      details: { totalMB: total, usedMB: used, availableMB: available, usedPercent },
      fix: severity !== 'ok' ? {
        description: 'Identify and optionally restart top memory consumers',
        actions: [
          { command: 'ps aux --sort=-%mem | head -10', destructive: false },
          { command: 'sync && echo 3 > /proc/sys/vm/drop_caches', destructive: true, rollbackNote: 'Page cache will be cleared (rebuilt automatically)' },
        ],
      } : null,
    };
  }

  async checkCPULoad() {
    const [loadResult, cpuResult] = await Promise.all([
      this.ssh.execute('cat /proc/loadavg'),
      this.ssh.execute('nproc'),
    ]);

    const parts = loadResult.stdout.split(' ');
    const load1 = parseFloat(parts[0]);
    const load5 = parseFloat(parts[1]);
    const load15 = parseFloat(parts[2]);
    const cpuCount = parseInt(cpuResult.stdout, 10);

    const ratio = load5 / cpuCount;
    const severity = ratio >= 2.0 ? 'critical' : ratio >= 1.0 ? 'warning' : 'ok';

    return {
      name: 'CPU Load',
      status: severity === 'ok' ? 'ok' : 'issue',
      severity,
      details: { load1, load5, load15, cpuCount, loadPerCPU: ratio.toFixed(2) },
      fix: severity !== 'ok' ? {
        description: 'Identify top CPU-consuming processes',
        actions: [
          { command: 'ps aux --sort=-%cpu | head -10', destructive: false },
          { command: 'top -bn1 | head -20', destructive: false },
        ],
      } : null,
    };
  }

  async checkZombieProcesses() {
    const { stdout } = await this.ssh.execute("ps aux | awk '$8==\"Z\" {print $0}'");
    const zombies = stdout.split('\n').filter(Boolean);

    return {
      name: 'Zombie Processes',
      status: zombies.length === 0 ? 'ok' : 'issue',
      severity: zombies.length > 10 ? 'warning' : zombies.length > 0 ? 'info' : 'ok',
      details: zombies.length === 0 ? 'No zombie processes found' : { count: zombies.length, processes: zombies.slice(0, 5) },
      fix: zombies.length > 0 ? {
        description: 'Kill parent processes of zombies to clean them up',
        actions: [
          { command: "ps aux | awk '$8==\"Z\" {print $3}' | xargs -r kill -9", destructive: true, rollbackNote: 'Parent processes of zombies will be killed' },
        ],
      } : null,
    };
  }

  async checkFailedServices() {
    const { stdout, code } = await this.ssh.execute('systemctl --failed --no-pager --no-legend 2>/dev/null');
    if (code !== 0) return { name: 'Failed Services', status: 'skipped', details: 'systemd not available' };

    const failed = stdout.split('\n').filter(Boolean);
    const services = failed.map((line) => {
      const match = line.match(/^\s*(\S+)/);
      return match ? match[1] : line.trim();
    }).filter(Boolean);

    return {
      name: 'Failed Services',
      status: services.length === 0 ? 'ok' : 'issue',
      severity: services.length > 0 ? 'warning' : 'ok',
      details: services.length === 0 ? 'No failed services' : { count: services.length, services },
      fix: services.length > 0 ? {
        description: 'Restart failed services',
        actions: services.map((svc) => ({
          command: `systemctl restart ${svc}`,
          destructive: true,
          rollbackNote: `Service ${svc} will be restarted`,
        })),
      } : null,
    };
  }

  async checkDNSResolution() {
    const { code } = await this.ssh.execute('host google.com 2>/dev/null || nslookup google.com 2>/dev/null || dig google.com +short 2>/dev/null');

    return {
      name: 'DNS Resolution',
      status: code === 0 ? 'ok' : 'issue',
      severity: code !== 0 ? 'critical' : 'ok',
      details: code === 0 ? 'DNS resolution working' : 'DNS resolution failed',
      fix: code !== 0 ? {
        description: 'Check and repair DNS configuration',
        actions: [
          { command: 'cat /etc/resolv.conf', destructive: false },
          { command: 'echo "nameserver 8.8.8.8\nnameserver 8.8.4.4" > /etc/resolv.conf', destructive: true, rollbackNote: 'resolv.conf will be overwritten with Google DNS' },
        ],
      } : null,
    };
  }

  async checkNTPSync() {
    const { stdout, code } = await this.ssh.execute('timedatectl status 2>/dev/null');
    const synced = stdout.includes('synchronized: yes') || stdout.includes('NTP synchronized: yes');

    return {
      name: 'NTP Sync',
      status: code !== 0 ? 'skipped' : synced ? 'ok' : 'issue',
      severity: !synced && code === 0 ? 'warning' : 'ok',
      details: code !== 0 ? 'timedatectl not available' : synced ? 'Time is synchronized' : 'Time is NOT synchronized',
      fix: !synced && code === 0 ? {
        description: 'Enable NTP synchronization',
        actions: [
          { command: 'timedatectl set-ntp true', destructive: true, rollbackNote: 'NTP will be enabled' },
        ],
      } : null,
    };
  }

  async checkOpenPorts() {
    const { stdout } = await this.ssh.execute("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null");
    const lines = stdout.split('\n').filter((l) => l.includes('LISTEN'));

    const ports = lines.map((line) => {
      const match = line.match(/:(\d+)\s/);
      return match ? parseInt(match[1], 10) : null;
    }).filter(Boolean);

    return {
      name: 'Open Ports',
      status: 'ok',
      severity: 'info',
      details: { listeningPorts: [...new Set(ports)].sort((a, b) => a - b), count: new Set(ports).size },
      fix: null,
    };
  }

  async checkDiskIOWait() {
    const { stdout, code } = await this.ssh.execute("iostat -c 1 2 2>/dev/null | tail -1");
    if (code !== 0) return { name: 'Disk I/O Wait', status: 'skipped', details: 'iostat not available' };

    const parts = stdout.trim().split(/\s+/);
    const iowait = parseFloat(parts[3]);
    const severity = iowait >= 20 ? 'critical' : iowait >= 10 ? 'warning' : 'ok';

    return {
      name: 'Disk I/O Wait',
      status: severity === 'ok' ? 'ok' : 'issue',
      severity,
      details: { iowaitPercent: iowait },
      fix: severity !== 'ok' ? {
        description: 'Identify I/O-heavy processes',
        actions: [{ command: 'iotop -bon1 2>/dev/null | head -20', destructive: false }],
      } : null,
    };
  }

  async checkSwapUsage() {
    const { stdout } = await this.ssh.execute("free -m | grep Swap");
    const match = stdout.match(/Swap:\s+(\d+)\s+(\d+)\s+(\d+)/);

    if (!match) return { name: 'Swap Usage', status: 'ok', details: 'No swap configured' };

    const total = parseInt(match[1], 10);
    const used = parseInt(match[2], 10);
    if (total === 0) return { name: 'Swap Usage', status: 'info', severity: 'info', details: 'No swap configured' };

    const usedPercent = Math.round((used / total) * 100);
    const severity = usedPercent >= 80 ? 'warning' : 'ok';

    return {
      name: 'Swap Usage',
      status: severity === 'ok' ? 'ok' : 'issue',
      severity,
      details: { totalMB: total, usedMB: used, usedPercent },
      fix: null,
    };
  }

  async checkOOMKills() {
    const { stdout } = await this.ssh.execute("dmesg -T 2>/dev/null | grep -i 'out of memory' | tail -5 || journalctl -k --no-pager 2>/dev/null | grep -i 'out of memory' | tail -5");
    const lines = stdout.split('\n').filter(Boolean);

    return {
      name: 'OOM Kills',
      status: lines.length === 0 ? 'ok' : 'issue',
      severity: lines.length > 0 ? 'warning' : 'ok',
      details: lines.length === 0 ? 'No recent OOM kills' : { count: lines.length, recent: lines },
      fix: null,
    };
  }

  async checkSSLCertificates() {
    const results = [];
    // Check if certbot is available
    const { stdout, code } = await this.ssh.execute('certbot certificates 2>/dev/null');
    if (code === 0 && stdout.includes('Certificate Name')) {
      const certs = stdout.split('Certificate Name:').slice(1);
      for (const cert of certs) {
        const nameMatch = cert.match(/^\s*(.+)/);
        const expiryMatch = cert.match(/Expiry Date:\s*(.+)\s/);
        if (nameMatch) {
          results.push({
            name: nameMatch[1].trim(),
            expiry: expiryMatch ? expiryMatch[1].trim() : 'unknown',
          });
        }
      }
    }

    return {
      name: 'SSL Certificates',
      status: results.length === 0 ? 'skipped' : 'ok',
      severity: 'info',
      details: results.length === 0 ? 'No certbot certificates found' : results,
      fix: null,
    };
  }

  async checkSecurityUpdates() {
    // Try apt-based systems first, then yum
    let { stdout, code } = await this.ssh.execute('apt list --upgradable 2>/dev/null | grep -i security | wc -l');
    if (code === 0 && stdout.trim() !== '') {
      const count = parseInt(stdout.trim(), 10);
      return {
        name: 'Security Updates',
        status: count === 0 ? 'ok' : 'issue',
        severity: count > 10 ? 'warning' : count > 0 ? 'info' : 'ok',
        details: count === 0 ? 'System is up to date' : { pendingSecurityUpdates: count },
        fix: count > 0 ? {
          description: 'Install security updates',
          actions: [
            { command: 'apt-get update && apt-get upgrade -y --only-upgrade', destructive: true, rollbackNote: 'Packages will be upgraded' },
          ],
        } : null,
      };
    }

    ({ stdout, code } = await this.ssh.execute('yum check-update --security 2>/dev/null | tail -n +3 | wc -l'));
    const count = parseInt(stdout.trim(), 10) || 0;

    return {
      name: 'Security Updates',
      status: count === 0 ? 'ok' : 'issue',
      severity: count > 10 ? 'warning' : count > 0 ? 'info' : 'ok',
      details: count === 0 ? 'System is up to date' : { pendingSecurityUpdates: count },
      fix: count > 0 ? {
        description: 'Install security updates',
        actions: [
          { command: 'yum update --security -y', destructive: true, rollbackNote: 'Packages will be upgraded' },
        ],
      } : null,
    };
  }

  async checkFirewall() {
    const [ufw, firewalld, iptables] = await Promise.allSettled([
      this.ssh.execute('ufw status 2>/dev/null'),
      this.ssh.execute('firewall-cmd --state 2>/dev/null'),
      this.ssh.execute('iptables -L -n 2>/dev/null | head -20'),
    ]);

    let firewallType = 'none';
    let active = false;
    let details = 'No firewall detected';

    if (ufw.status === 'fulfilled' && ufw.value.stdout.includes('active')) {
      firewallType = 'ufw';
      active = true;
      details = ufw.value.stdout;
    } else if (firewalld.status === 'fulfilled' && firewalld.value.stdout.includes('running')) {
      firewallType = 'firewalld';
      active = true;
      details = 'firewalld is running';
    } else if (iptables.status === 'fulfilled' && iptables.value.code === 0) {
      firewallType = 'iptables';
      active = true;
      details = iptables.value.stdout;
    }

    return {
      name: 'Firewall',
      status: active ? 'ok' : 'issue',
      severity: active ? 'ok' : 'warning',
      details: { firewallType, active, info: typeof details === 'string' ? details.substring(0, 500) : details },
      fix: null,
    };
  }

  /**
   * Apply a fix action with approval and rollback support.
   * @param {object} action - The fix action to apply.
   * @returns {Promise<object>} Result of the fix.
   */
  async applyFix(action) {
    if (action.destructive) {
      // Create rollback snapshot before destructive action
      const snapshotId = await this.rollback.createSnapshot(action.command, action.rollbackNote);

      // Request human approval
      const approved = await this.approval.requestApproval({
        action: action.command,
        description: action.rollbackNote || action.description,
        snapshotId,
        destructive: true,
      });

      if (!approved) {
        await this.rollback.removeSnapshot(snapshotId);
        return { applied: false, reason: 'User denied approval' };
      }
    }

    const result = await this.ssh.executeSudo(action.command);
    return { applied: true, result };
  }
}

module.exports = DiagnosticEngine;
