import json

import pytest

from mailkeep import auth, config


def _client(tmp_path, monkeypatch, data):
    path = tmp_path / "client.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("MAILKEEP_CLIENT_SECRET_FILE", str(path))


def test_web_client_unregistered_uri_fails_before_google(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch, {"web": {"client_id": "x", "client_secret": "y",
                                            "redirect_uris": ["http://localhost:8080/"]}})
    assert config.client_type() == "web"
    with pytest.raises(config.ConfigError, match="redirect"):
        auth.check_redirect(config.ui_redirect_uri(8765))
    auth.check_redirect("http://localhost:8080/")


def test_desktop_client_needs_no_registration(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch, {"installed": {"client_id": "x", "client_secret": "y"}})
    auth.check_redirect(config.ui_redirect_uri(8765))
    cfg = config.client_config("http://localhost:8765/oauth/callback")
    assert cfg["installed"]["redirect_uris"] == ["http://localhost:8765/oauth/callback"]


def test_save_client_file_rejects_non_google_json():
    with pytest.raises(config.ConfigError):
        config.save_client_file('{"foo": 1}')
