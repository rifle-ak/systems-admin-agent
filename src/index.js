#!/usr/bin/env node
'use strict';

const { Command } = require('commander');
const chalk = require('chalk');
const ora = require('ora');
const inquirer = require('inquirer');

const SSHManager = require('./connection/SSHManager');
const OSDetector = require('./discovery/OSDetector');
const AppDiscovery = require('./discovery/AppDiscovery');
const DiagnosticEngine = require('./diagnostics/DiagnosticEngine');
const RollbackManager = require('./rollback/RollbackManager');
const ApprovalManager = require('./approval/ApprovalManager');
const { formatOSInfo, formatAppDiscovery, formatDiagnostics, formatSnapshots } = require('./utils/formatters');

const program = new Command();

program
  .name('sysadmin-agent')
  .description('SSH-based systems administration agent with auto-diagnosis and safe rollback')
  .version('1.0.0');

/**
 * Shared options for SSH connection.
 */
function addSSHOptions(cmd) {
  return cmd
    .requiredOption('-H, --host <host>', 'Server hostname or IP address')
    .requiredOption('-u, --user <username>', 'SSH username')
    .option('-p, --password <password>', 'SSH password')
    .option('-k, --key <path>', 'Path to SSH private key')
    .option('--passphrase <passphrase>', 'Passphrase for the private key')
    .option('--port <port>', 'SSH port', '22');
}

/**
 * Create an SSH connection from CLI options.
 */
async function createConnection(opts) {
  const ssh = new SSHManager({
    host: opts.host,
    port: parseInt(opts.port, 10),
    username: opts.user,
    password: opts.password,
    privateKeyPath: opts.key,
    passphrase: opts.passphrase,
  });

  const spinner = ora(`Connecting to ${opts.host}...`).start();
  try {
    await ssh.connect();
    spinner.succeed(`Connected to ${opts.host}`);
    return ssh;
  } catch (err) {
    spinner.fail(`Connection failed: ${err.message}`);
    process.exit(1);
  }
}

// ─── SCAN command: full server scan (OS + apps + diagnostics) ───

addSSHOptions(
  program
    .command('scan')
    .description('Full server scan: detect OS, discover apps, run diagnostics')
).action(async (opts) => {
  const ssh = await createConnection(opts);
  try {
    // OS Detection
    const osSpinner = ora('Detecting operating system...').start();
    const osDetector = new OSDetector(ssh);
    const osInfo = await osDetector.detect();
    osSpinner.succeed('OS detected');
    console.log(chalk.bold('\n── Operating System ──'));
    console.log(formatOSInfo(osInfo));

    // App Discovery
    const appSpinner = ora('Discovering applications...').start();
    const appDiscovery = new AppDiscovery(ssh);
    const apps = await appDiscovery.discover();
    appSpinner.succeed('Application discovery complete');
    console.log(chalk.bold('\n── Installed Applications ──'));
    console.log(formatAppDiscovery(apps));

    // Diagnostics
    const diagSpinner = ora('Running diagnostics...').start();
    const approval = new ApprovalManager();
    const rollback = new RollbackManager(ssh);
    const diagnostics = new DiagnosticEngine(ssh, approval, rollback);
    const results = await diagnostics.runAll();
    diagSpinner.succeed('Diagnostics complete');
    console.log(chalk.bold('\n── Health Check Results ──'));
    console.log(formatDiagnostics(results));

    // Show fixable issues
    const fixable = results.filter((r) => r.fix && r.status === 'issue');
    if (fixable.length > 0) {
      console.log(chalk.yellow(`\n${fixable.length} issue(s) have automated fixes available.`));
      console.log(chalk.gray('Run "sysadmin-agent fix" to apply fixes with approval workflow.\n'));
    }
  } finally {
    ssh.disconnect();
  }
});

// ─── OS command: detect OS only ───

addSSHOptions(
  program
    .command('os')
    .description('Detect the remote server operating system')
).action(async (opts) => {
  const ssh = await createConnection(opts);
  try {
    const spinner = ora('Detecting operating system...').start();
    const osDetector = new OSDetector(ssh);
    const osInfo = await osDetector.detect();
    spinner.succeed('OS detected');
    console.log(formatOSInfo(osInfo));
  } finally {
    ssh.disconnect();
  }
});

// ─── APPS command: discover applications ───

addSSHOptions(
  program
    .command('apps')
    .description('Discover applications and services on the server')
).action(async (opts) => {
  const ssh = await createConnection(opts);
  try {
    const spinner = ora('Discovering applications...').start();
    const appDiscovery = new AppDiscovery(ssh);
    const apps = await appDiscovery.discover();
    spinner.succeed('Discovery complete');
    console.log(formatAppDiscovery(apps));
  } finally {
    ssh.disconnect();
  }
});

// ─── DIAGNOSE command: run health checks ───

