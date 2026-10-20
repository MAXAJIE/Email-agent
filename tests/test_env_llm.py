"""Provider, model, key and timeout come from gmail.env, not from presets."""
import json
import os

import pytest

from mailkeep import agent, config, llm

KEYS = ("OPENROUTER_API_KEY", "OPENROUTER_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "CORTENSOR_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY",
        "NIM_API_KEY", "OPENAI_BASE_URL", "MAILKEEP_ENV_FILE")


@pytest.fixture
def gmail_env(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MAILKEEP_LLM_", "LLM_")) or k in KEYS:
            monkeypatch.delenv(k, raising=False)
    env = tmp_path / "gmail.env"
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.setattr(config, "FALLBACK_ENV_FILE", tmp_path / "none1")
    monkeypatch.setattr(config, "EXTRA_ENV_FILES", ())
    monkeypatch.setattr(config, "DOTENV_FILES", ())
    monkeypatch.setattr(llm, "GENERATED_CONFIG", tmp_path / "agent.json")
    return env


def test_env_values_drive_generated_config(gmail_env):
    gmail_env.write_text(
        "MAILKEEP_LLM_PROVIDER=gemini\nMAILKEEP_LLM_MODEL=gemini-2.5-pro\n"
        "MAILKEEP_LLM_API_KEY=AIzaTest\nMAILKEEP_LLM_TIMEOUT=300\n", encoding="utf-8")
    data = json.loads(agent.config_path().read_text(encoding="utf-8"))
    assert data["agents"]["openminion"]["provider"] == "openai"
    assert data["providers"]["openai"]["model"] == "gemini-2.5-pro"
    assert data["providers"]["openai"]["timeout_seconds"] == 300
    assert os.environ["GEMINI_API_KEY"] == "AIzaTest"


def test_comment_after_empty_value_is_not_a_key(gmail_env):
    gmail_env.write_text(
        "MAILKEEP_LLM_PROVIDER=openrouter   # pick\n"
        "MAILKEEP_LLM_API_KEY=              # or OPENROUTER_API_KEY\n"
        "OPENROUTER_API_KEY=sk-or-real\n", encoding="utf-8")
    assert config.setting("MAILKEEP_LLM_API_KEY") == ""
    assert llm._api_key(llm.selected()) == "sk-or-real"


def test_custom_endpoint(gmail_env):
    gmail_env.write_text(
        "MAILKEEP_LLM_PROVIDER=custom\nMAILKEEP_LLM_MODEL=llama3\n"
        "MAILKEEP_LLM_BASE_URL=http://localhost:11434/v1\nMAILKEEP_LLM_API_KEY=x\n",
        encoding="utf-8")
    p = llm.selected()
    assert (p.model, p.base_url) == ("llama3", "http://localhost:11434/v1")


def test_generic_key_without_provider_is_inferred(gmail_env):
    gmail_env.write_text("MAILKEEP_LLM_API_KEY=sk-or-v1-abc\n", encoding="utf-8")
    assert llm.selected().key == "openrouter"


def test_custom_without_base_url_infers_nvidia(gmail_env):
    # The reported setup: provider=custom + an nvapi- key + a NIM model, but
    # no MAILKEEP_LLM_BASE_URL. It used to raise and silently drop to rules.
    gmail_env.write_text(
        "MAILKEEP_LLM_PROVIDER=custom\n"
        "MAILKEEP_LLM_MODEL=deepseek-ai/deepseek-v4.1-flash\n"
        "MAILKEEP_LLM_API_KEY=nvapi-test\n",
        encoding="utf-8",
    )
    f = llm.selected()
    assert f.base_url == "https://integrate.api.nvidia.com/v1"
    assert f.slot == "openai"


def test_custom_uses_openai_base_url_alias(gmail_env, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    gmail_env.write_text(
        "MAILKEEP_LLM_PROVIDER=custom\nMAILKEEP_LLM_MODEL=llama3\n"
        "MAILKEEP_LLM_API_KEY=local\n",
        encoding="utf-8",
    )
    assert llm.selected().base_url == "http://localhost:11434/v1"


def test_nvidia_key_selects_nvidia_and_exports_its_env(gmail_env, monkeypatch):
    gmail_env.write_text(
        "MAILKEEP_LLM_MODEL=deepseek-ai/deepseek-v4.1-flash\nNVIDIA_API_KEY=nvapi-test\n",
        encoding="utf-8",
    )
    p = llm.selected()
    assert (p.key, p.slot, p.base_url) == (
        "nvidia", "openai", "https://integrate.api.nvidia.com/v1"
    )
    import os

    try:
        assert llm.export_key(p)
        assert os.environ["NVIDIA_API_KEY"] == "nvapi-test"
    finally:
        # export_key writes straight to os.environ, outside monkeypatch.
        os.environ.pop("NVIDIA_API_KEY", None)


def test_custom_with_unknown_key_still_asks_for_base_url(gmail_env):
    gmail_env.write_text(
        "MAILKEEP_LLM_PROVIDER=custom\nMAILKEEP_LLM_MODEL=m\n"
        "MAILKEEP_LLM_API_KEY=opaque-key\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MAILKEEP_LLM_BASE_URL"):
        llm.selected()


def test_custom_with_generic_sk_key_does_not_assume_openai(gmail_env):
    # A bare sk- key is common to many vendors/proxies: never redirect those
    # requests to api.openai.com.
    gmail_env.write_text(
        "MAILKEEP_LLM_PROVIDER=custom\nMAILKEEP_LLM_MODEL=m\n"
        "MAILKEEP_LLM_API_KEY=sk-generic\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MAILKEEP_LLM_BASE_URL"):
        llm.selected()


def test_env_file_override_path(gmail_env, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere.env"
    other.write_text("MAILKEEP_LLM_PROVIDER=openai\nMAILKEEP_LLM_MODEL=gpt-x\n", encoding="utf-8")
    monkeypatch.setenv("MAILKEEP_ENV_FILE", str(other))
    assert llm.selected().model == "gpt-x"


def test_bad_timeout_is_reported(gmail_env):
    gmail_env.write_text("MAILKEEP_LLM_PROVIDER=openai\nMAILKEEP_LLM_TIMEOUT=abc\n", encoding="utf-8")
    with pytest.raises(ValueError):
        llm.selected()
