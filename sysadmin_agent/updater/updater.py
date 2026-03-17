import json
import re
import shutil
import subprocess
from pathlib import Path


class Updater:
    def __init__(self, repo_dir, config_files=None):
        self.repo_dir = Path(repo_dir)
        self.config_files = config_files or [".env", "config.json"]
        self._previous_head = None

    def _run(self, *args, check=True):
        result = subprocess.run(
            args, cwd=self.repo_dir, capture_output=True, text=True, check=check
        )
        return result

    def _get_default_branch(self):
        result = self._run("git", "symbolic-ref", "refs/remotes/origin/HEAD", check=False)
        if result.returncode == 0:
            return result.stdout.strip().replace("refs/remotes/origin/", "")
        return "main"

    def check_for_updates(self):
        self._run("git", "fetch", "origin")
        branch = self._get_default_branch()

        local = self._run("git", "rev-parse", "HEAD").stdout.strip()
        remote = self._run("git", "rev-parse", f"origin/{branch}").stdout.strip()

        behind_result = self._run(
            "git", "rev-list", "--count", f"HEAD..origin/{branch}"
        )
        commits_behind = int(behind_result.stdout.strip())

        changes_summary = []
        if commits_behind > 0:
            log_result = self._run(
                "git", "log", "--oneline", f"HEAD..origin/{branch}"
            )
            changes_summary = [
                line.strip() for line in log_result.stdout.strip().splitlines() if line.strip()
            ]

        return {
            "update_available": local != remote,
            "current_version": local[:8],
            "latest_version": remote[:8],
            "commits_behind": commits_behind,
            "changes_summary": changes_summary,
        }

    def backup_configs(self):
        backed_up = []
        for name in self.config_files:
            path = self.repo_dir / name
            if path.exists():
                backup_path = Path(str(path) + ".backup")
                shutil.copy2(path, backup_path)
                backed_up.append(str(backup_path))
        return backed_up

    def apply_update(self):
        errors = []
        config_changes = []

        self._previous_head = self._run("git", "rev-parse", "HEAD").stdout.strip()
        self.backup_configs()

        old_configs = {}
        for name in self.config_files:
            path = self.repo_dir / name
            if path.exists():
                old_configs[name] = path.read_text()

        branch = self._get_default_branch()
        pull_result = self._run("git", "pull", "origin", branch, check=False)
        if pull_result.returncode != 0:
            errors.append(f"git pull failed: {pull_result.stderr.strip()}")
            return {
                "success": False,
                "version": self._previous_head[:8],
                "config_changes": config_changes,
                "errors": errors,
            }

        for name in self.config_files:
            path = self.repo_dir / name
            backup_path = Path(str(path) + ".backup")
            if not backup_path.exists():
                continue

            try:
                if name.endswith(".env"):
                    if path.exists():
                        self.merge_env_files(backup_path, path, path)
                        config_changes.append(f"Merged {name}")
                    else:
                        shutil.copy2(backup_path, path)
                        config_changes.append(f"Restored {name} (removed upstream)")
                elif name.endswith(".json"):
                    if path.exists():
                        self.merge_json_configs(backup_path, path, path)
                        config_changes.append(f"Merged {name}")
                    else:
                        shutil.copy2(backup_path, path)
                        config_changes.append(f"Restored {name} (removed upstream)")
                else:
                    shutil.copy2(backup_path, path)
                    config_changes.append(f"Restored {name} from backup")
            except Exception as e:
                errors.append(f"Config merge failed for {name}: {e}")

        pip_result = self._install_deps()
        if pip_result and pip_result.returncode != 0:
            errors.append(f"Dependency install warning: {pip_result.stderr.strip()}")

        new_head = self._run("git", "rev-parse", "HEAD").stdout.strip()

        return {
            "success": len(errors) == 0,
            "version": new_head[:8],
            "config_changes": config_changes,
            "errors": errors,
        }

    def _install_deps(self):
        setup_py = self.repo_dir / "setup.py"
        setup_cfg = self.repo_dir / "setup.cfg"
        pyproject = self.repo_dir / "pyproject.toml"
        requirements = self.repo_dir / "requirements.txt"

        if setup_py.exists() or setup_cfg.exists() or pyproject.exists():
            return self._run(
                "pip", "install", "--user", "-e", ".", check=False
            )
        elif requirements.exists():
            return self._run(
                "pip", "install", "--user", "-r", "requirements.txt", check=False
            )
        return None

    def merge_env_files(self, old_path, new_path, output_path):
        old_path = Path(old_path)
        new_path = Path(new_path)
        output_path = Path(output_path)

        old_entries, old_comments = self._parse_env(old_path)
        new_entries, _ = self._parse_env(new_path)

        lines = []

        for line in old_comments:
            lines.append(line)

        for key, value in old_entries.items():
            if key in new_entries:
                lines.append(f"{key}={value}")
            else:
                lines.append(f"# REMOVED in update: {key}={value}")

        for key, value in new_entries.items():
            if key not in old_entries:
                lines.append(f"# NEW in update")
                lines.append(f"{key}={value}")

        output_path.write_text("\n".join(lines) + "\n")

    def merge_json_configs(self, old_path, new_path, output_path):
        old_path = Path(old_path)
        new_path = Path(new_path)
        output_path = Path(output_path)

        with open(old_path) as f:
            old_data = json.load(f)
        with open(new_path) as f:
            new_data = json.load(f)

        merged = self._deep_merge(new_data, old_data)

        with open(output_path, "w") as f:
            json.dump(merged, f, indent=2)
            f.write("\n")

    def _deep_merge(self, base, override):
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def _parse_env(self, path):
        entries = {}
        comments = []
        for line in Path(path).read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                comments.append(line)
                continue
            match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)', line)
            if match:
                entries[match.group(1)] = match.group(2)
            else:
                comments.append(line)
        return entries, comments

    def rollback_update(self):
        if not self._previous_head:
            return False

        try:
            for name in self.config_files:
                backup_path = self.repo_dir / (name + ".backup")
                target_path = self.repo_dir / name
                if backup_path.exists():
                    shutil.copy2(backup_path, target_path)
                    backup_path.unlink()

            self._run("git", "reset", "--hard", self._previous_head)
            self._previous_head = None
            return True
        except Exception:
            return False
