# Systems Admin Agent

SSH-based systems administration agent with automatic OS detection, application discovery, server diagnostics, and safe rollback capabilities.

## Features

- **SSH Connection Manager** — Connect to any server via password or SSH key authentication
- **OS Detection** — Automatically identify the operating system, distribution, version, kernel, and architecture
- **Application Discovery** — Detect web servers, databases, control panels (cPanel, Plesk, etc.), CMS platforms (WordPress, Joomla, Drupal), programming languages, and container runtimes
- **Health Diagnostics** — 14 automated health checks including disk usage, memory, CPU load, failed services, DNS, NTP, firewall, SSL certificates, security updates, and more
- **Auto-Fix with Approval** — Automatically propose and apply fixes for detected issues; destructive actions require explicit human approval
- **Rollback System** — Every destructive action creates a snapshot before execution, allowing full rollback if something goes wrong

## Installation

```bash
npm install
```

## Usage

### Full Server Scan (OS + Apps + Diagnostics)

```bash
# Using password authentication
npx sysadmin-agent scan -H 192.168.1.100 -u root -p 'yourpassword'

# Using SSH key
npx sysadmin-agent scan -H myserver.com -u admin -k ~/.ssh/id_rsa
```

### Detect Operating System

```bash
npx sysadmin-agent os -H 192.168.1.100 -u root -k ~/.ssh/id_rsa
```

### Discover Applications

```bash
npx sysadmin-agent apps -H 192.168.1.100 -u root -p 'password'
```

### Run Diagnostics

```bash
npx sysadmin-agent diagnose -H 192.168.1.100 -u root -k ~/.ssh/id_rsa
```

### Auto-Fix Issues (with approval prompts)

```bash
npx sysadmin-agent fix -H 192.168.1.100 -u root -p 'password'
```

### Rollback a Previous Action

```bash
npx sysadmin-agent rollback -H 192.168.1.100 -u root -k ~/.ssh/id_rsa
```

### Execute a Remote Command

```bash
npx sysadmin-agent exec -H 192.168.1.100 -u root -k ~/.ssh/id_rsa -- ls -la /var/log
npx sysadmin-agent exec -H 192.168.1.100 -u root -p 'pass' --sudo -- systemctl status nginx
```

## Connection Options

| Option | Description |
|---|---|
| `-H, --host <host>` | Server hostname or IP address (required) |
| `-u, --user <username>` | SSH username (required) |
| `-p, --password <password>` | SSH password |
| `-k, --key <path>` | Path to SSH private key file |
| `--passphrase <passphrase>` | Passphrase for encrypted private key |
| `--port <port>` | SSH port (default: 22) |

## Diagnostics Checks

| Check | Description |
|---|---|
| Disk Usage | Alerts when partitions exceed 90% |
| Memory Usage | Flags high memory consumption |
| CPU Load | Monitors load average per CPU |
| Zombie Processes | Detects orphaned zombie processes |
| Failed Services | Lists systemd services in failed state |
| DNS Resolution | Verifies DNS is working |
| NTP Sync | Checks time synchronization |
| Open Ports | Lists all listening ports |
| Disk I/O Wait | Monitors disk I/O bottlenecks |
| Swap Usage | Checks swap space pressure |
| OOM Kills | Detects recent out-of-memory events |
| SSL Certificates | Checks certificate expiry (certbot) |
| Security Updates | Counts pending security patches |
| Firewall | Verifies firewall is active |

## Safety Design

1. **Non-destructive by default** — All read-only commands run without prompts
2. **Human approval required** — Destructive actions display details and require explicit "Yes" confirmation
3. **Automatic snapshots** — Before any destructive action, affected files and service states are backed up
4. **Rollback on demand** — Any snapshot can be restored to revert changes
5. **Deny-by-default in non-interactive mode** — If running without a terminal, destructive actions are denied

## Testing

```bash
npm test
```

## Architecture

```
src/
  connection/SSHManager.js      — SSH connection, command execution, SFTP
  discovery/OSDetector.js       — OS type, distro, version detection
  discovery/AppDiscovery.js     — Application and service discovery
  diagnostics/DiagnosticEngine.js — Health checks with fix suggestions
  rollback/RollbackManager.js   — Snapshot creation and restoration
  approval/ApprovalManager.js   — Human approval workflow
  utils/formatters.js           — CLI output formatting
  index.js                      — CLI entry point (Commander.js)
tests/
  SSHManager.test.js
  OSDetector.test.js
  ApprovalManager.test.js
  RollbackManager.test.js
```

## License

MIT
