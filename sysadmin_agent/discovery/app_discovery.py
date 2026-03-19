from concurrent.futures import ThreadPoolExecutor, as_completed
import re


class AppDiscovery:
    def __init__(self, ssh_manager):
        self.ssh = ssh_manager

    def discover(self):
        categories = {
            "services": self._discover_system_services,
            "web_servers": self._discover_web_servers,
            "databases": self._discover_databases,
            "control_panels": self._discover_control_panels,
            "cms": self._discover_cms,
            "languages": self._discover_languages,
            "containers": self._discover_containers,
            "game_servers": self._discover_game_servers,
        }

        results = {}
        # Limit concurrency to avoid overwhelming the SSH connection
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(fn): name
                for name, fn in categories.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception:
                    results[name] = []

        return results

    def _run(self, command, timeout=15):
        try:
            return self.ssh.execute(command, timeout=timeout)
        except Exception:
            return {"stdout": "", "stderr": "", "exit_code": 1}

    def _check_binary(self, name, version_flag="--version"):
        result = self._run(f"which {name} 2>/dev/null && {name} {version_flag} 2>&1 | head -1")
        if result["exit_code"] == 0 and result["stdout"].strip():
            lines = result["stdout"].strip().splitlines()
            version_line = lines[-1] if len(lines) > 1 else lines[0]
            return {"name": name, "version": version_line.strip(), "installed": True}
        return None

    def _resolve_wp_domain(self, wp_dir):
        """Resolve the domain name for a WordPress installation.

        Tries multiple strategies:
        1. wp-cli option get siteurl (most reliable)
        2. Parse WP_SITEURL / WP_HOME from wp-config.php
        3. Match web server vhost configs pointing at this document root
        4. Infer from cPanel-style path (/home/USER/public_html -> main domain)
        """
        domain = None

        # Strategy 1: wp-cli (fast, authoritative)
        wp_result = self._run(
            f"wp option get siteurl --path={wp_dir} --skip-themes --skip-plugins 2>/dev/null",
            timeout=10,
        )
        if wp_result["exit_code"] == 0 and wp_result["stdout"].strip():
            url = wp_result["stdout"].strip()
            domain = re.sub(r'^https?://', '', url).split('/')[0]
            if domain:
                return domain

        # Strategy 2: Parse wp-config.php for hardcoded URL
        config_result = self._run(
            f"grep -E \"WP_SITEURL|WP_HOME\" {wp_dir}/wp-config.php 2>/dev/null | head -2"
        )
        if config_result["exit_code"] == 0 and config_result["stdout"].strip():
            for line in config_result["stdout"].strip().splitlines():
                match = re.search(r"https?://([^'\"/:]+)", line)
                if match:
                    domain = match.group(1)
                    return domain

        # Strategy 3: Match Apache/Nginx vhost document root to this path
        vhost_result = self._run(
            f"grep -rl '{re.escape(wp_dir)}' "
            "/etc/apache2/sites-enabled/ /etc/httpd/conf.d/ "
            "/etc/nginx/sites-enabled/ /etc/nginx/conf.d/ "
            "/usr/local/apache/conf/httpd.conf "
            "2>/dev/null | head -3"
        )
        if vhost_result["exit_code"] == 0 and vhost_result["stdout"].strip():
            for vhost_file in vhost_result["stdout"].strip().splitlines():
                vhost_content = self._run(f"cat {vhost_file} 2>/dev/null")
                if vhost_content["exit_code"] == 0:
                    # Extract ServerName (Apache) or server_name (Nginx)
                    sn_match = re.search(
                        r'(?:ServerName|server_name)\s+([^\s;]+)',
                        vhost_content["stdout"], re.IGNORECASE,
                    )
                    if sn_match:
                        domain = sn_match.group(1).strip()
                        return domain

        # Strategy 4: cPanel path inference (/home/USER/public_html -> main domain)
        cpanel_match = re.match(r'/home/([^/]+)/public_html(?:/(.+))?$', wp_dir)
        if cpanel_match:
            user = cpanel_match.group(1)
            subdomain_path = cpanel_match.group(2)

            if subdomain_path:
                # Subdomain/addon domain — check cPanel userdata
                userdata = self._run(
                    f"cat /var/cpanel/userdata/{user}/* 2>/dev/null | "
                    f"grep -B5 '{re.escape(wp_dir)}' | grep 'ServerName\\|servername' | head -1"
                )
                if userdata["exit_code"] == 0 and userdata["stdout"].strip():
                    sn_match = re.search(r'(?:ServerName|servername):\s*(\S+)', userdata["stdout"])
                    if sn_match:
                        return sn_match.group(1)
            else:
                # Main public_html — get domain from cPanel user config
                main_domain = self._run(
                    f"grep '^DNS=' /var/cpanel/users/{user} 2>/dev/null | head -1 | cut -d= -f2"
                )
                if main_domain["exit_code"] == 0 and main_domain["stdout"].strip():
                    return main_domain["stdout"].strip()

        return domain

    def _find_site_error_logs(self, site_path, domain=None):
        """Find error log paths relevant to a specific site.

        Checks:
        1. WordPress debug.log
        2. PHP error log configured for the site
        3. Apache/Nginx vhost-specific error logs
        4. cPanel per-domain logs
        """
        logs = {}

        # 1. WordPress debug.log
        debug_log = f"{site_path}/wp-content/debug.log"
        result = self._run(f"test -f {debug_log} && echo 'exists'")
        if result["stdout"].strip() == "exists":
            logs["wp_debug_log"] = debug_log

        # 2. PHP error log from wp-config.php or ini
        php_log = self._run(
            f"grep -i 'ini_set.*error_log\\|WP_DEBUG_LOG' {site_path}/wp-config.php 2>/dev/null | head -2"
        )
        if php_log["exit_code"] == 0 and php_log["stdout"].strip():
            for line in php_log["stdout"].strip().splitlines():
                path_match = re.search(r"['\"](/[^'\"]+)['\"]", line)
                if path_match and "log" in path_match.group(1).lower():
                    candidate = path_match.group(1)
                    exists = self._run(f"test -f {candidate} && echo 'exists'")
                    if exists["stdout"].strip() == "exists":
                        logs["php_error_log"] = candidate

        # 3. Web server vhost error logs
        if domain:
            # Apache error logs
            apache_log_result = self._run(
                f"grep -rh 'ErrorLog' "
                f"/etc/apache2/sites-enabled/ /etc/httpd/conf.d/ "
                f"/usr/local/apache/conf/httpd.conf 2>/dev/null | "
                f"grep -i '{re.escape(domain)}' | head -1"
            )
            if apache_log_result["exit_code"] == 0 and apache_log_result["stdout"].strip():
                path_match = re.search(r'ErrorLog\s+(\S+)', apache_log_result["stdout"])
                if path_match:
                    log_path = path_match.group(1).strip('"')
                    logs["apache_error_log"] = log_path

            # Nginx error logs
            nginx_log_result = self._run(
                f"grep -rh 'error_log' "
                f"/etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | "
                f"grep -i '{re.escape(domain)}' | head -1"
            )
            if nginx_log_result["exit_code"] == 0 and nginx_log_result["stdout"].strip():
                path_match = re.search(r'error_log\s+(\S+)', nginx_log_result["stdout"])
                if path_match:
                    log_path = path_match.group(1).strip(';').strip('"')
                    logs["nginx_error_log"] = log_path

        # 4. cPanel per-user logs
        cpanel_match = re.match(r'/home/([^/]+)/', site_path)
        if cpanel_match:
            user = cpanel_match.group(1)
            cpanel_paths = [
                f"/home/{user}/logs/error.log",
                f"/var/log/apache2/domlogs/{user}/",
                f"/usr/local/apache/domlogs/{user}/",
            ]
            if domain:
                cpanel_paths.insert(0, f"/home/{user}/logs/{domain}.error.log")
                cpanel_paths.insert(1, f"/var/log/apache2/domlogs/{user}/{domain}-error_log")

            for p in cpanel_paths:
                exists = self._run(f"test -e {p} && echo 'exists'")
                if exists["stdout"].strip() == "exists":
                    logs["cpanel_error_log"] = p
                    break

        # 5. Generic fallback: look near the site path
        if not logs.get("apache_error_log") and not logs.get("nginx_error_log"):
            # Common global error log locations
            for candidate in [
                "/var/log/apache2/error.log",
                "/var/log/httpd/error_log",
                "/var/log/nginx/error.log",
                "/usr/local/apache/logs/error_log",
            ]:
                exists = self._run(f"test -f {candidate} && echo 'exists'")
                if exists["stdout"].strip() == "exists":
                    logs["server_error_log"] = candidate
                    break

        return logs if logs else None

    def _discover_system_services(self):
        services = []

        result = self._run("systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null")
        if result["exit_code"] == 0 and result["stdout"].strip():
            for line in result["stdout"].strip().splitlines():
                parts = line.split()
                if parts:
                    svc_name = parts[0].replace(".service", "")
                    services.append({"name": svc_name, "status": "running", "manager": "systemd"})
            return services

        result = self._run("service --status-all 2>/dev/null")
        if result["exit_code"] == 0 and result["stdout"].strip():
            for line in result["stdout"].strip().splitlines():
                match = re.match(r'\s*\[\s*([+-?])\s*\]\s+(.+)', line)
                if match:
                    status_char, svc_name = match.group(1), match.group(2).strip()
                    status = {"+" : "running", "-": "stopped", "?": "unknown"}.get(status_char, "unknown")
                    services.append({"name": svc_name, "status": status, "manager": "sysvinit"})

        return services

    def _discover_web_servers(self):
        servers = []
        checks = [
            ("nginx", "nginx -v 2>&1"),
            ("apache2", "apache2 -v 2>&1 || httpd -v 2>&1"),
            ("litespeed", "/usr/local/lsws/bin/lshttpd -v 2>&1"),
            ("caddy", "caddy version 2>&1"),
        ]
        for name, cmd in checks:
            result = self._run(cmd)
            if result["exit_code"] == 0 and result["stdout"].strip():
                version = result["stdout"].strip().splitlines()[0]
                servers.append({"name": name, "version": version})
            elif result["stderr"].strip():
                first_line = result["stderr"].strip().splitlines()[0]
                if "version" in first_line.lower() or "/" in first_line:
                    servers.append({"name": name, "version": first_line})
        return servers

    def _discover_databases(self):
        databases = []
        checks = [
            ("mysql", "mysql --version 2>/dev/null"),
            ("mariadb", "mariadb --version 2>/dev/null"),
            ("postgresql", "psql --version 2>/dev/null"),
            ("mongodb", "mongod --version 2>/dev/null | head -1"),
            ("redis", "redis-server --version 2>/dev/null"),
            ("sqlite3", "sqlite3 --version 2>/dev/null"),
            ("memcached", "memcached -h 2>/dev/null | head -1"),
        ]
        for name, cmd in checks:
            result = self._run(cmd)
            stdout = result["stdout"].strip()
            if result["exit_code"] == 0 and stdout:
                version = stdout.splitlines()[0]
                databases.append({"name": name, "version": version})

                if name in ("mysql", "mariadb"):
                    running = self._run("pgrep -x mysqld >/dev/null 2>&1 || pgrep -x mariadbd >/dev/null 2>&1")
                    databases[-1]["running"] = running["exit_code"] == 0
                elif name == "postgresql":
                    running = self._run("pgrep -x postgres >/dev/null 2>&1 || pgrep -x postmaster >/dev/null 2>&1")
                    databases[-1]["running"] = running["exit_code"] == 0
                elif name == "mongodb":
                    running = self._run("pgrep -x mongod >/dev/null 2>&1")
                    databases[-1]["running"] = running["exit_code"] == 0
                elif name == "redis":
                    running = self._run("pgrep -x redis-server >/dev/null 2>&1")
                    databases[-1]["running"] = running["exit_code"] == 0
                elif name == "memcached":
                    running = self._run("pgrep -x memcached >/dev/null 2>&1")
                    databases[-1]["running"] = running["exit_code"] == 0

        return databases

    def _discover_control_panels(self):
        panels = []
        checks = [
            ("cPanel/WHM", "/usr/local/cpanel/cpanel -V 2>/dev/null"),
            ("Plesk", "plesk version 2>/dev/null"),
            ("Webmin", "/usr/libexec/webmin/version.cgi 2>/dev/null || cat /etc/webmin/version 2>/dev/null"),
            ("DirectAdmin", "/usr/local/directadmin/directadmin v 2>/dev/null"),
            ("CyberPanel", "cyberpanel --version 2>/dev/null || cat /usr/local/CyberCP/version.txt 2>/dev/null"),
            ("VestaCP", "cat /usr/local/vesta/conf/vesta.conf 2>/dev/null | head -1"),
            ("HestiaCP", "/usr/local/hestia/bin/v-list-sys-info 2>/dev/null | head -3"),
            ("ISPConfig", "cat /usr/local/ispconfig/server/lib/config.inc.php 2>/dev/null | grep -oP \"define.*ISPC_APP_VERSION.*?'\\K[^']+\""),
            ("CloudPanel", "cat /home/clp/htdocs/app/config/version.php 2>/dev/null || clpctl --version 2>/dev/null"),
        ]
        for name, cmd in checks:
            result = self._run(cmd)
            stdout = result["stdout"].strip()
            if result["exit_code"] == 0 and stdout:
                version = stdout.splitlines()[0]
                panels.append({"name": name, "version": version})
        return panels

    def _discover_cms(self):
        cms_list = []

        # WordPress
        wp_result = self._run(
            "find /var/www /home -maxdepth 5 -name wp-config.php -type f 2>/dev/null | head -10",
            timeout=30
        )
        if wp_result["exit_code"] == 0 and wp_result["stdout"].strip():
            for wp_config in wp_result["stdout"].strip().splitlines():
                wp_dir = wp_config.rsplit("/", 1)[0]
                ver_result = self._run(
                    f"grep '\\$wp_version' {wp_dir}/wp-includes/version.php 2>/dev/null | head -1"
                )
                version = "unknown"
                if ver_result["exit_code"] == 0 and ver_result["stdout"].strip():
                    match = re.search(r"'([^']+)'", ver_result["stdout"])
                    if match:
                        version = match.group(1)
                entry = {"name": "WordPress", "version": version, "path": wp_dir}

                # Resolve domain via wp-cli or wp-config.php
                domain = self._resolve_wp_domain(wp_dir)
                if domain:
                    entry["domain"] = domain

                # Map error log paths for this site
                error_logs = self._find_site_error_logs(wp_dir, domain)
                if error_logs:
                    entry["error_logs"] = error_logs

                cms_list.append(entry)

        # Joomla
        joomla_result = self._run(
            "find /var/www /home -maxdepth 5 -name configuration.php -path '*/joomla*' -o -name 'joomla.xml' 2>/dev/null | head -5",
            timeout=30
        )
        if joomla_result["exit_code"] == 0 and joomla_result["stdout"].strip():
            for path in joomla_result["stdout"].strip().splitlines():
                joomla_dir = path.rsplit("/", 1)[0]
                manifest = self._run(
                    f"cat {joomla_dir}/administrator/manifests/files/joomla.xml 2>/dev/null "
                    f"|| cat {joomla_dir}/joomla.xml 2>/dev/null"
                )
                version = "unknown"
                if manifest["exit_code"] == 0:
                    match = re.search(r'<version>([^<]+)</version>', manifest["stdout"])
                    if match:
                        version = match.group(1)
                cms_list.append({"name": "Joomla", "version": version, "path": joomla_dir})

        # Drupal
        drupal_result = self._run(
            "find /var/www /home -maxdepth 5 -name 'core.services.yml' -path '*/core/*' 2>/dev/null | head -5",
            timeout=30
        )
        if drupal_result["exit_code"] == 0 and drupal_result["stdout"].strip():
            for path in drupal_result["stdout"].strip().splitlines():
                drupal_dir = path.rsplit("/core/", 1)[0]
                ver_result = self._run(
                    f"grep -oP \"const VERSION = '\\K[^']+\" {drupal_dir}/core/lib/Drupal.php 2>/dev/null"
                )
                version = ver_result["stdout"].strip() if ver_result["exit_code"] == 0 else "unknown"
                cms_list.append({"name": "Drupal", "version": version, "path": drupal_dir})

        # Magento
        magento_result = self._run(
            "find /var/www /home -maxdepth 5 -name 'composer.json' -exec grep -l 'magento' {} \\; 2>/dev/null | head -5",
            timeout=30
        )
        if magento_result["exit_code"] == 0 and magento_result["stdout"].strip():
            for path in magento_result["stdout"].strip().splitlines():
                magento_dir = path.rsplit("/", 1)[0]
                ver_result = self._run(
                    f"php {magento_dir}/bin/magento --version 2>/dev/null || "
                    f"grep '\"version\"' {magento_dir}/composer.json 2>/dev/null | head -1"
                )
                version = ver_result["stdout"].strip() if ver_result["exit_code"] == 0 else "unknown"
                cms_list.append({"name": "Magento", "version": version, "path": magento_dir})

        return cms_list

    def _discover_languages(self):
        languages = []
        checks = [
            ("php", "php --version 2>/dev/null | head -1"),
            ("python", "python3 --version 2>/dev/null || python --version 2>/dev/null"),
            ("node", "node --version 2>/dev/null"),
            ("ruby", "ruby --version 2>/dev/null"),
            ("java", "java -version 2>&1 | head -1"),
            ("go", "go version 2>/dev/null"),
            ("perl", "perl --version 2>/dev/null | grep -oP 'v[\\d.]+'"),
        ]
        for name, cmd in checks:
            result = self._run(cmd)
            stdout = result["stdout"].strip()
            if result["exit_code"] == 0 and stdout:
                version = stdout.splitlines()[0]
                languages.append({"name": name, "version": version})
        return languages

    def _discover_containers(self):
        containers = []

        # Docker
        docker_ver = self._run("docker --version 2>/dev/null")
        if docker_ver["exit_code"] == 0 and docker_ver["stdout"].strip():
            docker_info = {"name": "docker", "version": docker_ver["stdout"].strip().splitlines()[0], "containers": []}
            ps_result = self._run("docker ps --format '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null")
            if ps_result["exit_code"] == 0 and ps_result["stdout"].strip():
                for line in ps_result["stdout"].strip().splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 4:
                        docker_info["containers"].append({
                            "id": parts[0],
                            "name": parts[1],
                            "image": parts[2],
                            "status": parts[3],
                        })
            containers.append(docker_info)

        # Podman
        podman_ver = self._run("podman --version 2>/dev/null")
        if podman_ver["exit_code"] == 0 and podman_ver["stdout"].strip():
            podman_info = {"name": "podman", "version": podman_ver["stdout"].strip().splitlines()[0], "containers": []}
            ps_result = self._run("podman ps --format '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null")
            if ps_result["exit_code"] == 0 and ps_result["stdout"].strip():
                for line in ps_result["stdout"].strip().splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 4:
                        podman_info["containers"].append({
                            "id": parts[0],
                            "name": parts[1],
                            "image": parts[2],
                            "status": parts[3],
                        })
            containers.append(podman_info)

        return containers

    def _discover_game_servers(self):
        servers = []

        # Pterodactyl Panel
        ptero_result = self._run(
            "php /var/www/pterodactyl/artisan --version 2>/dev/null || "
            "cat /var/www/pterodactyl/.env 2>/dev/null | grep APP_VERSION | head -1"
        )
        if ptero_result["exit_code"] == 0 and ptero_result["stdout"].strip():
            version = ptero_result["stdout"].strip().splitlines()[0]
            servers.append({"name": "Pterodactyl Panel", "version": version})

        # Pterodactyl Wings
        wings_result = self._run("wings --version 2>/dev/null || systemctl is-active wings 2>/dev/null")
        if wings_result["exit_code"] == 0 and wings_result["stdout"].strip():
            version = wings_result["stdout"].strip().splitlines()[0]
            running = self._run("systemctl is-active wings 2>/dev/null")
            servers.append({
                "name": "Pterodactyl Wings",
                "version": version,
                "running": running["exit_code"] == 0,
            })

        # Rust Dedicated Server (via Pterodactyl or standalone)
        rust_result = self._run(
            "pgrep -a RustDedicated 2>/dev/null || "
            "docker ps --format '{{.Names}}\\t{{.Image}}' 2>/dev/null | grep -i rust"
        )
        if rust_result["exit_code"] == 0 and rust_result["stdout"].strip():
            servers.append({
                "name": "Rust Dedicated Server",
                "version": rust_result["stdout"].strip().splitlines()[0],
                "running": True,
            })

        # Oxide/uMod
        oxide_result = self._run(
            "find /var/lib/pterodactyl/volumes -maxdepth 4 -name 'Oxide.Core.dll' -type f 2>/dev/null | head -1 || "
            "find /home -maxdepth 5 -name 'Oxide.Core.dll' -type f 2>/dev/null | head -1",
            timeout=15,
        )
        if oxide_result["exit_code"] == 0 and oxide_result["stdout"].strip():
            oxide_path = oxide_result["stdout"].strip().splitlines()[0]
            servers.append({
                "name": "Oxide/uMod",
                "path": oxide_path.rsplit("/", 1)[0],
                "installed": True,
            })

        return servers
