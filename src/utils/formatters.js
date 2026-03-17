'use strict';

const chalk = require('chalk');
const Table = require('cli-table3');

/**
 * Format OS information for display.
 */
function formatOSInfo(osInfo) {
  const table = new Table({
    head: [chalk.cyan('Property'), chalk.cyan('Value')],
    colWidths: [20, 55],
  });

  table.push(
    ['OS Type', osInfo.type],
    ['Distribution', osInfo.distribution],
    ['Version', osInfo.version],
    ['Kernel', osInfo.kernel],
    ['Architecture', osInfo.architecture],
    ['Hostname', osInfo.hostname],
    ['Uptime', osInfo.uptime]
  );

  return table.toString();
}

/**
 * Format application discovery results for display.
 */
function formatAppDiscovery(apps) {
  const sections = [];

  if (apps.webServers.length > 0) {
    const table = new Table({
      head: [chalk.green('Web Server'), chalk.green('Version')],
      colWidths: [25, 50],
    });
    for (const ws of apps.webServers) {
      table.push([ws.name, ws.version || 'N/A']);
    }
    sections.push(chalk.green.bold('\nWeb Servers:') + '\n' + table.toString());
  }

  if (apps.databases.length > 0) {
    const table = new Table({
      head: [chalk.blue('Database'), chalk.blue('Version')],
      colWidths: [25, 50],
    });
    for (const db of apps.databases) {
      table.push([db.name, db.version || 'N/A']);
    }
    sections.push(chalk.blue.bold('\nDatabases:') + '\n' + table.toString());
  }

  if (apps.controlPanels.length > 0) {
    const table = new Table({
      head: [chalk.magenta('Control Panel'), chalk.magenta('Version')],
      colWidths: [25, 50],
    });
    for (const cp of apps.controlPanels) {
      table.push([cp.name, cp.version || 'N/A']);
    }
    sections.push(chalk.magenta.bold('\nControl Panels:') + '\n' + table.toString());
  }

  if (apps.cms.length > 0) {
    const table = new Table({
      head: [chalk.yellow('CMS'), chalk.yellow('Version'), chalk.yellow('Path')],
      colWidths: [15, 15, 45],
    });
    for (const cm of apps.cms) {
      table.push([cm.name, cm.version || 'N/A', cm.path || 'N/A']);
    }
    sections.push(chalk.yellow.bold('\nCMS Applications:') + '\n' + table.toString());
  }

  if (apps.languages.length > 0) {
    const table = new Table({
      head: [chalk.white('Language'), chalk.white('Version')],
      colWidths: [25, 50],
    });
    for (const lang of apps.languages) {
      table.push([lang.name, lang.version || 'N/A']);
    }
    sections.push(chalk.white.bold('\nLanguages/Runtimes:') + '\n' + table.toString());
  }

  if (apps.containers.length > 0) {
    const table = new Table({
      head: [chalk.cyan('Container'), chalk.cyan('Image'), chalk.cyan('Status')],
      colWidths: [20, 30, 25],
    });
    for (const c of apps.containers) {
      table.push([c.name, c.image, c.status]);
    }
    sections.push(chalk.cyan.bold('\nContainers:') + '\n' + table.toString());
  }

  if (apps.services.length > 0) {
    const running = apps.services.filter((s) => s.status === 'running');
    sections.push(chalk.gray(`\nSystem Services: ${running.length} running out of ${apps.services.length} total`));
  }

  return sections.length > 0 ? sections.join('\n') : chalk.gray('No applications discovered.');
}

/**
 * Format diagnostic results for display.
 */
function formatDiagnostics(results) {
  const table = new Table({
    head: [
      chalk.white('Check'),
      chalk.white('Status'),
      chalk.white('Severity'),
      chalk.white('Fixable'),
    ],
    colWidths: [22, 10, 12, 10],
  });

  const severityColors = {
    ok: chalk.green,
    info: chalk.blue,
    warning: chalk.yellow,
    critical: chalk.red,
  };

  const statusIcons = {
    ok: chalk.green('PASS'),
    issue: chalk.red('FAIL'),
    skipped: chalk.gray('SKIP'),
    error: chalk.red('ERR'),
  };

  for (const result of results) {
    const colorFn = severityColors[result.severity] || chalk.white;
    table.push([
      result.name,
      statusIcons[result.status] || result.status,
      colorFn(result.severity || 'ok'),
      result.fix ? chalk.yellow('Yes') : chalk.gray('No'),
    ]);
  }

  return table.toString();
}

/**
 * Format rollback snapshot list for display.
 */
function formatSnapshots(snapshots) {
  if (snapshots.length === 0) return chalk.gray('No rollback snapshots available.');

  const table = new Table({
    head: [
      chalk.white('ID'),
      chalk.white('Time'),
      chalk.white('Command'),
      chalk.white('Status'),
    ],
    colWidths: [12, 22, 30, 12],
  });

  for (const snap of snapshots) {
    table.push([
      snap.id.substring(0, 8) + '...',
      snap.timestamp,
      snap.command.substring(0, 28),
      snap.status,
    ]);
  }

  return table.toString();
}

module.exports = {
  formatOSInfo,
  formatAppDiscovery,
  formatDiagnostics,
  formatSnapshots,
};