addSSHOptions(
  program
    .command('diagnose')
    .description('Run health diagnostics on the server')
).action(async (opts) => {
  const ssh = await createConnection(opts);
  try {
    const spinner = ora('Running diagnostics...').start();
    const approval = new ApprovalManager();
    const rollback = new RollbackManager(ssh);
    const diagnostics = new DiagnosticEngine(ssh, approval, rollback);
    const results = await diagnostics.runAll();
    spinner.succeed('Diagnostics complete');
    console.log(formatDiagnostics(results));

    const fixable = results.filter((r) => r.fix && r.status === 'issue');
    if (fixable.length > 0) {
      console.log(chalk.yellow(`\n${fixable.length} issue(s) have automated fixes available.`));
    }
  } finally {
    ssh.disconnect();
  }
});

// ─── FIX command: auto-fix issues with approval ───

addSSHOptions(
  program
    .command('fix')
    .description('Diagnose and fix issues (destructive actions require approval)')
    .option('--auto-approve', 'Auto-approve all destructive actions (use with caution)')
).action(async (opts) => {
  const ssh = await createConnection(opts);
  try {
    const spinner = ora('Running diagnostics...').start();
    const approval = new ApprovalManager({ autoApprove: opts.autoApprove });
    const rollback = new RollbackManager(ssh);
    const diagnostics = new DiagnosticEngine(ssh, approval, rollback);
    const results = await diagnostics.runAll();
    spinner.succeed('Diagnostics complete');
    console.log(formatDiagnostics(results));

    const fixable = results.filter((r) => r.fix && r.status === 'issue');
    if (fixable.length === 0) {
      console.log(chalk.green('\nNo issues to fix. Server is healthy!'));
      return;
    }

    console.log(chalk.yellow(`\nFound ${fixable.length} fixable issue(s):\n`));

    for (const issue of fixable) {
      console.log(chalk.bold(`\n── Fixing: ${issue.name} ──`));
      console.log(chalk.gray(`Fix: ${issue.fix.description}`));

      for (const action of issue.fix.actions) {
        console.log(chalk.white(`  Command: ${action.command}`));
        console.log(chalk.gray(`  Destructive: ${action.destructive ? 'Yes' : 'No'}`));

        try {
          const result = await diagnostics.applyFix(action);
          if (result.applied) {
            console.log(chalk.green('  Result: Applied successfully'));
            if (result.result && result.result.stdout) {
              console.log(chalk.gray(`  Output: ${result.result.stdout.substring(0, 200)}`));
            }
          } else {
            console.log(chalk.yellow(`  Result: Skipped - ${result.reason}`));
          }
        } catch (err) {
          console.log(chalk.red(`  Error: ${err.message}`));
        }
      }
    }

    // Show approval stats
    const stats = approval.getStats();
    console.log(chalk.bold('\n── Fix Summary ──'));
    console.log(`Total actions: ${stats.total}, Approved: ${stats.approved}, Denied: ${stats.denied}`);
  } finally {
    ssh.disconnect();
  }
});

// ─── ROLLBACK command: revert a previous action ───

addSSHOptions(
  program
    .command('rollback [snapshotId]')
    .description('Rollback a previous destructive action using its snapshot ID')
).action(async (snapshotId, opts) => {
  const ssh = await createConnection(opts);
  try {
    const rollback = new RollbackManager(ssh);
    const snapshots = rollback.listSnapshots();

    if (snapshots.length === 0) {
      console.log(chalk.gray('No rollback snapshots available.'));
      return;
    }

    if (!snapshotId) {
      console.log(chalk.bold('\n── Available Rollback Snapshots ──'));
      console.log(formatSnapshots(snapshots));

      const { selected } = await inquirer.prompt([
        {
          type: 'list',
          name: 'selected',
          message: 'Select a snapshot to rollback:',
          choices: snapshots.map((s) => ({
            name: `${s.id.substring(0, 8)}... | ${s.timestamp} | ${s.command.substring(0, 40)}`,
            value: s.id,
          })),
        },
      ]);
      snapshotId = selected;
    }

    const { confirm } = await inquirer.prompt([
      {
        type: 'confirm',
        name: 'confirm',
        message: `Are you sure you want to rollback snapshot ${snapshotId.substring(0, 8)}...?`,
        default: false,
      },
    ]);

    if (!confirm) {
      console.log(chalk.yellow('Rollback cancelled.'));
      return;
    }

    const spinner = ora('Rolling back...').start();
    const result = await rollback.rollback(snapshotId);
    spinner.succeed('Rollback complete');

    for (const r of result.results) {
      const status = r.status === 'restored' ? chalk.green('Restored') : chalk.red(r.status);
      console.log(`  ${r.file || r.service}: ${status}`);
    }
  } finally {
    ssh.disconnect();
  }
});

// ─── EXEC command: execute an arbitrary command ───

addSSHOptions(
  program
    .command('exec <command...>')
    .description('Execute a command on the remote server')
    .option('--sudo', 'Run with sudo')
).action(async (commandParts, opts) => {
  const ssh = await createConnection(opts);
  try {
    const command = commandParts.join(' ');
    const result = opts.sudo
      ? await ssh.executeSudo(command)
      : await ssh.execute(command);

    if (result.stdout) console.log(result.stdout);
    if (result.stderr) console.error(chalk.red(result.stderr));
    process.exit(result.code);
  } finally {
    ssh.disconnect();
  }
});

program.parse();
