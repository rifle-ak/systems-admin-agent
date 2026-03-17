# Systems Admin Agent

AI-powered systems administration agent. Connect to any server via SSH, diagnose issues, and fix them — using plain English.

Built with Python. Uses Claude as the AI brain (token-conscious — only calls the API when human judgment is needed).

## Features

- **Web UI** — Browser-based interface with self-installing setup wizard. No command line needed.
- **Plain English Interface** — Ask things like "fix the slow page load on mysite.com" or "check why email isn't working"
- **SSH Connection Manager** — Connect via password or SSH key
- **OS Detection** — Identify distribution, version, kernel, architecture
- **Application Discovery** — Detect web servers, databases, control panels (cPanel, Plesk), CMS (WordPress, Joomla, Drupal), languages, Docker containers
- **14 System Health Checks** — Disk, memory, CPU, failed services, DNS, NTP, firewall, SSL certs, security updates, and more
- **15 WordPress/Elementor Checks** — PHP-FPM tuning, OPcache, object cache, database optimization, plugin audit, file permissions, page speed factors, Elementor health, security basics
- **Auto-Fix with Approval** — Propose and apply fixes; destructive actions require explicit human approval
- **Rollback System** — Every destructive action creates a snapshot first, allowing full rollback
- **Server Profiles** — Save connection details so you don't retype credentials every time
- **Conversation Memory** — AI remembers what it checked/fixed during a session
- **Auto-Updater** — Pull latest version without destroying your configs
- **Built-in Knowledge Base** — Local documentation for WordPress, Elementor, cPanel, Apache, Nginx, MySQL, PHP-FPM, Redis, Docker, Pterodactyl, Saltbox, and more — saves tokens by providing context without API calls

## Quick Start — Web UI

The easiest way to get started. Upload the files to your server, then:

```bash
# 1. Install Python dependencies
pip install --user paramiko anthropic rich click flask flask-socketio python-dotenv

# 2. Start the web UI
python -m sysadmin_agent web --web-port 5000
```

Open `http://your-server:5000` in a browser. The setup wizard will:
1. Check for missing dependencies and offer to install them
2. Ask for your Anthropic API key
3. Redirect you to the dashboard

From the dashboard, connect to any server and start chatting in plain English.

## Quick Start — CLI

```bash
# Set your API key
export ANTHROPIC_API_KEY="sk-ant-..."

# Interactive mode (connect once, ask multiple questions)
sysadmin-agent -h myserver.com -u root -k ~/.ssh/id_rsa interactive

# Ask a single question
sysadmin-agent -h myserver.com -u root -k ~/.ssh/id_rsa ask "optimize page speed for WordPress"

# Full server scan
sysadmin-agent -h myserver.com -u root -k ~/.ssh/id_rsa scan
```

Interactive mode commands:
```
> check disk usage and suggest cleanup
> why is WordPress slow on this server
> show me the top memory consumers
> !uptime              # prefix with ! to run raw commands
> exit
```

## All CLI Commands

```bash
sysadmin-agent [SSH OPTIONS] <command>

# Server analysis
scan          # Full scan: OS + apps + diagnostics
os            # OS detection only
apps          # Discover installed applications
diagnose      # Run health checks

# AI-powered
ask "..."     # Ask in plain English (one-shot)
interactive   # Interactive REPL mode

# Actions
fix           # Run diagnostics and apply fixes (with approval)
rollback      # List/restore snapshots
exec "cmd"    # Execute a remote command

# Web
web           # Launch web UI (--web-port, --web-host, --debug)
```

## SSH Options

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

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Key settings:
- `ANTHROPIC_API_KEY` — Required for AI features
- `WEB_PASSWORD` — Optional password to protect the web UI
- `AI_MODEL` — AI model to use (default: claude-sonnet-4-20250514)

## System Health Checks (14)

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

## WordPress/Elementor Checks (15)

| Check | What it does |
|---|---|
| WP Version | Compare installed vs latest, warn if outdated |
| Debug Mode | Warn if WP_DEBUG is on in production |
| Plugin Updates | List plugins needing updates |
| Theme Updates | List themes needing updates |
| Inactive Plugins | Find inactive plugins (security risk + bloat) |
| PHP-FPM Config | Analyze pm.max_children, memory_limit, max_execution_time |
| OPcache | Check if enabled, memory, accelerated files |
| Object Cache | Check for Redis/Memcached, recommend if missing |
| WP-Cron | Check if using system cron vs WP-Cron |
| Database | Autoloaded options size, post revisions, table optimization |
| File Permissions | Find 777 files, check wp-config.php permissions |
| SSL/HTTPS | Verify site URL uses HTTPS, check redirects |
| Page Speed | Plugin count, caching plugin, GZIP/Brotli, Elementor CSS |
| Security | xmlrpc.php, directory listing, file editing, login protection |
| Elementor Health | Version, CSS output, asset loading, DOM optimization |

## Safety Design

1. **Non-destructive by default** — Read-only commands run without prompts
2. **Human approval required** — Destructive actions show details and require "Yes"
3. **Automatic snapshots** — Affected files/services backed up before changes
4. **Rollback on demand** — Any snapshot can be restored
5. **Deny-by-default** — Non-interactive mode denies destructive actions
6. **Never guesses** — Asks clarifying questions when uncertain
7. **Passwords never stored** — Server profiles store a "password required" flag, not the actual password

## Auto-Updater

Pull the latest version while preserving your configs:

```bash
python -m sysadmin_agent.updater
```

The updater:
- Backs up `.env` and `config.json` before updating
- Preserves your existing config values
- Adds new config keys with their defaults (marked with `# NEW in update`)
- Comments out removed keys (marked with `# REMOVED in update`)
- Updates Python dependencies automatically
- Can rollback if something goes wrong

## Architecture

```
sysadmin_agent/
  connection/ssh_manager.py        — SSH connection, command execution, SFTP
  discovery/os_detector.py         — OS type, distro, version detection
  discovery/app_discovery.py       — Application and service discovery
  diagnostics/diagnostic_engine.py — 14 system health checks
  diagnostics/wordpress_checks.py  — 15 WordPress/Elementor checks
  rollback/rollback_manager.py     — Snapshot creation and restoration
  approval/approval_manager.py     — Human approval workflow
  ai/brain.py                      — Claude API integration (token-conscious)
  knowledge/doc_fetcher.py         — Built-in software documentation
  profiles/profile_manager.py      — Saved server connection profiles
  memory/conversation_memory.py    — Persistent conversation history
  updater/updater.py               — Auto-update with config preservation
  web/app.py                       — Flask + SocketIO web application
  web/templates/                   — HTML templates (setup, dashboard, login)
  web/static/                      — CSS and JavaScript
  utils/formatters.py              — Rich terminal output formatting
  cli.py                           — Click-based CLI entry point
```

## Token Efficiency

The agent minimizes API costs by:
- Running direct commands (OS detection, app discovery, diagnostics) without AI
- Using a local knowledge base for known software docs
- Condensing conversation history into summaries instead of sending full transcripts
- Only calling Claude when plain English interpretation or complex analysis is needed
- Tracking and displaying token usage after every session

## Testing

```bash
pytest tests/ -v
```

## License

MIT
