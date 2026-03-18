from concurrent.futures import ThreadPoolExecutor, as_completed
import re


class OSDetector:
    def __init__(self, ssh_manager):
        self.ssh = ssh_manager

    def detect(self):
        commands = {
            "uname": "uname -a",
            "os_release": "cat /etc/os-release 2>/dev/null || true",
            "arch": "uname -m",
            "hostname": "hostname",
            "uptime": "uptime",
            "proc_version": "cat /proc/version 2>/dev/null || true",
            "lsb_release": "lsb_release -a 2>/dev/null || true",
        }

        results = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(self.ssh.execute, cmd): name
                for name, cmd in commands.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception:
                    results[name] = {"stdout": "", "stderr": "", "exit_code": 1}

        return {
            "type": self._parse_os_type(results),
            "distribution": self._parse_distribution(results),
            "version": self._parse_version(results),
            "kernel": self._parse_kernel(results),
            "architecture": self._parse_architecture(results),
            "hostname": self._parse_hostname(results),
            "uptime": self._parse_uptime(results),
            "raw": {k: v["stdout"] for k, v in results.items()},
        }

    def _parse_os_type(self, results):
        uname = results.get("uname", {}).get("stdout", "").lower()
        if "linux" in uname:
            return "Linux"
        if "darwin" in uname:
            return "macOS"
        if "freebsd" in uname:
            return "FreeBSD"
        return "Unknown"

    def _parse_distribution(self, results):
        os_release = results.get("os_release", {}).get("stdout", "")
        lsb = results.get("lsb_release", {}).get("stdout", "")

        id_match = re.search(r'^ID=[""]?([^""\n]+)', os_release, re.MULTILINE)
        if id_match:
            distro_id = id_match.group(1).strip().lower()
            distro_map = {
                "ubuntu": "Ubuntu",
                "centos": "CentOS",
                "debian": "Debian",
                "almalinux": "AlmaLinux",
                "rocky": "Rocky",
                "amzn": "Amazon Linux",
                "alpine": "Alpine",
                "fedora": "Fedora",
                "rhel": "RHEL",
                "opensuse-leap": "openSUSE Leap",
                "opensuse-tumbleweed": "openSUSE Tumbleweed",
                "arch": "Arch Linux",
                "ol": "Oracle Linux",
            }
            for key, name in distro_map.items():
                if distro_id == key:
                    return name

            name_match = re.search(r'^NAME=[""]?([^""\n]+)', os_release, re.MULTILINE)
            if name_match:
                return name_match.group(1).strip()
            return distro_id.capitalize()

        dist_match = re.search(r'Distributor ID:\s*(.+)', lsb)
        if dist_match:
            return dist_match.group(1).strip()

        uname = results.get("uname", {}).get("stdout", "").lower()
        if "darwin" in uname:
            return "macOS"
        if "freebsd" in uname:
            return "FreeBSD"

        return "Unknown"

    def _parse_version(self, results):
        os_release = results.get("os_release", {}).get("stdout", "")
        match = re.search(r'^VERSION_ID=[""]?([^""\n]+)', os_release, re.MULTILINE)
        if match:
            return match.group(1).strip()

        lsb = results.get("lsb_release", {}).get("stdout", "")
        match = re.search(r'Release:\s*(.+)', lsb)
        if match:
            return match.group(1).strip()

        return "Unknown"

    def _parse_kernel(self, results):
        uname = results.get("uname", {}).get("stdout", "").strip()
        parts = uname.split()
        if len(parts) >= 3:
            return parts[2]
        return uname or "Unknown"

    def _parse_architecture(self, results):
        arch = results.get("arch", {}).get("stdout", "").strip()
        return arch or "Unknown"

    def _parse_hostname(self, results):
        hostname = results.get("hostname", {}).get("stdout", "").strip()
        return hostname or "Unknown"

    def _parse_uptime(self, results):
        uptime = results.get("uptime", {}).get("stdout", "").strip()
        return uptime or "Unknown"
