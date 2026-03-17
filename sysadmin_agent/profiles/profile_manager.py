import json
from pathlib import Path


class ProfileManager:
    def __init__(self, config_path="config.json"):
        self.config_path = Path(config_path)
        self._profiles = {}
        self._load()

    def save_profile(self, name, host, username, port=22, auth_type="key",
                     password=None, key_path=None, passphrase=None, notes=None):
        profile = {
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "auth_type": auth_type,
            "notes": notes,
        }

        if auth_type == "password":
            profile["password_required"] = True
        elif auth_type == "key":
            profile["key_path"] = key_path
            if passphrase:
                profile["passphrase_required"] = True

        self._profiles[name] = profile
        self._save()
        return profile

    def get_profile(self, name):
        return self._profiles.get(name)

    def list_profiles(self):
        return [
            {
                "name": p["name"],
                "host": p["host"],
                "username": p["username"],
                "auth_type": p["auth_type"],
            }
            for p in self._profiles.values()
        ]

    def delete_profile(self, name):
        if name not in self._profiles:
            return False
        del self._profiles[name]
        self._save()
        return True

    def update_profile(self, name, **kwargs):
        if name not in self._profiles:
            return None
        self._profiles[name].update(kwargs)
        self._save()
        return self._profiles[name]

    def to_ssh_kwargs(self, name):
        profile = self.get_profile(name)
        if not profile:
            return None

        kwargs = {
            "host": profile["host"],
            "port": profile.get("port", 22),
            "username": profile["username"],
        }

        if profile["auth_type"] == "key":
            if profile.get("key_path"):
                kwargs["private_key_path"] = profile["key_path"]
        elif profile["auth_type"] == "password":
            kwargs["password"] = None

        return kwargs

    def _load(self):
        if not self.config_path.exists():
            self._profiles = {}
            return
        try:
            data = json.loads(self.config_path.read_text())
            self._profiles = data.get("profiles", {})
        except (json.JSONDecodeError, KeyError):
            self._profiles = {}

    def _save(self):
        data = {}
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text())
            except json.JSONDecodeError:
                data = {}
        data["profiles"] = self._profiles
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(data, indent=2) + "\n")
