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
                cms_list.append({"name": "WordPress", "version": version, "path": wp_dir})

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
