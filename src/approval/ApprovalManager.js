'use strict';

const { EventEmitter } = require('events');

/**
 * Manages human approval workflow for destructive actions.
 * Presents the action to the user and waits for explicit approval.
 */
class ApprovalManager extends EventEmitter {
  constructor(options = {}) {
    super();
    this.autoApprove = options.autoApprove || false;
    this.approvalHistory = [];
    this.promptFn = options.promptFn || null; // injectable prompt function for testing
  }

  /**
   * Request approval from the user for a destructive action.
   * @param {object} action - The action details.
   * @param {string} action.action - The command to be executed.
   * @param {string} action.description - Human-readable description.
   * @param {string} action.snapshotId - ID of the rollback snapshot.
   * @param {boolean} action.destructive - Whether this is destructive.
   * @returns {Promise<boolean>} True if approved.
   */
  async requestApproval(action) {
    const record = {
      timestamp: new Date().toISOString(),
      action: action.action,
      description: action.description,
      snapshotId: action.snapshotId,
      destructive: action.destructive,
      decision: null,
    };

    this.emit('approval_requested', action);

    if (this.autoApprove) {
      record.decision = 'auto_approved';
      this.approvalHistory.push(record);
      return true;
    }

    try {
      const approved = await this._promptUser(action);
      record.decision = approved ? 'approved' : 'denied';
      this.approvalHistory.push(record);
      this.emit('approval_decided', { ...action, approved });
      return approved;
    } catch (err) {
      record.decision = 'error';
      record.error = err.message;
      this.approvalHistory.push(record);
      return false;
    }
  }

  /**
   * Prompt the user for approval via the CLI.
   */
  async _promptUser(action) {
    if (this.promptFn) {
      return this.promptFn(action);
    }

    // Dynamic import for inquirer (supports both interactive and non-interactive)
    try {
      const inquirer = require('inquirer');
      const chalk = require('chalk');

      console.log('\n' + chalk.yellow('━'.repeat(60)));
      console.log(chalk.yellow.bold('⚠  DESTRUCTIVE ACTION REQUIRES APPROVAL'));
      console.log(chalk.yellow('━'.repeat(60)));
      console.log(chalk.white.bold('Command: ') + chalk.red(action.action));
      if (action.description) {
        console.log(chalk.white.bold('Details: ') + chalk.white(action.description));
      }
      if (action.snapshotId) {
        console.log(chalk.white.bold('Rollback ID: ') + chalk.cyan(action.snapshotId));
      }
      console.log(chalk.gray('A rollback snapshot has been created. You can revert this change if needed.'));
      console.log(chalk.yellow('━'.repeat(60)));

      const { approved } = await inquirer.prompt([
        {
          type: 'confirm',
          name: 'approved',
          message: 'Do you approve this action?',
          default: false,
        },
      ]);

      return approved;
    } catch {
      // Non-interactive fallback: deny by default for safety
      console.error('Non-interactive mode: destructive action denied by default');
      return false;
    }
  }

  /**
   * Get the full approval history.
   */
  getHistory() {
    return [...this.approvalHistory];
  }

  /**
   * Get summary statistics of approvals.
   */
  getStats() {
    const total = this.approvalHistory.length;
    const approved = this.approvalHistory.filter((r) => r.decision === 'approved' || r.decision === 'auto_approved').length;
    const denied = this.approvalHistory.filter((r) => r.decision === 'denied').length;
    const errors = this.approvalHistory.filter((r) => r.decision === 'error').length;

    return { total, approved, denied, errors };
  }
}

module.exports = ApprovalManager;
