import base64
import json
from pathlib import Path


class ProfileManager:
    def __init__(self, config_path="config.json"):
        self.config_path = Path(config_path)
        self._profiles = {}
        self._load()

    @staticmethod
    def _obfuscate(value):
        """Base64-encode a string for basic obfuscation in config files.
        This is NOT encryption — it only prevents casual shoulder-surfing."""
        if not value:
            return None
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    @staticmethod
    def _deobfuscate(value):
        """Decode a base64-obfuscated string."""
        if not value:
            return None
        try:
            return base64.b64decode(value.encode("ascii")).decode("utf-8")
        except Exception:
            return value  # Return as-is if not valid base64

    def save_profile(self, name, host, username, port=22, auth_type="key",
                     password=None, key_path=None, passphrase=None, notes=None,
                     save_password=False):
        profile = {
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "auth_type": auth_type,
            "notes": notes,
        }

        if auth_type == "password":
            if save_password and password:
                profile["password_saved"] = True
                profile["password_obf"] = self._obfuscate(password)
            else:
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
        result = []
        for p in self._profiles.values():
            entry = {
                "name": p["name"],
                "host": p["host"],
                "username": p["username"],
                "auth_type": p["auth_type"],
                "port": p.get("port", 22),
                "key_path": p.get("key_path", ""),
                "password_saved": p.get("password_saved", False),
            }
            result.append(entry)
        return result

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

    def get_saved_password(self, name):
        """Return the deobfuscated password for a profile, or None."""
        profile = self.get_profile(name)
        if not profile:
            return None
        obf = profile.get("password_obf")
        if obf:
            return self._deobfuscate(obf)
        return None

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
            # Return saved password if available
            obf = profile.get("password_obf")
            kwargs["password"] = self._deobfuscate(obf) if obf else None

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
