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

---

## Installation

Choose your path — **Web UI** (recommended for most users) or **CLI** (for terminal users).

---

### Option A: Web UI on cPanel

Best for: hosting the agent on a cPanel server with a domain, accessible from any browser.

#### 1. Create a cPanel account

In WHM, create a new account (or use an existing one) and assign a domain or subdomain to it, for example `agent.yourdomain.com`.

#### 2. SSH into the account

```bash
ssh youraccount@yourserver.com
```

Or use the cPanel Terminal (cPanel > Advanced > Terminal).

#### 3. Clone the repo into your home directory

```bash
cd ~
git clone https://github.com/rifle-ak/systems-admin-agent.git
cd systems-admin-agent
```

#### 4. Install Python dependencies (user-level, no root needed)

```bash
pip install --user paramiko anthropic rich click flask flask-socketio simple-websocket python-dotenv
```

If `pip` isn't available, try `pip3` or `python3 -m pip`.

#### 5. Configure

```bash
cp .env.example .env
nano .env
```

At minimum, set:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
WEB_PASSWORD=pick-a-strong-password
```

#### 6. Start the web UI

```bash
python3 -m sysadmin_agent web --web-port 5000
```

#### 7. Access it

Open `http://agent.yourdomain.com:5000` in your browser.

The setup wizard will verify everything is installed correctly. If anything is missing, it will offer to install it for you.

#### Keeping it running (optional)

To keep the agent running after you close your SSH session:

```bash
# Using nohup
nohup python3 -m sysadmin_agent web --web-port 5000 &

# Or using screen
screen -S sysadmin
python3 -m sysadmin_agent web --web-port 5000
# Press Ctrl+A then D to detach. Reattach with: screen -r sysadmin
```

#### cPanel Proxy (optional, use port 443 instead of 5000)

If you want to access the agent through your domain without a port number, set up a cPanel Application in **cPanel > Setup Python App** (if available) or use an `.htaccess` proxy rule. This depends on your hosting setup — ask the agent itself for help once it's running.

---

### Option B: Web UI on Any Linux Server

Best for: VPS, dedicated servers, cloud instances (AWS, DigitalOcean, Vultr, etc.).

```bash
# 1. Clone
git clone https://github.com/rifle-ak/systems-admin-agent.git
cd systems-admin-agent

# 2. Install dependencies
pip install --user paramiko anthropic rich click flask flask-socketio simple-websocket python-dotenv

# 3. Configure
cp .env.example .env
nano .env   # Set ANTHROPIC_API_KEY and WEB_PASSWORD

# 4. Start
python3 -m sysadmin_agent web --web-port 5000
```

Open `http://your-server-ip:5000` in your browser.

> **Firewall note:** Make sure port 5000 is open, or use `--web-port 80` if port 80 is free. For production, use the automated setup below.

#### Production Deployment (Apache + HTTPS + systemd)

For a production setup with HTTPS, a reverse proxy, and auto-start on boot:

```bash
sudo bash deploy/setup-domain.sh
```

This script:
1. Installs Python dependencies
2. Enables required Apache modules (`proxy`, `proxy_http`, `proxy_wstunnel`, `headers`, `ssl`, `rewrite`)
3. Obtains an SSL certificate via Let's Encrypt (certbot)
4. Installs the Apache reverse proxy config (auto-detects server IP for cPanel compatibility)
5. Installs and enables a systemd service
6. Starts the web UI

Before running, edit the variables at the top of the script to match your setup:
```bash
DOMAIN="your-domain.com"
APP_USER="your-username"
```

After setup, manage the service with:
```bash
systemctl status sysadmin-agent     # Check status
systemctl restart sysadmin-agent    # Restart
journalctl -u sysadmin-agent -f     # View logs
```

---

### Option C: Web UI on macOS

Best for: running from your laptop to manage remote servers.

```bash
# 1. Clone
git clone https://github.com/rifle-ak/systems-admin-agent.git
cd systems-admin-agent

# 2. Install dependencies (use pip3 on macOS)
pip3 install paramiko anthropic rich click flask flask-socketio simple-websocket python-dotenv

# 3. Configure
cp .env.example .env
nano .env   # Set ANTHROPIC_API_KEY

# 4. Start
python3 -m sysadmin_agent web --web-port 5000
```

Open `http://localhost:5000` in your browser. No password needed when running locally.

---

### Option D: CLI Only (No Web UI)

Best for: terminal-savvy users who prefer SSH + command line.

```bash
# 1. Clone
git clone https://github.com/rifle-ak/systems-admin-agent.git
cd systems-admin-agent

# 2. Install dependencies (fewer than web UI — no flask needed)
pip install --user paramiko anthropic rich click

# 3. Set your API key
export ANTHROPIC_API_KEY="sk-ant-..."

# 4. Use it
# Interactive mode (recommended — connect once, ask multiple questions)
python3 -m sysadmin_agent -h myserver.com -u root -k ~/.ssh/id_rsa interactive

# Or ask a single question
python3 -m sysadmin_agent -h myserver.com -u root -k ~/.ssh/id_rsa ask "optimize WordPress page speed"

# Or run a full scan
python3 -m sysadmin_agent -h myserver.com -u root -k ~/.ssh/id_rsa scan
```

Interactive mode:
```
> check disk usage and suggest cleanup
> why is WordPress slow on this server
> show me the top memory consumers
> !uptime              # prefix with ! to run raw commands
> exit
```

---

## Using the Web UI

Once the setup wizard completes, you'll land on the dashboard.

