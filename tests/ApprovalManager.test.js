'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert');
const ApprovalManager = require('../src/approval/ApprovalManager');

describe('ApprovalManager', () => {
  it('should auto-approve when autoApprove is true', async () => {
    const manager = new ApprovalManager({ autoApprove: true });

    const approved = await manager.requestApproval({
      action: 'rm -rf /tmp/old',
      description: 'Remove old temp files',
      snapshotId: 'test-123',
      destructive: true,
    });

    assert.strictEqual(approved, true);
    assert.strictEqual(manager.getHistory().length, 1);
    assert.strictEqual(manager.getHistory()[0].decision, 'auto_approved');
  });

  it('should use custom prompt function', async () => {
    const manager = new ApprovalManager({
      promptFn: async () => true,
    });

    const approved = await manager.requestApproval({
      action: 'systemctl restart nginx',
      description: 'Restart nginx',
      destructive: true,
    });

    assert.strictEqual(approved, true);
  });

  it('should deny when custom prompt returns false', async () => {
    const manager = new ApprovalManager({
      promptFn: async () => false,
    });

    const approved = await manager.requestApproval({
      action: 'systemctl restart nginx',
      description: 'Restart nginx',
      destructive: true,
    });

    assert.strictEqual(approved, false);
    assert.strictEqual(manager.getHistory()[0].decision, 'denied');
  });

  it('should track approval statistics correctly', async () => {
    const manager = new ApprovalManager({ autoApprove: true });

    await manager.requestApproval({ action: 'cmd1', destructive: true });
    await manager.requestApproval({ action: 'cmd2', destructive: true });

    const stats = manager.getStats();
    assert.strictEqual(stats.total, 2);
    assert.strictEqual(stats.approved, 2);
    assert.strictEqual(stats.denied, 0);
  });

  it('should emit events for approval requests', async () => {
    const manager = new ApprovalManager({ autoApprove: true });

    let eventFired = false;
    manager.on('approval_requested', () => { eventFired = true; });

    await manager.requestApproval({ action: 'test', destructive: true });
    assert.strictEqual(eventFired, true);
  });

  it('should handle prompt errors gracefully', async () => {
    const manager = new ApprovalManager({
      promptFn: async () => { throw new Error('Terminal unavailable'); },
    });

    const approved = await manager.requestApproval({
      action: 'rm -rf /tmp',
      destructive: true,
    });

    assert.strictEqual(approved, false);
    assert.strictEqual(manager.getHistory()[0].decision, 'error');
  });
});
