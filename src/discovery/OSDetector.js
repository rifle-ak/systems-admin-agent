'use strict';

/**
 * Detects the operating system and distribution of a remote server.
 */
class OSDetector {
  constructor(sshManager) {
    this.ssh = sshManager;
  }

  /**
   * Detect the full OS information from the remote server.
   * @returns {Promise<object>} OS details including type, distro, version, kernel, arch.
   */
  async detect() {
    const results = await Promise.allSettled([
      this.ssh.execute('uname -a'),
      this.ssh.execute('cat /etc/os-release 2>/dev/null || cat /etc/redhat-release 2>/dev/null || cat /etc/issue 2>/dev/null'),
      this.ssh.execute('uname -m'),
      this.ssh.execute('hostname'),
      this.ssh.execute('uptime -p 2>/dev/null || uptime'),
      this.ssh.execute('cat /proc/version 2>/dev/null'),
      this.ssh.execute('lsb_release -a 2>/dev/null'),
    ]);

    const uname = results[0].status === 'fulfilled' ? results[0].value.stdout : '';
    const osRelease = results[1].status === 'fulfilled' ? results[1].value.stdout : '';
    const arch = results[2].status === 'fulfilled' ? results[2].value.stdout : '';
    const hostname = results[3].status === 'fulfilled' ? results[3].value.stdout : '';
    const uptime = results[4].status === 'fulfilled' ? results[4].value.stdout : '';
    const procVersion = results[5].status === 'fulfilled' ? results[5].value.stdout : '';
    const lsbRelease = results[6].status === 'fulfilled' ? results[6].value.stdout : '';

    const osInfo = {
      type: this._detectOSType(uname),
      distribution: this._parseDistribution(osRelease, lsbRelease),
      version: this._parseVersion(osRelease, lsbRelease),
      kernel: this._parseKernel(uname, procVersion),
      architecture: arch,
      hostname: hostname,
      uptime: uptime,
      raw: { uname, osRelease, procVersion, lsbRelease },
    };

    return osInfo;
  }

  _detectOSType(uname) {
    const lower = uname.toLowerCase();
    if (lower.includes('linux')) return 'Linux';
    if (lower.includes('darwin')) return 'macOS';
    if (lower.includes('freebsd')) return 'FreeBSD';
    if (lower.includes('openbsd')) return 'OpenBSD';
    if (lower.includes('cygwin') || lower.includes('mingw')) return 'Windows (Cygwin)';
    return 'Unknown';
  }

  _parseDistribution(osRelease, lsbRelease) {
    // Try os-release first (most modern distros)
    const nameMatch = osRelease.match(/^NAME="?([^"\n]+)"?/m);
    if (nameMatch) return nameMatch[1].trim();

    // Try lsb_release
    const lsbMatch = lsbRelease.match(/Distributor ID:\s*(.+)/);
    if (lsbMatch) return lsbMatch[1].trim();

    // Try common patterns
    if (osRelease.includes('CentOS')) return 'CentOS';
    if (osRelease.includes('Red Hat')) return 'Red Hat Enterprise Linux';
    if (osRelease.includes('Ubuntu')) return 'Ubuntu';
    if (osRelease.includes('Debian')) return 'Debian';
    if (osRelease.includes('Amazon Linux')) return 'Amazon Linux';
    if (osRelease.includes('Alpine')) return 'Alpine Linux';

    return 'Unknown';
  }

  _parseVersion(osRelease, lsbRelease) {
    const versionMatch = osRelease.match(/^VERSION_ID="?([^"\n]+)"?/m);
    if (versionMatch) return versionMatch[1].trim();

    const lsbMatch = lsbRelease.match(/Release:\s*(.+)/);
    if (lsbMatch) return lsbMatch[1].trim();

    return 'Unknown';
  }

  _parseKernel(uname, procVersion) {
    const parts = uname.split(' ');
    if (parts.length >= 3) return parts[2];

    const match = procVersion.match(/version\s+(\S+)/);
    if (match) return match[1];

    return 'Unknown';
  }
}

module.exports = OSDetector;
