# Systems Admin Agent

AI-powered systems administration agent. Connect to any server via SSH, diagnose issues, and fix them — using plain English.

Built with Python. Uses Claude as the AI brain (token-conscious — only calls the API when human judgment is needed).

## Features

- **Plain English Interface** — Ask things like "fix the slow page load on mysite.com" or "check why email isn't working"
- **SSH Connection Manager** — Connect via password or SSH key
- **OS Detection** — Identify distribution, version, kernel, architecture
- **Application Discovery** — Detect web servers, databases, control panels (cPanel, Plesk), CMS (WordPress, Joomla, Drupal), languages, Docker containers
- **14 Health Checks** — Disk, memory, CPU, failed services, DNS, NTP, firewall, SSL certs, security updates, and more
- **Auto-Fix with Approval** — Propose and apply fixes; destructive actions require explicit human approval
- **Rollback System** — Every destructive action creates a snapshot first, allowing full rollback
- **Built-in Knowledge Base** — Local documentation for WordPress, Elementor, cPanel, Apache, Nginx, MySQL, PHP-FPM, Redis, Docker, Pterodactyl, Saltbox, and more — saves tokens by providing context without API calls

## Installation

```bash
pip install -e .
```

Or install dependencies directly:

```bash
pip install paramiko anthropic rich click
```

## Quick Start

### Interactive Mode (Recommended)

Connect to a server and chat with the AI agent:

```bash
sysadmin-agent --host myserver.com --username root --key ~/.ssh/id_rsa interactive
```

Then type requests in plain English:
```
> check disk usage and suggest cleanup
> why is WordPress slow on this server
> show me the top memory consumers
> !uptime              # prefix with ! to run raw commands
> exit
```

### Ask a Single Question

```bash
sysadmin-agent -h myserver.com -u root -k ~/.ssh/id_rsa ask "optimize page speed for WordPress"
```

### Full Server Scan

```bash
# Using SSH key
sysadmin-agent -h myserver.com -u root -k ~/.ssh/id_rsa scan

# Using password
sysadmin-agent -h 192.168.1.100 -u root -P 'yourpassword' scan
```

### Other Commands

```bash
# OS detection only
sysadmin-agent -h myserver.com -u root -k ~/.ssh/id_rsa os

# Discover installed applications
sysadmin-agent -h myserver.com -u root -k ~/.ssh/id_rsa apps

# Run health checks
sysadmin-agent -h myserver.com -u root -k ~/.ssh/id_rsa diagnose

# Auto-fix issues (with approval prompts)
sysadmin-agent -h myserver.com -u root -k ~/.ssh/id_rsa fix

# Rollback a previous action
sysadmin-agent -h myserver.com -u root -k ~/.ssh/id_rsa rollback

# Execute a remote command
sysadmin-agent -h myserver.com -u root -k ~/.ssh/id_rsa exec "ls -la /var/log"
```

## Configuration

Set your Anthropic API key (required for `ask` and `interactive` commands):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or pass it directly:

```bash
sysadmin-agent -h myserver.com -u root --api-key sk-ant-... interactive
```

## Connection Options

| Option | Description |
|---|---|
| `-h, --host` | Server hostname or IP (required) |
| `-p, --port` | SSH port (default: 22) |
| `-u, --username` | SSH username (required) |
| `-P, --password` | SSH password |
| `-k, --key` | Path to SSH private key |
| `--passphrase` | Passphrase for encrypted key |
| `--api-key` | Anthropic API key (or set `ANTHROPIC_API_KEY`) |
| `--auto-approve` | Skip approval prompts (use with caution) |

## Diagnostics Checks

| Check | Threshold | Fixable |
|---|---|---|
| Disk Usage | >= 90% per mount | Yes |
| Memory Usage | >= 85% | Yes |
| CPU Load | load/cpu ratio >= 1.0 | No |
| Zombie Processes | Any detected | Yes |
| Failed Services | Any in failed state | Yes |
| DNS Resolution | google.com lookup fails | Yes |
| NTP Sync | Time not synced | Yes |
| Open Ports | Info only | No |
| Disk I/O Wait | >= 10% | No |
| Swap Usage | >= 80% | No |
| OOM Kills | Any in logs | No |
| SSL Certificates | Expiry check | No |
| Security Updates | Pending count | No |
| Firewall | Status check | No |

## Safety Design

1. **Non-destructive by default** — Read-only commands run without prompts
2. **Human approval required** — Destructive actions show details and require "Yes"
3. **Automatic snapshots** — Affected files/services backed up before changes
4. **Rollback on demand** — Any snapshot can be restored
5. **Deny-by-default** — Non-interactive mode denies destructive actions
6. **Never guesses** — Asks clarifying questions when uncertain

## Testing

```bash
pytest tests/ -v
```

## Architecture

```
sysadmin_agent/
  connection/ssh_manager.py      — SSH connection, command execution, SFTP
  discovery/os_detector.py       — OS type, distro, version detection
  discovery/app_discovery.py     — Application and service discovery
  diagnostics/diagnostic_engine.py — 14 health checks with fix suggestions
  rollback/rollback_manager.py   — Snapshot creation and restoration
  approval/approval_manager.py   — Human approval workflow
  ai/brain.py                    — Claude API integration (token-conscious)
  knowledge/doc_fetcher.py       — Built-in software documentation
  utils/formatters.py            — Rich terminal output formatting
  cli.py                         — Click-based CLI entry point
```

## Token Efficiency

The agent minimizes API costs by:
- Running direct commands (OS detection, app discovery, diagnostics) without AI
- Using a local knowledge base for known software docs
- Only calling Claude when plain English interpretation or complex analysis is needed
- Tracking and displaying token usage after every session

## License

MIT
