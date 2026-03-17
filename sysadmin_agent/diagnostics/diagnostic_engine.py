import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class DiagnosticEngine:
    def __init__(self, ssh_manager, approval_manager, rollback_manager):
        self.ssh = ssh_manager
        self.approval = approval_manager
        self.rollback = rollback_manager
        self._checks = [
            self.check_disk_usage,
            self.check_memory_usage,
            self.check_cpu_load,
            self.check_zombie_processes,
            self.check_failed_services,
            self.check_dns_resolution,
            self.check_ntp_sync,
            self.check_open_ports,
            self.check_disk_io_wait,
            self.check_swap_usage,
            self.check_oom_kills,
            self.check_ssl_certificates,
            self.check_security_updates,
            self.check_firewall,
        ]

    def run_all(self):
        results = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(check): check.__name__ for check in self._checks}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"Check {name} failed: {e}")
                    results.append({
                        "name": name,
                        "status": "error",
                        "severity": "low",
                        "details": str(e),
                        "fix": None,
                    })
        return results

    def check_disk_usage(self):
        result = self.ssh.execute("df -Ph --exclude-type=tmpfs --exclude-type=devtmpfs")
        if result["exit_code"] != 0:
            return self._error("check_disk_usage", result["stderr"])

        alerts = []
        for line in result["stdout"].strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
            usage_str = parts[4].rstrip("%")
            try:
                usage = int(usage_str)
            except ValueError:
                continue
            mount = parts[5]
            if usage >= 90:
                alerts.append(f"{mount} at {usage}%")

        if alerts:
            return {
                "name": "check_disk_usage",
                "status": "critical",
                "severity": "high",
                "details": f"High disk usage: {'; '.join(alerts)}",
                "fix": [
                    {"command": "journalctl --vacuum-size=100M", "description": "Clear journal logs to 100MB", "destructive": True},
                    {"command": "apt-get clean 2>/dev/null; yum clean all 2>/dev/null; true", "description": "Clear package manager cache", "destructive": True},
                    {"command": "find /tmp -type f -atime +7 -delete", "description": "Remove temp files older than 7 days", "destructive": True},
                ],
            }

        return self._ok("check_disk_usage", "All filesystems below 90% usage")

    def check_memory_usage(self):
        result = self.ssh.execute("free -m")
        if result["exit_code"] != 0:
            return self._error("check_memory_usage", result["stderr"])

        lines = result["stdout"].strip().splitlines()
        for line in lines:
            if line.startswith("Mem:"):
                parts = line.split()
                total = int(parts[1])
                available = int(parts[6]) if len(parts) >= 7 else int(parts[3])
                used_pct = ((total - available) / total) * 100 if total > 0 else 0
                break
        else:
            return self._error("check_memory_usage", "Could not parse memory info")

        if used_pct >= 85:
            return {
                "name": "check_memory_usage",
                "status": "warning",
                "severity": "medium",
                "details": f"Memory usage at {used_pct:.1f}% ({total}MB total, {available}MB available)",
                "fix": [
                    {"command": "ps aux --sort=-%mem | head -20", "description": "Show top memory-consuming processes", "destructive": False},
                    {"command": "sync && echo 3 > /proc/sys/vm/drop_caches", "description": "Clear page cache", "destructive": True},
                ],
            }

        return self._ok("check_memory_usage", f"Memory usage at {used_pct:.1f}%")

    def check_cpu_load(self):
        result = self.ssh.execute("nproc && cat /proc/loadavg")
        if result["exit_code"] != 0:
            return self._error("check_cpu_load", result["stderr"])

        lines = result["stdout"].strip().splitlines()
        cpu_count = int(lines[0])
        load_1, load_5, load_15 = [float(x) for x in lines[1].split()[:3]]
        ratio = load_1 / cpu_count if cpu_count > 0 else load_1

        if ratio >= 2.0:
            status = "critical"
            severity = "high"
        elif ratio >= 1.0:
            status = "warning"
            severity = "medium"
        else:
            status = "ok"
            severity = "low"

        return {
            "name": "check_cpu_load",
            "status": status,
            "severity": severity,
            "details": f"Load avg: {load_1}/{load_5}/{load_15} ({cpu_count} CPUs, ratio {ratio:.2f})",
            "fix": None,
        }

    def check_zombie_processes(self):
        result = self.ssh.execute("ps aux | awk '$8 ~ /Z/ {print $2, $11}'")
        if result["exit_code"] != 0:
            return self._error("check_zombie_processes", result["stderr"])

        zombies = [line.strip() for line in result["stdout"].strip().splitlines() if line.strip()]

        if zombies:
            parent_result = self.ssh.execute(
                "ps -eo pid,ppid,stat | awk '$3 ~ /Z/ {print $2}' | sort -u"
            )
            parent_pids = [p.strip() for p in parent_result["stdout"].strip().splitlines() if p.strip()]
            fix_actions = [
                {"command": f"kill -9 {ppid}", "description": f"Kill parent process {ppid} of zombie", "destructive": True}
                for ppid in parent_pids
            ]
            return {
                "name": "check_zombie_processes",
                "status": "warning",
                "severity": "medium",
                "details": f"Found {len(zombies)} zombie process(es)",
                "fix": fix_actions if fix_actions else None,
            }

        return self._ok("check_zombie_processes", "No zombie processes found")

    def check_failed_services(self):
        result = self.ssh.execute("systemctl --failed --no-legend --no-pager")
        if result["exit_code"] != 0:
            return self._error("check_failed_services", result["stderr"])

        failed = []
        for line in result["stdout"].strip().splitlines():
            line = line.strip()
            if not line:
                continue
            unit = line.split()[0]
            failed.append(unit)

        if failed:
            fix_actions = [
                {"command": f"systemctl restart {unit}", "description": f"Restart {unit}", "destructive": True}
                for unit in failed
            ]
            return {
                "name": "check_failed_services",
                "status": "critical",
                "severity": "high",
                "details": f"Failed services: {', '.join(failed)}",
                "fix": fix_actions,
            }

        return self._ok("check_failed_services", "No failed services")

    def check_dns_resolution(self):
        result = self.ssh.execute("host -W 5 google.com")
        if result["exit_code"] != 0:
            return {
                "name": "check_dns_resolution",
                "status": "critical",
                "severity": "high",
                "details": f"DNS resolution failed: {result['stderr'].strip() or result['stdout'].strip()}",
                "fix": [
                    {
                        "command": "echo 'nameserver 8.8.8.8\nnameserver 8.8.4.4' >> /etc/resolv.conf",
                        "description": "Add Google DNS servers to resolv.conf",
                        "destructive": True,
                    }
                ],
            }

        return self._ok("check_dns_resolution", "DNS resolution working")

    def check_ntp_sync(self):
        result = self.ssh.execute("timedatectl show --property=NTPSynchronized --value 2>/dev/null || timedatectl status")
        if result["exit_code"] != 0:
            return self._error("check_ntp_sync", result["stderr"])

        output = result["stdout"].strip()
        synced = "yes" in output.lower() and "ntp" in result["stdout"].lower() or output == "yes"

        if not synced:
            return {
                "name": "check_ntp_sync",
                "status": "warning",
                "severity": "medium",
                "details": "NTP is not synchronized",
                "fix": [
                    {"command": "timedatectl set-ntp true", "description": "Enable NTP synchronization", "destructive": True},
                ],
            }

        return self._ok("check_ntp_sync", "NTP synchronized")

    def check_open_ports(self):
        result = self.ssh.execute("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null")
        if result["exit_code"] != 0:
            return self._error("check_open_ports", result["stderr"])

        return {
            "name": "check_open_ports",
            "status": "info",
            "severity": "low",
            "details": result["stdout"].strip(),
            "fix": None,
        }

    def check_disk_io_wait(self):
        result = self.ssh.execute("awk '{print $5}' /proc/stat | head -1")
        if result["exit_code"] != 0:
            result = self.ssh.execute("iostat -c 1 2 2>/dev/null | tail -1")
            if result["exit_code"] != 0:
                return self._error("check_disk_io_wait", "Could not determine I/O wait")

        vmstat_result = self.ssh.execute("vmstat 1 2 | tail -1")
        if vmstat_result["exit_code"] != 0:
            return self._error("check_disk_io_wait", vmstat_result["stderr"])

        parts = vmstat_result["stdout"].strip().split()
        try:
            iowait = int(parts[15]) if len(parts) > 15 else int(parts[-1])
        except (ValueError, IndexError):
            return self._error("check_disk_io_wait", f"Could not parse vmstat output: {vmstat_result['stdout']}")

        if iowait >= 20:
            status = "critical"
            severity = "high"
        elif iowait >= 10:
            status = "warning"
            severity = "medium"
        else:
            status = "ok"
            severity = "low"

        return {
            "name": "check_disk_io_wait",
            "status": status,
            "severity": severity,
            "details": f"I/O wait at {iowait}%",
            "fix": None,
        }

    def check_swap_usage(self):
        result = self.ssh.execute("free -m | grep Swap")
        if result["exit_code"] != 0:
            return self._error("check_swap_usage", result["stderr"])

        parts = result["stdout"].strip().split()
        if len(parts) < 3:
            return self._ok("check_swap_usage", "No swap configured")

        total = int(parts[1])
        used = int(parts[2])

        if total == 0:
            return self._ok("check_swap_usage", "No swap configured")

        pct = (used / total) * 100

        if pct >= 80:
            return {
                "name": "check_swap_usage",
                "status": "warning",
                "severity": "medium",
                "details": f"Swap usage at {pct:.1f}% ({used}MB/{total}MB)",
                "fix": None,
            }

        return self._ok("check_swap_usage", f"Swap usage at {pct:.1f}%")

    def check_oom_kills(self):
        result = self.ssh.execute(
            "dmesg -T 2>/dev/null | grep -i 'out of memory\\|oom-killer' | tail -5; "
            "journalctl -k --no-pager -g 'Out of memory|oom-killer' 2>/dev/null | tail -5"
        )

        output = result["stdout"].strip()
        if output:
            lines = [l for l in output.splitlines() if l.strip()]
            return {
                "name": "check_oom_kills",
                "status": "info",
                "severity": "medium",
                "details": f"Found {len(lines)} OOM event(s):\n" + "\n".join(lines),
                "fix": None,
            }

        return self._ok("check_oom_kills", "No OOM kills detected")

    def check_ssl_certificates(self):
        result = self.ssh.execute("certbot certificates 2>/dev/null")
        if result["exit_code"] != 0 or "No certificates found" in result["stdout"]:
            return {
                "name": "check_ssl_certificates",
                "status": "info",
                "severity": "low",
                "details": "No certbot certificates found or certbot not installed",
                "fix": None,
            }

        expiry_info = []
        lines = result["stdout"].splitlines()
        current_domain = None
        for line in lines:
            line = line.strip()
            if line.startswith("Domains:"):
                current_domain = line.split(":", 1)[1].strip()
            elif line.startswith("Expiry Date:") and current_domain:
                date_str = line.split(":", 1)[1].strip().split(" ")[0:3]
                try:
                    expiry = datetime.strptime(" ".join(date_str), "%Y-%m-%d %H:%M:%S%z")
                    days_left = (expiry - datetime.now(timezone.utc)).days
                    expiry_info.append(f"{current_domain}: {days_left} days left")
                except (ValueError, IndexError):
                    expiry_info.append(f"{current_domain}: could not parse expiry")
                current_domain = None

        status = "info"
        severity = "low"
        for info in expiry_info:
            match = re.search(r"(\d+) days left", info)
            if match and int(match.group(1)) < 30:
                status = "warning"
                severity = "medium"

        return {
            "name": "check_ssl_certificates",
            "status": status,
            "severity": severity,
            "details": "\n".join(expiry_info) if expiry_info else "No certificate expiry info found",
            "fix": None,
        }

    def check_security_updates(self):
        apt_result = self.ssh.execute(
            "apt list --upgradable 2>/dev/null | grep -i security | wc -l"
        )
        if apt_result["exit_code"] == 0 and apt_result["stdout"].strip() != "0":
            count = apt_result["stdout"].strip()
            return {
                "name": "check_security_updates",
                "status": "warning",
                "severity": "medium",
                "details": f"{count} security update(s) available (apt)",
                "fix": [
                    {"command": "apt-get update && apt-get upgrade -y", "description": "Install all available updates", "destructive": True},
                ],
            }

        yum_result = self.ssh.execute(
            "yum check-update --security 2>/dev/null | tail -n +2 | grep -v '^$' | wc -l"
        )
        if yum_result["exit_code"] in (0, 100) and yum_result["stdout"].strip() not in ("0", ""):
            count = yum_result["stdout"].strip()
            return {
                "name": "check_security_updates",
                "status": "warning",
                "severity": "medium",
                "details": f"{count} security update(s) available (yum)",
                "fix": [
                    {"command": "yum update --security -y", "description": "Install security updates", "destructive": True},
                ],
            }

        return self._ok("check_security_updates", "No pending security updates")

    def check_firewall(self):
        ufw = self.ssh.execute("ufw status 2>/dev/null")
        if ufw["exit_code"] == 0 and "active" in ufw["stdout"].lower():
            return {
                "name": "check_firewall",
                "status": "ok",
                "severity": "low",
                "details": f"UFW is active\n{ufw['stdout'].strip()}",
                "fix": None,
            }

        firewalld = self.ssh.execute("firewall-cmd --state 2>/dev/null")
        if firewalld["exit_code"] == 0 and "running" in firewalld["stdout"].lower():
            zones = self.ssh.execute("firewall-cmd --list-all 2>/dev/null")
            return {
                "name": "check_firewall",
                "status": "ok",
                "severity": "low",
                "details": f"firewalld is running\n{zones['stdout'].strip()}",
                "fix": None,
            }

        iptables = self.ssh.execute("iptables -L -n 2>/dev/null | wc -l")
        if iptables["exit_code"] == 0:
            rule_count = int(iptables["stdout"].strip())
            if rule_count > 8:
                return {
                    "name": "check_firewall",
                    "status": "ok",
                    "severity": "low",
                    "details": f"iptables has {rule_count} rules configured",
                    "fix": None,
                }

        return {
            "name": "check_firewall",
            "status": "warning",
            "severity": "medium",
            "details": "No active firewall detected (checked ufw, firewalld, iptables)",
            "fix": None,
        }

    def apply_fix(self, action):
        command = action["command"]
        description = action.get("description", command)
        destructive = action.get("destructive", False)

        if destructive:
            snapshot_id = self.rollback.create_snapshot(command, description)

            approved = self.approval.request_approval({
                "command": command,
                "description": description,
                "destructive": destructive,
                "snapshot_id": snapshot_id,
            })

            if not approved:
                return {"applied": False, "reason": "Approval denied"}

            result = self.ssh.execute_sudo(command)
        else:
            result = self.ssh.execute(command)

        if result["exit_code"] != 0:
            return {"applied": False, "reason": f"Command failed (exit {result['exit_code']}): {result['stderr']}"}

        return {"applied": True, "result": result["stdout"].strip()}

    def _ok(self, name, details):
        return {"name": name, "status": "ok", "severity": "low", "details": details, "fix": None}

    def _error(self, name, details):
        return {"name": name, "status": "error", "severity": "low", "details": details, "fix": None}
