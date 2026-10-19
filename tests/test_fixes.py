"""Sign-in, shared MCP token and model provider fixes (no network)."""

from __future__ import annotations

import json

import pytest

from mailkeep import auth, config, llm


@pytest.fixture
def env_only_client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CLIENT_SECRET_FILE", tmp_path / "none.json")
    monkeypatch.setattr(config, "CLIENT_TYPE_FILE", tmp_path / "client_type")
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "gmail.env")
    monkeypatch.setattr(config, "FALLBACK_ENV_FILE", tmp_path / "gmail2.env")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "x.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "s")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_TYPE", raising=False)


def test_env_only_client_type_is_not_guessed(env_only_client):
    # The screenshot: env-only Web client was assumed Desktop -> redirect_uri_mismatch.
    assert config.client_type() == "unknown"
    with pytest.raises(config.ConfigError, match="redirect_uri_mismatch"):
        auth.check_redirect("http://localhost:8765/oauth/callback")


def test_type_picked_in_ui_is_remembered(env_only_client):
    assert config.save_client_type("desktop") == "installed"
    assert config.client_type() == "installed"
    auth.check_redirect("http://localhost:8765/oauth/callback")  # Desktop: no registration needed
    config.save_client_type("web")
    assert config.client_type() == "web"


def test_bad_client_type_rejected(env_only_client):
    with pytest.raises(config.ConfigError):
        config.save_client_type("tv")


def test_scopes_match_workspace_mcp_drafts_and_never_send():
    assert "https://www.googleapis.com/auth/gmail.send" not in config.SCOPES
    for scope in ("gmail.readonly", "gmail.labels", "gmail.modify", "gmail.compose", "userinfo.email"):
        assert f"https://www.googleapis.com/auth/{scope}" in config.SCOPES
    launcher = (config.ROOT / "scripts" / "start_gmail_mcp.py").read_text(encoding="utf-8")
    assert '"gmail:drafts"' in launcher and '"gmail:full"' not in launcher


def test_unticked_gmail_boxes_are_detected():
    assert auth.missing_scopes(["openid"]) == list(config.REQUIRED_SCOPES)
    assert auth.missing_scopes(config.SCOPES) == []


def test_mcp_token_written_in_workspace_mcp_format(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "mailkeep-token.json")
    monkeypatch.setattr(auth, "TOKEN_DIR", tmp_path)
    monkeypatch.setattr(auth.mcp_token_path, "__defaults__", (tmp_path,))

    class Creds:
        token, refresh_token, token_uri = "t", "r", "https://oauth2.googleapis.com/token"
        client_id, client_secret, scopes, expiry = "id", "sec", list(config.SCOPES), None

        def to_json(self):
            return "{}"

    auth.save_credentials(Creds(), "a.b+c@gmail.com")
    data = json.loads((tmp_path / "a.b%2Bc@gmail.com.json").read_text())
    assert data["refresh_token"] == "r" and data["client_id"] == "id"


def test_access_denied_explains_test_users():
    assert "Test users" in auth.CONSENT_ERRORS["access_denied"]


@pytest.mark.parametrize(
    "key,slot,base,env",
    [
        ("portal", "openai", "https://api.cortensor.app/v1", "CORTENSOR_API_KEY"),
        ("gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
        ("openrouter", "openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    ],
)
def test_provider_presets(monkeypatch, key, slot, base, env):
    monkeypatch.setenv("MAILKEEP_LLM_PROVIDER", key)
    cfg = llm.build_config(llm.selected())
    assert cfg["agents"]["openminion"] == {"name": "openminion", "provider": slot, "default_channel": "console"}
    assert cfg["providers"][slot]["base_url"] == base
    assert cfg["providers"][slot]["api_key_env"] == env
    assert "system" not in cfg


def test_unknown_provider_falls_back_to_rules(monkeypatch):
    from mailkeep import agent

    monkeypatch.setenv("MAILKEEP_LLM_PROVIDER", "nope")
    mode, reason = agent.resolve_mode()
    assert mode == "rules" and "nope" in reason


def test_selected_provider_without_key_falls_back(monkeypatch):
    from mailkeep import agent

    monkeypatch.setenv("MAILKEEP_LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(llm, "setting", lambda name: "gemini" if name == "MAILKEEP_LLM_PROVIDER" else "")
    mode, reason = agent.resolve_mode()
    assert mode == "rules" and "GEMINI_API_KEY" in reason
