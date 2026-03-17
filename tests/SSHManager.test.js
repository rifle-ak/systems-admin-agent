'use strict';

const { describe, it, beforeEach } = require('node:test');
const assert = require('node:assert');
const SSHManager = require('../src/connection/SSHManager');

describe('SSHManager', () => {
  it('should initialize with correct default options', () => {
    const ssh = new SSHManager({
      host: '192.168.1.1',
      username: 'root',
      password: 'secret',
    });

    assert.strictEqual(ssh.host, '192.168.1.1');
    assert.strictEqual(ssh.port, 22);
    assert.strictEqual(ssh.username, 'root');
    assert.strictEqual(ssh.password, 'secret');
    assert.strictEqual(ssh.privateKeyPath, null);
    assert.strictEqual(ssh.connected, false);
    assert.strictEqual(ssh.timeout, 15000);
  });

  it('should accept custom port and timeout', () => {
    const ssh = new SSHManager({
      host: 'myserver.com',
      port: 2222,
      username: 'admin',
      password: 'pass',
      timeout: 30000,
    });

    assert.strictEqual(ssh.port, 2222);
    assert.strictEqual(ssh.timeout, 30000);
  });

  it('should reject connect without credentials', async () => {
    const ssh = new SSHManager({
      host: '192.168.1.1',
      username: 'root',
    });

    await assert.rejects(
      () => ssh.connect(),
      { message: 'Either password or privateKeyPath must be provided' }
    );
  });

  it('should reject execute when not connected', async () => {
    const ssh = new SSHManager({
      host: '192.168.1.1',
      username: 'root',
      password: 'pass',
    });

    await assert.rejects(
      () => ssh.execute('ls'),
      { message: 'Not connected. Call connect() first.' }
    );
  });

  it('should reject file operations when not connected', async () => {
    const ssh = new SSHManager({
      host: '192.168.1.1',
      username: 'root',
      password: 'pass',
    });

    await assert.rejects(
      () => ssh.uploadFile('/tmp/a', '/tmp/b'),
      { message: 'Not connected. Call connect() first.' }
    );

    await assert.rejects(
      () => ssh.downloadFile('/tmp/a', '/tmp/b'),
      { message: 'Not connected. Call connect() first.' }
    );
  });

  it('should handle disconnect gracefully when not connected', () => {
    const ssh = new SSHManager({
      host: '192.168.1.1',
      username: 'root',
      password: 'pass',
    });

    // Should not throw
    ssh.disconnect();
    assert.strictEqual(ssh.connected, false);
    assert.strictEqual(ssh.client, null);
  });

  it('should reject connect with invalid key path', async () => {
    const ssh = new SSHManager({
      host: '192.168.1.1',
      username: 'root',
      privateKeyPath: '/nonexistent/key',
    });

    await assert.rejects(
      () => ssh.connect(),
      (err) => {
        assert.ok(err.message.includes('Failed to read private key'));
        return true;
      }
    );
  });
});
