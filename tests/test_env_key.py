"""The provider key must be found in .env, even with Windows file quirks."""
import importlib

import pytest

from mailkeep import config, llm


@pytest.fixture
def envdir(tmp_path, monkeypatch):
    for k in ("OPENROUTER_API_KEY", "MAILKEEP_LLM_PROVIDER", "MAILKEEP_LLM_API_KEY", "LLM_PROVIDER",
              "LLM_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "CORTENSOR_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    dot = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "missing1")
    monkeypatch.setattr(config, "FALLBACK_ENV_FILE", tmp_path / "missing2")
    monkeypatch.setattr(config, "DOTENV_FILES", (dot,))
    return dot


def test_root_dotenv_key_is_used(envdir):
    envdir.write_text("OPENROUTER_API_KEY=sk-or-abc\n", encoding="utf-8")
    assert llm.selected().key == "openrouter"


def test_bom_export_and_comment(envdir):
    envdir.write_bytes("\ufeffexport OPENROUTER_API_KEY = sk-or-abc  # mine\n".encode("utf-8"))
    assert config.setting("OPENROUTER_API_KEY") == "sk-or-abc"


def test_utf16_file(envdir):
    envdir.write_text("OPENROUTER_API_KEY=sk-or-abc\n", encoding="utf-16")
    assert config.setting("OPENROUTER_API_KEY") == "sk-or-abc"


def test_key_exported_for_openminion(envdir, monkeypatch):
    envdir.write_text('OPENROUTER_API_KEY="sk-or-abc"\n', encoding="utf-8")
    assert llm.export_key(llm.by_name("openrouter"))
    import os
    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-abc"


def test_key_found_by_prefix_under_any_name(envdir):
    envdir.write_text("MY_SECRET=sk-or-v1-xyz\n", encoding="utf-8")
    assert llm.selected().key == "openrouter"
    assert llm._api_key(llm.selected()) == "sk-or-v1-xyz"


def test_env_txt_is_read(envdir, monkeypatch):
    txt = envdir.with_name(".env.txt")
    monkeypatch.setattr(config, "DOTENV_FILES", (envdir, txt))
    txt.write_text("OPENROUTER_API_KEY=sk-or-abc\n", encoding="utf-8")
    assert config.setting("OPENROUTER_API_KEY") == "sk-or-abc"


def test_describe_never_prints_key(envdir):
    envdir.write_text("OPENROUTER_API_KEY=sk-or-secretvalue\n", encoding="utf-8")
    assert "sk-or-secretvalue" not in "\n".join(llm.describe())
