'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert');
const OSDetector = require('../src/discovery/OSDetector');

/**
 * Create a mock SSH manager that returns predefined responses.
 */
function createMockSSH(responses) {
  return {
    execute: async (cmd) => {
      for (const [pattern, response] of Object.entries(responses)) {
        if (cmd.includes(pattern)) {
          return { stdout: response, stderr: '', code: 0 };
        }
      }
      return { stdout: '', stderr: 'command not found', code: 127 };
    },
  };
}

describe('OSDetector', () => {
  it('should detect Ubuntu Linux', async () => {
    const mockSSH = createMockSSH({
      'uname -a': 'Linux myserver 5.15.0-91-generic #101-Ubuntu SMP x86_64 GNU/Linux',
      'os-release': 'NAME="Ubuntu"\nVERSION_ID="22.04"\nID=ubuntu',
      'uname -m': 'x86_64',
      'hostname': 'myserver',
      'uptime': 'up 15 days, 3 hours',
      'proc/version': 'Linux version 5.15.0-91-generic',
      'lsb_release': 'Distributor ID: Ubuntu\nRelease: 22.04',
    });

    const detector = new OSDetector(mockSSH);
    const osInfo = await detector.detect();

    assert.strictEqual(osInfo.type, 'Linux');
    assert.strictEqual(osInfo.distribution, 'Ubuntu');
    assert.strictEqual(osInfo.version, '22.04');
    assert.strictEqual(osInfo.architecture, 'x86_64');
    assert.strictEqual(osInfo.hostname, 'myserver');
  });

  it('should detect CentOS Linux', async () => {
    const mockSSH = createMockSSH({
      'uname -a': 'Linux centos-server 4.18.0-513.el8 x86_64 GNU/Linux',
      'os-release': 'NAME="CentOS Stream"\nVERSION_ID="8"\nID=centos',
      'uname -m': 'x86_64',
      'hostname': 'centos-server',
      'uptime': 'up 30 days',
      'proc/version': 'Linux version 4.18.0-513.el8',
      'lsb_release': '',
    });

    const detector = new OSDetector(mockSSH);
    const osInfo = await detector.detect();

    assert.strictEqual(osInfo.type, 'Linux');
    assert.strictEqual(osInfo.distribution, 'CentOS Stream');
    assert.strictEqual(osInfo.version, '8');
  });

  it('should handle unknown OS gracefully', async () => {
    const mockSSH = createMockSSH({
      'uname -a': 'SomeUnknownOS',
      'uname -m': 'arm64',
      'hostname': 'mystery',
      'uptime': 'up 1 day',
    });

    const detector = new OSDetector(mockSSH);
    const osInfo = await detector.detect();

    assert.strictEqual(osInfo.type, 'Unknown');
    assert.strictEqual(osInfo.architecture, 'arm64');
  });

  it('should handle SSH errors gracefully', async () => {
    const mockSSH = {
      execute: async () => { throw new Error('Connection lost'); },
    };

    const detector = new OSDetector(mockSSH);
    const osInfo = await detector.detect();

    // Should not throw, should return defaults
    assert.strictEqual(osInfo.type, 'Unknown');
    assert.strictEqual(osInfo.distribution, 'Unknown');
  });
});
