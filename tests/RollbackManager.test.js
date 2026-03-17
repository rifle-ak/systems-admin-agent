'use strict';

const { describe, it, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const RollbackManager = require('../src/rollback/RollbackManager');

function createMockSSH() {
  return {
    execute: async (cmd) => {
      if (cmd.includes('test -f')) return { stdout: '', stderr: '', code: 1 };
      if (cmd.includes('systemctl')) return { stdout: 'nginx.service loaded active running', stderr: '', code: 0 };
      if (cmd.includes('dpkg')) return { stdout: 'package-list', stderr: '', code: 0 };
      return { stdout: '', stderr: '', code: 0 };
    },
    executeSudo: async () => ({ stdout: '', stderr: '', code: 0 }),
    downloadFile: async () => {},
    uploadFile: async () => {},
  };
}

describe('RollbackManager', () => {
  const testSnapshotDir = path.join(process.cwd(), '.test-snapshots-' + Date.now());

  afterEach(() => {
    // Cleanup test snapshot directory
    if (fs.existsSync(testSnapshotDir)) {
      fs.rmSync(testSnapshotDir, { recursive: true, force: true });
    }
  });

  it('should create a snapshot', async () => {
    const ssh = createMockSSH();
    const manager = new RollbackManager(ssh, { snapshotDir: testSnapshotDir });

    const snapshotId = await manager.createSnapshot('echo test > /etc/resolv.conf', 'Test snapshot');
    assert.ok(snapshotId);
    assert.strictEqual(typeof snapshotId, 'string');

    const snapshot = manager.getSnapshot(snapshotId);
    assert.ok(snapshot);
    assert.strictEqual(snapshot.command, 'echo test > /etc/resolv.conf');
    assert.strictEqual(snapshot.status, 'ready');
  });

  it('should list snapshots', async () => {
    const ssh = createMockSSH();
    const manager = new RollbackManager(ssh, { snapshotDir: testSnapshotDir });

    await manager.createSnapshot('cmd1', 'First');
    await manager.createSnapshot('cmd2', 'Second');

    const list = manager.listSnapshots();
    assert.strictEqual(list.length, 2);
    assert.strictEqual(list[0].description, 'First');
    assert.strictEqual(list[1].description, 'Second');
  });

  it('should remove a snapshot', async () => {
    const ssh = createMockSSH();
    const manager = new RollbackManager(ssh, { snapshotDir: testSnapshotDir });

    const id = await manager.createSnapshot('cmd1', 'To remove');
    assert.strictEqual(manager.listSnapshots().length, 1);

    await manager.removeSnapshot(id);
    assert.strictEqual(manager.listSnapshots().length, 0);
  });

  it('should throw when rolling back nonexistent snapshot', async () => {
    const ssh = createMockSSH();
    const manager = new RollbackManager(ssh, { snapshotDir: testSnapshotDir });

    await assert.rejects(
      () => manager.rollback('nonexistent-id'),
      { message: 'Snapshot nonexistent-id not found' }
    );
  });

  it('should identify affected files from commands', async () => {
    const ssh = createMockSSH();
    const manager = new RollbackManager(ssh, { snapshotDir: testSnapshotDir });

    // Test internal file identification
    const files = manager._identifyAffectedFiles('echo "nameserver 8.8.8.8" > /etc/resolv.conf');
    assert.ok(files.includes('/etc/resolv.conf'));
  });

  it('should persist snapshots to disk', async () => {
    const ssh = createMockSSH();
    const manager1 = new RollbackManager(ssh, { snapshotDir: testSnapshotDir });
    await manager1.createSnapshot('cmd1', 'Persistent');

    // Create new manager instance - should load from disk
    const manager2 = new RollbackManager(ssh, { snapshotDir: testSnapshotDir });
    const list = manager2.listSnapshots();
    assert.strictEqual(list.length, 1);
    assert.strictEqual(list[0].description, 'Persistent');
  });
});
