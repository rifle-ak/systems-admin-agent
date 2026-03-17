import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class WordPressChecker:
    def __init__(self, ssh_manager, site_path):
        self.ssh = ssh_manager
        self.site_path = site_path.rstrip("/")
        self._wp_cli = None
        self._checks = [
            self.check_wp_version,
            self.check_wp_debug_mode,
            self.check_plugin_updates,
            self.check_theme_updates,
            self.check_inactive_plugins,
            self.check_php_fpm_config,
            self.check_opcache,
            self.check_object_cache,
            self.check_wp_cron,
            self.check_database_optimization,
            self.check_file_permissions,
            self.check_ssl_and_urls,
            self.check_page_speed_factors,
            self.check_security_basics,
            self.check_elementor_health,
        ]

    # ------------------------------------------------------------------ #
    # WP-CLI detection
    # ------------------------------------------------------------------ #

    def _detect_wp_cli(self):
        """Detect wp-cli path. Returns the path or None."""
        if self._wp_cli is not None:
            return self._wp_cli if self._wp_cli else None

        candidates = ["wp", "/usr/local/bin/wp", "~/.local/bin/wp"]
        for candidate in candidates:
            result = self.ssh.execute(f"{candidate} --version 2>/dev/null")
            if result["exit_code"] == 0 and "WP-CLI" in result["stdout"]:
                self._wp_cli = candidate
                return self._wp_cli

        self._wp_cli = ""
        return None

    def _wp(self, subcommand, timeout=30):
        """Run a wp-cli command in the site path. Returns result dict or None if wp-cli unavailable."""
        cli = self._detect_wp_cli()
        if cli is None:
            return None
        return self.ssh.execute(
            f"{cli} {subcommand} --path={self.site_path}", timeout=timeout
        )

    def _wp_cli_missing_result(self, name):
        return {
            "name": name,
            "status": "info",
            "severity": "low",
            "details": "wp-cli not installed — install it for deeper WordPress analysis",
            "fix": None,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _ok(self, name, details):
        return {"name": name, "status": "ok", "severity": "low", "details": details, "fix": None}

    def _error(self, name, details):
        return {"name": name, "status": "error", "severity": "low", "details": details, "fix": None}

    # ------------------------------------------------------------------ #
    # 1. WordPress version
    # ------------------------------------------------------------------ #

    def check_wp_version(self):
        name = "check_wp_version"
        result = self.ssh.execute(f"cat {self.site_path}/wp-includes/version.php")
        if result["exit_code"] != 0:
            return self._error(name, f"Cannot read version.php: {result['stderr']}")

        match = re.search(r"\$wp_version\s*=\s*'([^']+)'", result["stdout"])
        if not match:
            return self._error(name, "Could not parse WordPress version from version.php")

        current = match.group(1)

        # Known latest at the time of writing — this should be updated regularly
        known_latest = "6.7.2"

        if current == known_latest:
            return self._ok(name, f"WordPress {current} is up to date")

        # Simple semver comparison
        def _ver_tuple(v):
            return tuple(int(x) for x in v.split(".") if x.isdigit())

        if _ver_tuple(current) < _ver_tuple(known_latest):
            return {
                "name": name,
                "status": "warning",
                "severity": "medium",
                "details": f"WordPress {current} is outdated (latest known: {known_latest})",
                "fix": [
                    {
                        "command": f"{self._detect_wp_cli() or 'wp'} core update --path={self.site_path}",
                        "description": "Update WordPress core to latest version",
                        "destructive": True,
                    }
                ],
            }

        return self._ok(name, f"WordPress {current} (known latest: {known_latest})")

    # ------------------------------------------------------------------ #
    # 2. WP_DEBUG mode
    # ------------------------------------------------------------------ #

    def check_wp_debug_mode(self):
        name = "check_wp_debug_mode"
        result = self.ssh.execute(f"cat {self.site_path}/wp-config.php")
        if result["exit_code"] != 0:
            return self._error(name, f"Cannot read wp-config.php: {result['stderr']}")

        content = result["stdout"]

        debug_match = re.search(
            r"define\s*\(\s*['\"]WP_DEBUG['\"]\s*,\s*(true|false)\s*\)", content, re.IGNORECASE
        )

        if not debug_match:
            return self._ok(name, "WP_DEBUG not explicitly set (defaults to false)")

        if debug_match.group(1).lower() == "true":
            return {
                "name": name,
                "status": "warning",
                "severity": "medium",
                "details": "WP_DEBUG is enabled — should be disabled on production",
                "fix": [
                    {
                        "command": (
                            f"sed -i \"s/define\\s*("
                            f"\\s*['\\\"]WP_DEBUG['\\\"]\\s*,\\s*true\\s*)/define('WP_DEBUG', false)/g\" "
                            f"{self.site_path}/wp-config.php"
                        ),
                        "description": "Set WP_DEBUG to false in wp-config.php",
                        "destructive": True,
                    }
                ],
            }

        return self._ok(name, "WP_DEBUG is disabled")

    # ------------------------------------------------------------------ #
    # 3. Plugin updates
    # ------------------------------------------------------------------ #

    def check_plugin_updates(self):
        name = "check_plugin_updates"
        result = self._wp("plugin list --format=json")
        if result is None:
            return self._wp_cli_missing_result(name)
        if result["exit_code"] != 0:
            return self._error(name, f"wp plugin list failed: {result['stderr']}")

        try:
            plugins = json.loads(result["stdout"])
        except (json.JSONDecodeError, ValueError) as e:
            return self._error(name, f"Failed to parse plugin list JSON: {e}")

        needs_update = [p for p in plugins if p.get("update") == "available"]

        if not needs_update:
            return self._ok(name, f"All {len(plugins)} plugins are up to date")

        details_lines = [f"{len(needs_update)} plugin(s) have updates available:"]
        for p in needs_update:
            details_lines.append(
                f"  - {p.get('name', '?')} ({p.get('version', '?')} -> update available)"
            )

        return {
            "name": name,
            "status": "info",
            "severity": "low",
            "details": "\n".join(details_lines),
            "fix": None,
        }

    # ------------------------------------------------------------------ #
    # 4. Theme updates
    # ------------------------------------------------------------------ #

    def check_theme_updates(self):
        name = "check_theme_updates"
        result = self._wp("theme list --format=json")
        if result is None:
            return self._wp_cli_missing_result(name)
        if result["exit_code"] != 0:
            return self._error(name, f"wp theme list failed: {result['stderr']}")

        try:
            themes = json.loads(result["stdout"])
        except (json.JSONDecodeError, ValueError) as e:
            return self._error(name, f"Failed to parse theme list JSON: {e}")

        needs_update = [t for t in themes if t.get("update") == "available"]

        if not needs_update:
            return self._ok(name, f"All {len(themes)} themes are up to date")

        details_lines = [f"{len(needs_update)} theme(s) have updates available:"]
        for t in needs_update:
            details_lines.append(
                f"  - {t.get('name', '?')} ({t.get('version', '?')} -> update available)"
            )

        return {
            "name": name,
            "status": "info",
            "severity": "low",
            "details": "\n".join(details_lines),
            "fix": None,
        }

    # ------------------------------------------------------------------ #
    # 5. Inactive plugins
    # ------------------------------------------------------------------ #

    def check_inactive_plugins(self):
        name = "check_inactive_plugins"
        result = self._wp("plugin list --format=json")
        if result is None:
            return self._wp_cli_missing_result(name)
        if result["exit_code"] != 0:
            return self._error(name, f"wp plugin list failed: {result['stderr']}")

        try:
            plugins = json.loads(result["stdout"])
        except (json.JSONDecodeError, ValueError) as e:
            return self._error(name, f"Failed to parse plugin list JSON: {e}")

        inactive = [p for p in plugins if p.get("status") == "inactive"]

        if not inactive:
            return self._ok(name, "No inactive plugins found")

        names = [p.get("name", "?") for p in inactive]
        cli = self._detect_wp_cli() or "wp"
        fix_actions = [
            {
                "command": f"{cli} plugin delete {n} --path={self.site_path}",
                "description": f"Delete inactive plugin '{n}'",
                "destructive": True,
            }
            for n in names
        ]

        return {
            "name": name,
            "status": "warning",
            "severity": "low",
            "details": f"{len(inactive)} inactive plugin(s) (security risk / bloat): {', '.join(names)}",
            "fix": fix_actions,
        }

    # ------------------------------------------------------------------ #
    # 6. PHP-FPM configuration
    # ------------------------------------------------------------------ #

    def check_php_fpm_config(self):
        name = "check_php_fpm_config"

        # Detect PHP version
        php_ver_result = self.ssh.execute("php -r \"echo PHP_MAJOR_VERSION.'.'.PHP_MINOR_VERSION;\" 2>/dev/null")
        php_version = php_ver_result["stdout"].strip() if php_ver_result["exit_code"] == 0 else None

        # Search for pool config files
        search_paths = []
        if php_version:
            search_paths.append(f"/etc/php/{php_version}/fpm/pool.d/")
        search_paths.extend([
            "/etc/php/*/fpm/pool.d/",
            "/opt/cpanel/ea-php*/root/etc/php-fpm.d/",
            "/etc/php-fpm.d/",
        ])

        pool_config = None
        pool_path = None
        for pattern in search_paths:
            result = self.ssh.execute(f"cat {pattern}*.conf 2>/dev/null | head -200")
            if result["exit_code"] == 0 and result["stdout"].strip():
                pool_config = result["stdout"]
                # Get the actual file path
                path_result = self.ssh.execute(f"ls {pattern}*.conf 2>/dev/null | head -1")
                pool_path = path_result["stdout"].strip() if path_result["exit_code"] == 0 else pattern
                break

        if not pool_config:
            return {
                "name": name,
                "status": "info",
                "severity": "low",
                "details": "PHP-FPM pool configuration not found in standard locations",
                "fix": None,
            }

        issues = []
        values = {}

        # Parse key settings
        for key in ["pm.max_children", "pm.start_servers", "pm.min_spare_servers",
                     "pm.max_spare_servers", "memory_limit", "max_execution_time"]:
            match = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+)", pool_config, re.MULTILINE)
            if match:
                values[key] = match.group(1).strip()

        # Also check php.ini values for memory_limit and max_execution_time
        if "memory_limit" not in values or "max_execution_time" not in values:
            php_ini_result = self.ssh.execute("php -i 2>/dev/null | grep -E 'memory_limit|max_execution_time'")
            if php_ini_result["exit_code"] == 0:
                for line in php_ini_result["stdout"].splitlines():
                    for key in ["memory_limit", "max_execution_time"]:
                        if key in line and key not in values:
                            parts = line.split("=>")
                            if len(parts) >= 2:
                                values[key] = parts[-1].strip()

        # Evaluate settings
        max_children = values.get("pm.max_children")
        if max_children:
            try:
                mc = int(max_children)
                if mc < 5:
                    issues.append(f"pm.max_children={mc} is too low (recommend >= 5)")
                elif mc > 50:
                    issues.append(f"pm.max_children={mc} is very high (recommend <= 50 for typical sites)")
            except ValueError:
                pass

        # Check spare server ratios
        start = values.get("pm.start_servers")
        min_spare = values.get("pm.min_spare_servers")
        max_spare = values.get("pm.max_spare_servers")
        if start and min_spare and max_spare and max_children:
            try:
                s, mn, mx, mc = int(start), int(min_spare), int(max_spare), int(max_children)
                if s < mn:
                    issues.append(f"pm.start_servers ({s}) < pm.min_spare_servers ({mn})")
                if mx > mc:
                    issues.append(f"pm.max_spare_servers ({mx}) > pm.max_children ({mc})")
                if s > mx:
                    issues.append(f"pm.start_servers ({s}) > pm.max_spare_servers ({mx})")
            except ValueError:
                pass

        mem_limit = values.get("memory_limit", "")
        mem_match = re.match(r"(\d+)", mem_limit)
        if mem_match:
            mem_val = int(mem_match.group(1))
            if "G" in mem_limit:
                mem_val *= 1024
            if mem_val < 256:
                issues.append(f"memory_limit={mem_limit} is low (recommend >= 256M)")

        exec_time = values.get("max_execution_time", "")
        exec_match = re.match(r"(\d+)", exec_time)
        if exec_match and int(exec_match.group(1)) < 30:
            issues.append(f"max_execution_time={exec_time} is low (recommend >= 30)")

        details_parts = [f"Pool config: {pool_path}"]
        for k, v in values.items():
            details_parts.append(f"  {k} = {v}")

        if issues:
            details_parts.append("\nIssues:")
            for issue in issues:
                details_parts.append(f"  - {issue}")

            return {
                "name": name,
                "status": "warning",
                "severity": "medium",
                "details": "\n".join(details_parts),
                "fix": None,
            }

        return self._ok(name, "\n".join(details_parts))

    # ------------------------------------------------------------------ #
    # 7. OPcache
    # ------------------------------------------------------------------ #

    def check_opcache(self):
        name = "check_opcache"
        result = self.ssh.execute("php -r \"echo json_encode(opcache_get_configuration());\" 2>/dev/null")

        if result["exit_code"] != 0 or not result["stdout"].strip():
            # Fallback: check php -i
            result = self.ssh.execute("php -i 2>/dev/null | grep -i opcache")
            if result["exit_code"] != 0 or not result["stdout"].strip():
                return {
                    "name": name,
                    "status": "warning",
                    "severity": "medium",
                    "details": "OPcache does not appear to be installed or enabled",
                    "fix": [
                        {
                            "command": "php -m | grep -i opcache || echo 'Install php-opcache package'",
                            "description": "Check if OPcache module is available",
                            "destructive": False,
                        }
                    ],
                }
            # Parse from php -i output
            issues = []
            info_lines = result["stdout"].strip().splitlines()
            enabled = any("opcache.enable => On" in l or "opcache.enable => 1" in l for l in info_lines)
            if not enabled:
                return {
                    "name": name,
                    "status": "warning",
                    "severity": "medium",
                    "details": "OPcache is installed but not enabled",
                    "fix": [
                        {
                            "command": "echo 'opcache.enable=1' >> $(php -i | grep 'Loaded Configuration' | awk '{print $NF}')",
                            "description": "Enable OPcache in php.ini",
                            "destructive": True,
                        }
                    ],
                }
            return {
                "name": name,
                "status": "info",
                "severity": "low",
                "details": "OPcache enabled (could not retrieve detailed configuration via API)",
                "fix": None,
            }

        try:
            config = json.loads(result["stdout"])
        except (json.JSONDecodeError, ValueError):
            return self._error(name, "Could not parse OPcache configuration JSON")

        directives = config.get("directives", {})
        issues = []
        fix_commands = []

        enabled = directives.get("opcache.enable", False)
        if not enabled:
            return {
                "name": name,
                "status": "warning",
                "severity": "medium",
                "details": "OPcache is not enabled",
                "fix": [
                    {
                        "command": "echo 'opcache.enable=1' >> $(php -i | grep 'Loaded Configuration' | awk '{print $NF}')",
                        "description": "Enable OPcache in php.ini",
                        "destructive": True,
                    }
                ],
            }

        mem = directives.get("opcache.memory_consumption", 0)
        if mem < 128:
            issues.append(f"opcache.memory_consumption={mem}MB (recommend >= 128)")
            fix_commands.append({
                "command": "sed -i 's/^opcache.memory_consumption=.*/opcache.memory_consumption=128/' $(php -i 2>/dev/null | grep 'Loaded Configuration' | awk '{print $NF}')",
                "description": "Set opcache.memory_consumption to 128MB",
                "destructive": True,
            })

        max_files = directives.get("opcache.max_accelerated_files", 0)
        if max_files < 10000:
            issues.append(f"opcache.max_accelerated_files={max_files} (recommend >= 10000)")
            fix_commands.append({
                "command": "sed -i 's/^opcache.max_accelerated_files=.*/opcache.max_accelerated_files=10000/' $(php -i 2>/dev/null | grep 'Loaded Configuration' | awk '{print $NF}')",
                "description": "Set opcache.max_accelerated_files to 10000",
                "destructive": True,
            })

        revalidate = directives.get("opcache.revalidate_freq", -1)
        details_parts = [
            "OPcache is enabled",
            f"  memory_consumption: {mem}MB",
            f"  max_accelerated_files: {max_files}",
            f"  revalidate_freq: {revalidate}s",
        ]

        if issues:
            details_parts.append("\nRecommendations:")
            for issue in issues:
                details_parts.append(f"  - {issue}")
            return {
                "name": name,
                "status": "warning",
                "severity": "low",
                "details": "\n".join(details_parts),
                "fix": fix_commands,
            }

        return self._ok(name, "\n".join(details_parts))

    # ------------------------------------------------------------------ #
    # 8. Object cache (Redis / Memcached)
    # ------------------------------------------------------------------ #

    def check_object_cache(self):
        name = "check_object_cache"
        oc_path = f"{self.site_path}/wp-content/object-cache.php"

        result = self.ssh.execute(f"test -f {oc_path} && echo 'exists' || echo 'missing'")
        has_drop_in = result["stdout"].strip() == "exists"

        redis_result = self.ssh.execute("systemctl is-active redis-server 2>/dev/null || systemctl is-active redis 2>/dev/null")
        redis_running = redis_result["exit_code"] == 0 and "active" in redis_result["stdout"]

        memcached_result = self.ssh.execute("systemctl is-active memcached 2>/dev/null")
        memcached_running = memcached_result["exit_code"] == 0 and "active" in memcached_result["stdout"]

        details_parts = []
        if has_drop_in:
            details_parts.append("object-cache.php drop-in is present")
            # Try to identify which backend
            oc_content = self.ssh.execute(f"head -30 {oc_path}")
            if oc_content["exit_code"] == 0:
                content = oc_content["stdout"].lower()
                if "redis" in content:
                    details_parts.append("Backend: Redis")
                elif "memcache" in content:
                    details_parts.append("Backend: Memcached")
                else:
                    details_parts.append("Backend: Unknown (check object-cache.php)")
        else:
            details_parts.append("No object-cache.php drop-in found")

        details_parts.append(f"Redis service: {'running' if redis_running else 'not running / not installed'}")
        details_parts.append(f"Memcached service: {'running' if memcached_running else 'not running / not installed'}")

        if not has_drop_in and not redis_running and not memcached_running:
            return {
                "name": name,
                "status": "warning",
                "severity": "medium",
                "details": "\n".join(details_parts) + "\n\nNo persistent object cache configured — recommended for WordPress performance",
                "fix": [
                    {
                        "command": "apt-get install -y redis-server 2>/dev/null || yum install -y redis 2>/dev/null",
                        "description": "Install Redis server",
                        "destructive": True,
                    },
                    {
                        "command": f"{self._detect_wp_cli() or 'wp'} plugin install redis-cache --activate --path={self.site_path}",
                        "description": "Install and activate Redis Object Cache plugin",
                        "destructive": True,
                    },
                ],
            }

        if has_drop_in and (redis_running or memcached_running):
            return self._ok(name, "\n".join(details_parts))

        return {
            "name": name,
            "status": "info",
            "severity": "low",
            "details": "\n".join(details_parts),
            "fix": None,
        }

    # ------------------------------------------------------------------ #
    # 9. WP-Cron vs system cron
    # ------------------------------------------------------------------ #

    def check_wp_cron(self):
        name = "check_wp_cron"
        result = self.ssh.execute(f"cat {self.site_path}/wp-config.php")
        if result["exit_code"] != 0:
            return self._error(name, f"Cannot read wp-config.php: {result['stderr']}")

        content = result["stdout"]
        disable_match = re.search(
            r"define\s*\(\s*['\"]DISABLE_WP_CRON['\"]\s*,\s*(true|false)\s*\)", content, re.IGNORECASE
        )
        wp_cron_disabled = disable_match and disable_match.group(1).lower() == "true"

        # Check if system cron has a wp-cron entry
        cron_result = self.ssh.execute(
            "crontab -l 2>/dev/null | grep -i wp-cron; "
            "cat /etc/cron.d/* 2>/dev/null | grep -i wp-cron; "
            "cat /var/spool/cron/* 2>/dev/null | grep -i wp-cron"
        )
        has_system_cron = bool(cron_result["stdout"].strip())

        if wp_cron_disabled and has_system_cron:
            return self._ok(name, "WP-Cron disabled, system cron configured (optimal)")

        if wp_cron_disabled and not has_system_cron:
            return {
                "name": name,
                "status": "critical",
                "severity": "high",
                "details": "WP-Cron is disabled but no system cron entry found — scheduled tasks will NOT run",
                "fix": [
                    {
                        "command": (
                            f"(crontab -l 2>/dev/null; echo '*/5 * * * * curl -s {self.site_path}/wp-cron.php >/dev/null 2>&1') | crontab -"
                        ),
                        "description": "Add wp-cron system cron entry (every 5 minutes)",
                        "destructive": True,
                    }
                ],
            }

        if not wp_cron_disabled:
            cli = self._detect_wp_cli() or "wp"
            return {
                "name": name,
                "status": "warning",
                "severity": "low",
                "details": "WP-Cron is using default mode (triggered on page load) — system cron is more reliable and performant",
                "fix": [
                    {
                        "command": (
                            f"sed -i \"/\\/\\* That's all, stop editing/i "
                            f"define('DISABLE_WP_CRON', true);\" "
                            f"{self.site_path}/wp-config.php"
                        ),
                        "description": "Add DISABLE_WP_CRON to wp-config.php",
                        "destructive": True,
                    },
                    {
                        "command": (
                            f"(crontab -l 2>/dev/null; echo '*/5 * * * * {cli} cron event run --due-now "
                            f"--path={self.site_path} >/dev/null 2>&1') | crontab -"
                        ),
                        "description": "Add system cron entry for WP-Cron (every 5 minutes via wp-cli)",
                        "destructive": True,
                    },
                ],
            }

    # ------------------------------------------------------------------ #
    # 10. Database optimization
    # ------------------------------------------------------------------ #

    def check_database_optimization(self):
        name = "check_database_optimization"
        cli = self._detect_wp_cli()
        if cli is None:
            return self._wp_cli_missing_result(name)

        details_parts = []
        issues = []
        fix_actions = []

        # Database size
        size_result = self._wp("db size --size_format=mb --format=csv")
        if size_result and size_result["exit_code"] == 0:
            details_parts.append(f"Database size: {size_result['stdout'].strip()}")

        # Autoloaded options size
        autoload_result = self._wp(
            "db query \"SELECT SUM(LENGTH(option_value)) as total FROM wp_options WHERE autoload='yes'\" --skip-column-names"
        )
        if autoload_result and autoload_result["exit_code"] == 0:
            try:
                autoload_bytes = int(autoload_result["stdout"].strip())
                autoload_mb = autoload_bytes / (1024 * 1024)
                details_parts.append(f"Autoloaded options size: {autoload_mb:.2f}MB")
                if autoload_mb > 1.0:
                    issues.append(
                        f"Autoloaded options are {autoload_mb:.2f}MB (> 1MB) — "
                        "this is a common WordPress performance killer"
                    )
                    fix_actions.append({
                        "command": (
                            f"{cli} db query \"SELECT option_name, LENGTH(option_value) as size "
                            f"FROM wp_options WHERE autoload='yes' ORDER BY size DESC LIMIT 20\" "
                            f"--path={self.site_path}"
                        ),
                        "description": "Show top 20 largest autoloaded options (for manual review)",
                        "destructive": False,
                    })
            except (ValueError, TypeError):
                pass

        # Post revisions count
        revisions_result = self._wp(
            "db query \"SELECT COUNT(*) FROM wp_posts WHERE post_type='revision'\" --skip-column-names"
        )
        if revisions_result and revisions_result["exit_code"] == 0:
            try:
                revision_count = int(revisions_result["stdout"].strip())
                details_parts.append(f"Post revisions: {revision_count}")
                if revision_count > 500:
                    issues.append(f"{revision_count} post revisions — consider cleaning old revisions")
                    fix_actions.append({
                        "command": (
                            f"{cli} db query \"DELETE FROM wp_posts WHERE post_type='revision'\" "
                            f"--path={self.site_path}"
                        ),
                        "description": "Delete all post revisions",
                        "destructive": True,
                    })
                    fix_actions.append({
                        "command": f"{cli} db optimize --path={self.site_path}",
                        "description": "Optimize database tables",
                        "destructive": True,
                    })
            except (ValueError, TypeError):
                pass

        if issues:
            details_parts.append("\nIssues:")
            for issue in issues:
                details_parts.append(f"  - {issue}")
            return {
                "name": name,
                "status": "warning",
                "severity": "medium",
                "details": "\n".join(details_parts),
                "fix": fix_actions,
            }

        return self._ok(name, "\n".join(details_parts))

    # ------------------------------------------------------------------ #
    # 11. File permissions
    # ------------------------------------------------------------------ #

    def check_file_permissions(self):
        name = "check_file_permissions"
        issues = []
        fix_actions = []
        sp = self.site_path

        # Check wp-config.php permissions
        result = self.ssh.execute(f"stat -c '%a' {sp}/wp-config.php 2>/dev/null")
        if result["exit_code"] == 0:
            perms = result["stdout"].strip()
            if perms not in ("600", "640"):
                issues.append(f"wp-config.php has permissions {perms} (should be 600 or 640)")
                fix_actions.append({
                    "command": f"chmod 640 {sp}/wp-config.php",
                    "description": "Set wp-config.php to 640",
                    "destructive": True,
                })

        # Check wp-content permissions
        result = self.ssh.execute(f"stat -c '%a' {sp}/wp-content 2>/dev/null")
        if result["exit_code"] == 0:
            perms = result["stdout"].strip()
            if perms != "755":
                issues.append(f"wp-content has permissions {perms} (should be 755)")
                fix_actions.append({
                    "command": f"chmod 755 {sp}/wp-content",
                    "description": "Set wp-content to 755",
                    "destructive": True,
                })

        # Check uploads permissions
        result = self.ssh.execute(f"stat -c '%a' {sp}/wp-content/uploads 2>/dev/null")
        if result["exit_code"] == 0:
            perms = result["stdout"].strip()
            if perms != "755":
                issues.append(f"wp-content/uploads has permissions {perms} (should be 755)")
                fix_actions.append({
                    "command": f"chmod 755 {sp}/wp-content/uploads",
                    "description": "Set wp-content/uploads to 755",
                    "destructive": True,
                })

        # Check for world-writable files (limit scan depth and count)
        result = self.ssh.execute(
            f"find {sp} -maxdepth 3 -perm -o=w -type f 2>/dev/null | head -20"
        )
        if result["exit_code"] == 0 and result["stdout"].strip():
            world_writable = [f.strip() for f in result["stdout"].strip().splitlines() if f.strip()]
            if world_writable:
                issues.append(f"Found {len(world_writable)} world-writable file(s) (showing up to 20)")
                for f in world_writable[:5]:
                    issues.append(f"  {f}")
                if len(world_writable) > 5:
                    issues.append(f"  ... and {len(world_writable) - 5} more")
                fix_actions.append({
                    "command": f"find {sp} -maxdepth 3 -perm -o=w -type f -exec chmod o-w {{}} +",
                    "description": "Remove world-writable permission from all files",
                    "destructive": True,
                })

        if issues:
            return {
                "name": name,
                "status": "warning",
                "severity": "high",
                "details": "File permission issues:\n" + "\n".join(f"  - {i}" for i in issues),
                "fix": fix_actions,
            }

        return self._ok(name, "File permissions look correct")

    # ------------------------------------------------------------------ #
    # 12. SSL and URLs
    # ------------------------------------------------------------------ #

    def check_ssl_and_urls(self):
        name = "check_ssl_and_urls"
        issues = []
        details_parts = []

        # Check siteurl and home
        cli = self._detect_wp_cli()
        siteurl = None
        homeurl = None

        if cli:
            site_result = self._wp("option get siteurl")
            if site_result and site_result["exit_code"] == 0:
                siteurl = site_result["stdout"].strip()
                details_parts.append(f"Site URL: {siteurl}")

            home_result = self._wp("option get home")
            if home_result and home_result["exit_code"] == 0:
                homeurl = home_result["stdout"].strip()
                details_parts.append(f"Home URL: {homeurl}")

            if siteurl and not siteurl.startswith("https://"):
                issues.append(f"Site URL does not use HTTPS: {siteurl}")
            if homeurl and not homeurl.startswith("https://"):
                issues.append(f"Home URL does not use HTTPS: {homeurl}")
        else:
            details_parts.append("wp-cli not available — cannot check siteurl/home options")

        # Check .htaccess for HTTPS redirect
        htaccess_result = self.ssh.execute(f"cat {self.site_path}/.htaccess 2>/dev/null")
        if htaccess_result["exit_code"] == 0:
            htaccess = htaccess_result["stdout"]
            has_redirect = bool(
                re.search(r"RewriteRule.*https://", htaccess, re.IGNORECASE)
                or re.search(r"Header.*Strict-Transport-Security", htaccess, re.IGNORECASE)
            )
            if has_redirect:
                details_parts.append("HTTPS redirect found in .htaccess")
            else:
                issues.append("No HTTPS redirect found in .htaccess")
        else:
            details_parts.append(".htaccess not found or not readable")

        if issues:
            return {
                "name": name,
                "status": "warning",
                "severity": "high",
                "details": "\n".join(details_parts) + "\n\nIssues:\n" + "\n".join(f"  - {i}" for i in issues),
                "fix": None,
            }

        return self._ok(name, "\n".join(details_parts))

    # ------------------------------------------------------------------ #
    # 13. Page speed factors
    # ------------------------------------------------------------------ #

    def check_page_speed_factors(self):
        name = "check_page_speed_factors"
        issues = []
        details_parts = []

        # Count active plugins
        cli = self._detect_wp_cli()
        if cli:
            plugin_result = self._wp("plugin list --status=active --format=json")
            if plugin_result and plugin_result["exit_code"] == 0:
                try:
                    active_plugins = json.loads(plugin_result["stdout"])
                    count = len(active_plugins)
                    details_parts.append(f"Active plugins: {count}")
                    if count > 20:
                        issues.append(f"{count} active plugins — consider reducing (> 20 impacts performance)")
                except (json.JSONDecodeError, ValueError):
                    pass

            # Check for caching plugin
            cache_plugins = [
                "wp-super-cache", "w3-total-cache", "litespeed-cache",
                "wp-rocket", "wp-fastest-cache", "sg-cachepress",
            ]
            cache_found = False
            if plugin_result and plugin_result["exit_code"] == 0:
                try:
                    active_plugins = json.loads(plugin_result["stdout"])
                    active_names = [p.get("name", "") for p in active_plugins]
                    found = [n for n in active_names if n in cache_plugins]
                    if found:
                        details_parts.append(f"Caching plugin: {', '.join(found)}")
                        cache_found = True
                except (json.JSONDecodeError, ValueError):
                    pass
            if not cache_found:
                issues.append("No known caching plugin detected — strongly recommended")
        else:
            details_parts.append("wp-cli not available — limited speed check")

        # Check GZIP / Brotli
        gzip_result = self.ssh.execute(
            "grep -rl 'mod_deflate\\|AddOutputFilterByType.*DEFLATE\\|gzip' "
            f"{self.site_path}/.htaccess /etc/apache2/conf-enabled/ /etc/nginx/nginx.conf "
            "/etc/nginx/conf.d/ 2>/dev/null | head -3"
        )
        if gzip_result["stdout"].strip():
            details_parts.append("GZIP/compression: configured")
        else:
            issues.append("No GZIP/Brotli compression detected in web server config")

        # Check browser caching headers
        cache_header_result = self.ssh.execute(
            f"grep -i 'Expires\\|Cache-Control\\|mod_expires' {self.site_path}/.htaccess 2>/dev/null | head -5"
        )
        if cache_header_result["stdout"].strip():
            details_parts.append("Browser caching headers: configured")
        else:
            issues.append("No browser caching headers (Expires/Cache-Control) found in .htaccess")

        # Elementor CSS directory size
        elementor_css_result = self.ssh.execute(
            f"du -sh {self.site_path}/wp-content/uploads/elementor/css/ 2>/dev/null"
        )
        if elementor_css_result["exit_code"] == 0 and elementor_css_result["stdout"].strip():
            css_size = elementor_css_result["stdout"].strip().split()[0]
            details_parts.append(f"Elementor CSS cache size: {css_size}")

        # Check Elementor optimization settings (widget loading)
        elementor_opt_result = self.ssh.execute(
            f"grep -r 'elementor_experiment\\|improved_asset_loading\\|e_optimized_assets_loading' "
            f"{self.site_path}/wp-content/uploads/elementor/ 2>/dev/null; "
            f"test -d {self.site_path}/wp-content/plugins/elementor && echo 'elementor_installed'"
        )
        if "elementor_installed" in elementor_opt_result["stdout"]:
            # Check if optimized asset loading is enabled via DB
            if cli:
                asset_result = self._wp(
                    "option get elementor_experiment-e_optimized_assets_loading 2>/dev/null"
                )
                if asset_result and asset_result["exit_code"] == 0:
                    val = asset_result["stdout"].strip()
                    if val in ("active", "default"):
                        details_parts.append(f"Elementor optimized asset loading: {val}")
                    else:
                        issues.append("Elementor 'Improved Asset Loading' experiment is not active")

        if issues:
            details_parts.append("\nSpeed issues:")
            for issue in issues:
                details_parts.append(f"  - {issue}")
            return {
                "name": name,
                "status": "warning",
                "severity": "medium",
                "details": "\n".join(details_parts),
                "fix": None,
            }

        return self._ok(name, "\n".join(details_parts))

    # ------------------------------------------------------------------ #
    # 14. Security basics
    # ------------------------------------------------------------------ #

    def check_security_basics(self):
        name = "check_security_basics"
        issues = []
        details_parts = []
        fix_actions = []
        sp = self.site_path

        # Check xmlrpc.php accessibility
        xmlrpc_result = self.ssh.execute(f"test -f {sp}/xmlrpc.php && echo 'exists'")
        if xmlrpc_result["stdout"].strip() == "exists":
            # Check if blocked in .htaccess
            htaccess_result = self.ssh.execute(f"grep -i xmlrpc {sp}/.htaccess 2>/dev/null")
            if not htaccess_result["stdout"].strip():
                issues.append("xmlrpc.php is accessible and not blocked (common attack vector)")
                fix_actions.append({
                    "command": (
                        f"cat >> {sp}/.htaccess << 'HTEOF'\n"
                        "\n# Block xmlrpc.php\n"
                        "<Files xmlrpc.php>\n"
                        "  Order Deny,Allow\n"
                        "  Deny from all\n"
                        "</Files>\n"
                        "HTEOF"
                    ),
                    "description": "Block xmlrpc.php via .htaccess",
                    "destructive": True,
                })
            else:
                details_parts.append("xmlrpc.php: blocked in .htaccess")

        # Check directory listing
        htaccess_result = self.ssh.execute(f"grep -i 'Options.*-Indexes' {sp}/.htaccess 2>/dev/null")
        if htaccess_result["stdout"].strip():
            details_parts.append("Directory listing: disabled")
        else:
            issues.append("Directory listing may not be disabled (no 'Options -Indexes' in .htaccess)")
            fix_actions.append({
                "command": f"sed -i '1i Options -Indexes' {sp}/.htaccess",
                "description": "Disable directory listing in .htaccess",
                "destructive": True,
            })

        # Check if wp-config.php is web-accessible (should be one level up or protected)
        wp_config_result = self.ssh.execute(
            f"grep -i 'wp-config' {sp}/.htaccess 2>/dev/null"
        )
        if wp_config_result["stdout"].strip():
            details_parts.append("wp-config.php: protected in .htaccess")
        else:
            issues.append("wp-config.php is not explicitly protected in .htaccess")
            fix_actions.append({
                "command": (
                    f"cat >> {sp}/.htaccess << 'HTEOF'\n"
                    "\n# Protect wp-config.php\n"
                    "<Files wp-config.php>\n"
                    "  Order Allow,Deny\n"
                    "  Deny from all\n"
                    "</Files>\n"
                    "HTEOF"
                ),
                "description": "Protect wp-config.php via .htaccess",
                "destructive": True,
            })

        # Check DISALLOW_FILE_EDIT
        config_result = self.ssh.execute(f"cat {sp}/wp-config.php 2>/dev/null")
        if config_result["exit_code"] == 0:
            content = config_result["stdout"]
            edit_match = re.search(
                r"define\s*\(\s*['\"]DISALLOW_FILE_EDIT['\"]\s*,\s*(true|false)\s*\)",
                content, re.IGNORECASE,
            )
            if edit_match and edit_match.group(1).lower() == "true":
                details_parts.append("File editing: disabled (DISALLOW_FILE_EDIT)")
            else:
                issues.append("DISALLOW_FILE_EDIT is not set to true — dashboard file editing is a security risk")
                fix_actions.append({
                    "command": (
                        f"sed -i \"/\\/\\* That's all, stop editing/i "
                        f"define('DISALLOW_FILE_EDIT', true);\" "
                        f"{sp}/wp-config.php"
                    ),
                    "description": "Add DISALLOW_FILE_EDIT to wp-config.php",
                    "destructive": True,
                })

        # Check brute force protection
        cli = self._detect_wp_cli()
        brute_force_plugins = [
            "limit-login-attempts", "limit-login-attempts-reloaded",
            "wordfence", "sucuri-scanner", "all-in-one-wp-security-and-firewall",
            "ithemes-security", "loginizer",
        ]
        if cli:
            plugin_result = self._wp("plugin list --status=active --format=json")
            if plugin_result and plugin_result["exit_code"] == 0:
                try:
                    active = json.loads(plugin_result["stdout"])
                    active_names = [p.get("name", "") for p in active]
                    found = [n for n in active_names if n in brute_force_plugins]
                    if found:
                        details_parts.append(f"Brute force protection: {', '.join(found)}")
                    else:
                        issues.append("No brute force protection plugin detected (consider limit-login-attempts-reloaded or Wordfence)")
                except (json.JSONDecodeError, ValueError):
                    pass

        if issues:
            details_parts.append(f"\nSecurity issues ({len(issues)}):")
            for issue in issues:
                details_parts.append(f"  - {issue}")

            status = "critical" if len(issues) >= 3 else "warning"
            return {
                "name": name,
                "status": status,
                "severity": "high",
                "details": "\n".join(details_parts),
                "fix": fix_actions if fix_actions else None,
            }

        return self._ok(name, "\n".join(details_parts))

    # ------------------------------------------------------------------ #
    # 15. Elementor health
    # ------------------------------------------------------------------ #

    def check_elementor_health(self):
        name = "check_elementor_health"
        sp = self.site_path
        elementor_dir = f"{sp}/wp-content/plugins/elementor"

        # Check if Elementor is installed
        result = self.ssh.execute(f"test -d {elementor_dir} && echo 'installed'")
        if result["stdout"].strip() != "installed":
            return {
                "name": name,
                "status": "info",
                "severity": "low",
                "details": "Elementor is not installed",
                "fix": None,
            }

        details_parts = []
        issues = []
        cli = self._detect_wp_cli()

        # Check Elementor version
        version_result = self.ssh.execute(
            f"grep -m1 'Version:' {elementor_dir}/elementor.php 2>/dev/null || "
            f"grep -m1 '\"Version\"' {elementor_dir}/elementor.php 2>/dev/null"
        )
        if version_result["exit_code"] == 0 and version_result["stdout"].strip():
            ver_match = re.search(r"Version:\s*(\S+)", version_result["stdout"])
            if ver_match:
                details_parts.append(f"Elementor version: {ver_match.group(1)}")

        # Check CSS print method (external file vs inline)
        if cli:
            css_method_result = self._wp("option get elementor_css_print_method 2>/dev/null")
            if css_method_result and css_method_result["exit_code"] == 0:
                method = css_method_result["stdout"].strip()
                details_parts.append(f"CSS print method: {method}")
                if method == "internal":
                    issues.append(
                        "Elementor CSS is set to 'internal' (inline) — "
                        "'external' is better for caching"
                    )

            # Check Improved Asset Loading experiment
            asset_loading_result = self._wp(
                "option get elementor_experiment-e_optimized_assets_loading 2>/dev/null"
            )
            if asset_loading_result and asset_loading_result["exit_code"] == 0:
                val = asset_loading_result["stdout"].strip()
                details_parts.append(f"Improved Asset Loading: {val}")
                if val not in ("active", "default"):
                    issues.append("'Improved Asset Loading' experiment is not active (recommended for Elementor 3.x+)")

            # Check DOM output optimization
            dom_opt_result = self._wp(
                "option get elementor_experiment-e_dom_optimization 2>/dev/null"
            )
            if dom_opt_result and dom_opt_result["exit_code"] == 0:
                val = dom_opt_result["stdout"].strip()
                details_parts.append(f"DOM optimization: {val}")
                if val not in ("active", "default"):
                    issues.append("'DOM Output Optimization' experiment is not active")

            # Report all experiments status
            experiments_result = self._wp(
                "db query \"SELECT option_name, option_value FROM wp_options WHERE option_name LIKE 'elementor_experiment-%'\" "
                "--skip-column-names 2>/dev/null"
            )
            if experiments_result and experiments_result["exit_code"] == 0 and experiments_result["stdout"].strip():
                details_parts.append("\nElementor experiments:")
                for line in experiments_result["stdout"].strip().splitlines():
                    parts = line.strip().split("\t")
                    if len(parts) >= 2:
                        exp_name = parts[0].replace("elementor_experiment-", "")
                        exp_val = parts[1]
                        details_parts.append(f"  {exp_name}: {exp_val}")
        else:
            details_parts.append("wp-cli not available — limited Elementor checks")

        if issues:
            details_parts.append("\nRecommendations:")
            for issue in issues:
                details_parts.append(f"  - {issue}")
            return {
                "name": name,
                "status": "warning",
                "severity": "low",
                "details": "\n".join(details_parts),
                "fix": None,
            }

        return self._ok(name, "\n".join(details_parts))

    # ------------------------------------------------------------------ #
    # Run all checks
    # ------------------------------------------------------------------ #

    def run_all(self):
        """Run all 15 checks in parallel where safe, return list of results."""
        results = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(check): check.__name__ for check in self._checks}
            for future in as_completed(futures):
                check_name = futures[future]
                try:
                    result = future.result()
                    if result is not None:
                        results.append(result)
                except Exception as e:
                    logger.error(f"WordPress check {check_name} failed: {e}")
                    results.append({
                        "name": check_name,
                        "status": "error",
                        "severity": "low",
                        "details": str(e),
                        "fix": None,
                    })
        return results
