"""Tests for the ProfileManager module."""

import json
import pytest

from sysadmin_agent.profiles.profile_manager import ProfileManager


class TestSaveAndGetProfile:
    def test_save_and_get_profile(self, tmp_path):
        config = tmp_path / "config.json"
        pm = ProfileManager(config_path=str(config))

        pm.save_profile(
            name="web1",
            host="10.0.0.1",
            username="deploy",
            port=2222,
            auth_type="key",
            key_path="/home/deploy/.ssh/id_rsa",
            notes="production web server",
        )

        profile = pm.get_profile("web1")
        assert profile is not None
        assert profile["host"] == "10.0.0.1"
        assert profile["username"] == "deploy"
        assert profile["port"] == 2222
        assert profile["auth_type"] == "key"
        assert profile["key_path"] == "/home/deploy/.ssh/id_rsa"
        assert profile["notes"] == "production web server"


class TestListProfiles:
    def test_list_profiles_returns_all(self, tmp_path):
        config = tmp_path / "config.json"
        pm = ProfileManager(config_path=str(config))

        pm.save_profile(name="web1", host="10.0.0.1", username="admin")
        pm.save_profile(name="web2", host="10.0.0.2", username="deploy")
        pm.save_profile(name="db1", host="10.0.0.3", username="root")

        profiles = pm.list_profiles()
        assert len(profiles) == 3
        names = {p["name"] for p in profiles}
        assert names == {"web1", "web2", "db1"}
        # Each summary should have the expected keys
        for p in profiles:
            assert "name" in p
            assert "host" in p
            assert "username" in p
            assert "auth_type" in p


class TestDeleteProfile:
    def test_delete_profile(self, tmp_path):
        config = tmp_path / "config.json"
        pm = ProfileManager(config_path=str(config))

        pm.save_profile(name="web1", host="10.0.0.1", username="admin")
        assert pm.get_profile("web1") is not None

        result = pm.delete_profile("web1")
        assert result is True
        assert pm.get_profile("web1") is None

    def test_delete_nonexistent_returns_false(self, tmp_path):
        config = tmp_path / "config.json"
        pm = ProfileManager(config_path=str(config))

        result = pm.delete_profile("does_not_exist")
        assert result is False


class TestPasswordNotStoredPlaintext:
    def test_password_not_stored_plaintext(self, tmp_path):
        config = tmp_path / "config.json"
        pm = ProfileManager(config_path=str(config))

        pm.save_profile(
            name="secret_server",
            host="10.0.0.99",
            username="admin",
            auth_type="password",
            password="super_secret_p@ssw0rd!",
        )

        raw = config.read_text()
        assert "super_secret_p@ssw0rd!" not in raw

        data = json.loads(raw)
        profile_data = data["profiles"]["secret_server"]
        assert profile_data.get("password_required") is True
        assert "password" not in profile_data


class TestToSshKwargs:
    def test_to_ssh_kwargs_maps_correctly(self, tmp_path):
        config = tmp_path / "config.json"
        pm = ProfileManager(config_path=str(config))

        pm.save_profile(
            name="web1",
            host="192.168.1.10",
            username="deploy",
            port=2222,
            auth_type="key",
            key_path="/home/deploy/.ssh/id_ed25519",
        )

        kwargs = pm.to_ssh_kwargs("web1")
        assert kwargs is not None
        assert kwargs["host"] == "192.168.1.10"
        assert kwargs["port"] == 2222
        assert kwargs["username"] == "deploy"
        assert kwargs["private_key_path"] == "/home/deploy/.ssh/id_ed25519"

    def test_to_ssh_kwargs_nonexistent_returns_none(self, tmp_path):
        config = tmp_path / "config.json"
        pm = ProfileManager(config_path=str(config))

        assert pm.to_ssh_kwargs("nope") is None