### Connecting to a server
1. Fill in the connection form in the left sidebar (host, username, password or key path)
2. Click **Connect**
3. The agent will scan the server and show you what it found (OS, apps, services)

### Asking questions
Type in the chat box at the bottom, in plain English:
- "Why is this WordPress site slow?"
- "Check if there are any security issues"
- "Optimize PHP-FPM for this server"
- "Show me disk usage"

### Running raw commands
Prefix with `!` or toggle to command mode:
- `!uptime`
- `!df -h`
- `!wp plugin list --path=/home/user/public_html`

### Quick actions
Use the sidebar buttons for common tasks:
- **Scan** — Full server scan (OS + apps + diagnostics)
- **Diagnose** — Run health checks only
- **Fix** — Find and fix issues (asks for approval on destructive actions)

### Approval flow
When the agent wants to do something destructive (restart a service, modify a config file), it will show an approval card with:
- The exact command it wants to run
- A description of what it does
- A snapshot ID (for rollback if needed)

Click **Approve** or **Deny**. If you don't respond within 60 seconds, it's automatically denied.

### Server profiles
Save connection details so you don't retype them:
1. Fill in the connection form
2. Click **Save Profile**
3. Next time, select from the dropdown

Passwords are never stored — the agent only saves a "password required" flag and asks you each time.

---

## Configuration

Copy `.env.example` to `.env` and set your values:

```bash
cp .env.example .env
```

| Setting | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Your Anthropic API key for AI features |
| `WEB_PASSWORD` | Recommended | Password to protect the web UI (leave empty for no auth) |
| `SECRET_KEY` | No | Flask secret key (auto-generated if not set) |
| `AI_MODEL` | No | AI model (default: `claude-sonnet-4-20250514`) |

---

## All CLI Commands

```bash
python3 -m sysadmin_agent [SSH OPTIONS] <command>

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

### SSH Options

| Option | Description |
|---|---|
| `-h, --host` | Server hostname or IP (required for SSH commands) |
| `-p, --port` | SSH port (default: 22) |
| `-u, --username` | SSH username (required for SSH commands) |
| `-P, --password` | SSH password |
| `-k, --key` | Path to SSH private key |
| `--passphrase` | Passphrase for encrypted key |
| `--api-key` | Anthropic API key (or set `ANTHROPIC_API_KEY`) |
| `--auto-approve` | Skip approval prompts (use with caution) |

---

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

---

## Safety Design

1. **Non-destructive by default** — Read-only commands run without prompts
2. **Human approval required** — Destructive actions show details and require "Yes"
3. **Automatic snapshots** — Affected files/services backed up before changes
4. **Rollback on demand** — Any snapshot can be restored
5. **Deny-by-default** — Non-interactive mode and approval timeouts default to deny
6. **Never guesses** — Asks clarifying questions when uncertain
7. **Passwords never stored** — Server profiles store a "password required" flag, not the actual password

---

## Auto-Updater

Pull the latest version while preserving your configs:

```bash
cd ~/systems-admin-agent
python3 -m sysadmin_agent.updater
```

The updater:
- Backs up `.env` and `config.json` before updating
- Preserves your existing config values
- Adds new config keys with their defaults (marked with `# NEW in update`)
- Comments out removed keys (marked with `# REMOVED in update`)
- Updates Python dependencies automatically
- Can rollback if something goes wrong

---

## Token Efficiency

The agent minimizes API costs by:
- Running direct commands (OS detection, app discovery, diagnostics) without AI
- Using a local knowledge base for known software docs
- Condensing conversation history into summaries instead of sending full transcripts
- Only calling Claude when plain English interpretation or complex analysis is needed
- Tracking and displaying token usage after every session

---

## Deployment Troubleshooting

### "Index of" or cPanel default page instead of the web UI

This means Apache is not proxying to the app. Common causes:

1. **App not running** — Verify with: `curl -s http://127.0.0.1:5000/` (should return HTML)
2. **Wrong VirtualHost IP** — On cPanel/WHM servers, vhosts must be bound to the server's IP, not `*`. Run `apachectl -S` to see what IP other vhosts use, then update your config:
   ```bash
   sed -i 's/\*:80/YOUR_IP:80/' /etc/apache2/conf.d/sysadmin-agent.conf
   sed -i 's/\*:443/YOUR_IP:443/' /etc/apache2/conf.d/sysadmin-agent.conf
   apachectl configtest && apachectl graceful
   ```
3. **Cloudflare caching** — Purge cache in the Cloudflare dashboard after config changes. Test bypassing Cloudflare:
   ```bash
   curl -sk https://your-domain --resolve your-domain:443:YOUR_SERVER_IP | head -5
   ```

### Apache ProxyPass "Unable to parse URL"

The URL in `ProxyPass` has extra characters (often `<` `>` angle brackets from copy-paste). The URL must be plain: `http://127.0.0.1:5000/` with no surrounding brackets.

### Cloudflare SSL issues

Set Cloudflare SSL/TLS mode to **Full (Strict)** when using Let's Encrypt certs. If set to "Flexible", Cloudflare sends HTTP to your server, which then redirects to HTTPS, causing a redirect loop.

### Service won't start

```bash
journalctl -u sysadmin-agent -n 50    # Check logs
which sysadmin-agent                   # Verify CLI is installed
pip3 install -e /path/to/systems-admin-agent  # Reinstall if missing
```

---

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

## Testing

```bash
pytest tests/ -v
```

## License

MIT
