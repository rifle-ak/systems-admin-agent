#!/bin/bash
# Setup script for deploying systems-admin-agent behind Apache on a cPanel server.
# Usage: sudo bash deploy/setup-domain.sh
#
# This script:
# 1. Enables required Apache modules
# 2. Installs the Apache reverse proxy vhost config
# 3. Obtains an SSL certificate via Let's Encrypt (certbot)
# 4. Installs and enables the systemd service
# 5. Starts the web UI

set -euo pipefail

DOMAIN="server.contois.fyi"
APP_USER="contois"
APP_DIR="/home/${APP_USER}/systems-admin-agent"
DEPLOY_DIR="${APP_DIR}/deploy"
SERVICE_NAME="sysadmin-agent"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[x]${NC} $1"; exit 1; }

# Must be root
[[ $EUID -eq 0 ]] || error "Run this script as root: sudo bash $0"

# ── Step 0: Install Python dependencies ───────────────────────────
info "Installing Python dependencies..."
if [[ -f "${APP_DIR}/requirements.txt" ]]; then
    pip3 install -r "${APP_DIR}/requirements.txt" --quiet || warn "pip install failed — check manually"
fi
# Install the package itself if setup.py or pyproject.toml exists
if [[ -f "${APP_DIR}/setup.py" ]] || [[ -f "${APP_DIR}/pyproject.toml" ]]; then
    pip3 install -e "${APP_DIR}" --quiet || warn "pip install -e failed — check manually"
fi

# ── Step 1: Apache modules ──────────────────────────────────────────
info "Enabling required Apache modules..."
for mod in proxy proxy_http proxy_wstunnel headers ssl rewrite; do
    if ! apachectl -M 2>/dev/null | grep -q "${mod}_module"; then
        a2enmod "$mod" 2>/dev/null || warn "Could not enable mod_${mod} (may already be compiled in)"
    fi
done

# ── Step 2: SSL certificate via Let's Encrypt ───────────────────────
if [[ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
    info "Obtaining SSL certificate for ${DOMAIN}..."
    if command -v certbot &>/dev/null; then
        certbot certonly --webroot -w "/var/www/html" -d "${DOMAIN}" --non-interactive --agree-tos --register-unsafely-without-email || {
            warn "Certbot webroot failed, stopping Apache for standalone mode..."
            systemctl stop httpd 2>/dev/null || apachectl stop 2>/dev/null || true
            certbot certonly --standalone -d "${DOMAIN}" --non-interactive --agree-tos --register-unsafely-without-email
            systemctl start httpd 2>/dev/null || apachectl start 2>/dev/null || true
        }
    else
        warn "certbot not found. Install it or use cPanel AutoSSL."
        warn "Then update the SSL paths in the Apache config."
        warn "Continuing without SSL for now..."
    fi
else
    info "SSL certificate already exists for ${DOMAIN}"
fi

# ── Step 3: Apache vhost config ─────────────────────────────────────
APACHE_CONF="/etc/apache2/conf.d/sysadmin-agent.conf"
# cPanel may use /etc/httpd/conf.d/ instead
if [[ -d /etc/httpd/conf.d ]] && [[ ! -d /etc/apache2/conf.d ]]; then
    APACHE_CONF="/etc/httpd/conf.d/sysadmin-agent.conf"
fi

info "Installing Apache reverse proxy config to ${APACHE_CONF}..."
cp "${DEPLOY_DIR}/apache-reverse-proxy.conf" "${APACHE_CONF}"

# If SSL cert doesn't exist yet, comment out the SSL vhost to avoid Apache errors
if [[ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
    warn "No SSL cert found — disabling HTTPS vhost (HTTP-only for now)."
    # Replace the 443 vhost with a simple non-SSL proxy
    cat > "${APACHE_CONF}" <<'HTTPCONF'
<VirtualHost *:80>
    ServerName server.contois.fyi
    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:5000/
    ProxyPassReverse / http://127.0.0.1:5000/
    RewriteEngine On
    RewriteCond %{HTTP:Upgrade} =websocket [NC]
    RewriteRule /(.*) ws://127.0.0.1:5000/$1 [P,L]
    ProxyTimeout 300
</VirtualHost>
HTTPCONF
fi

# Test and reload Apache
apachectl configtest && apachectl graceful
info "Apache config installed and reloaded."

# ── Step 4: Systemd service ─────────────────────────────────────────
info "Installing systemd service..."
cp "${DEPLOY_DIR}/sysadmin-agent.service" /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}

# ── Step 5: Ensure .env exists ──────────────────────────────────────
if [[ ! -f "${APP_DIR}/.env" ]]; then
    warn ".env file not found at ${APP_DIR}/.env"
    warn "Copy .env.example and set your ANTHROPIC_API_KEY and WEB_PASSWORD before starting."
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    chown ${APP_USER}:${APP_USER} "${APP_DIR}/.env"
    chmod 600 "${APP_DIR}/.env"
fi

# ── Step 6: Start the service ──────────────────────────────────────
info "Starting ${SERVICE_NAME}..."
systemctl restart ${SERVICE_NAME}
sleep 2

if systemctl is-active --quiet ${SERVICE_NAME}; then
    info "Service is running!"
else
    warn "Service may not have started. Check: journalctl -u ${SERVICE_NAME} -f"
fi

echo ""
info "Setup complete!"
echo ""
echo "  Domain:  https://${DOMAIN}"
echo "  Service: systemctl status ${SERVICE_NAME}"
echo "  Logs:    journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "  Make sure WEB_PASSWORD is set in ${APP_DIR}/.env"
echo "  to protect the web UI with login authentication."
echo ""
