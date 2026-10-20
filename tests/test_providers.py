"""Settings gear, role fallback and the direct fast path."""
import os

from mailkeep import config, llm, providers


def _clean(monkeypatch):
    for name in list(os.environ):
        if "API_KEY" in name or name.startswith(("MAILKEEP_LLM", "LLM_", "MAILKEEP_UI_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "env_files", lambda: ())
    monkeypatch.setattr(llm, "env_files", lambda: ())
    monkeypatch.setattr(llm, "env_values", lambda: {})


def test_unset_reasoning_and_draft_fall_back_to_main(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    for role in ("reasoning", "draft"):
        preset, note = providers.effective(role)
        assert preset.key == "gemini" and preset.model == "gemini-2.5-flash"
        assert "main model" in note


def test_role_without_key_falls_back_to_main(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.setenv("MAILKEEP_LLM_DRAFT_PROVIDER", "openrouter")
    preset, note = providers.effective("draft")
    assert preset.key == "gemini" and "falls back" in note


def test_no_model_at_all_means_rule_parser(monkeypatch):
    _clean(monkeypatch)
    assert providers.effective("draft")[0] is None
    assert providers.inventory(probe=False)["mode_hint"] == "rules"


def test_key_typed_in_ui_activates_provider_without_env(monkeypatch):
    _clean(monkeypatch)
    providers.apply_settings({"persist": False, "providers": {"portal": {"api_key": "ck-test"}}})
    main = llm.selected()
    assert main.key == "portal" and llm._api_key(main) == "ck-test"
    assert not config.SETTINGS_FILE.exists()  # session only


def test_saved_setting_persists_to_file_and_overrides_env(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-env")
    providers.apply_settings({"persist": True, "providers": {"openrouter": {"api_key": "sk-or-ui"}},
                              "roles": {"default": "openrouter"}})
    assert config.SETTINGS_FILE.exists()
    monkeypatch.setattr(config, "_session_overrides", {})
    assert llm.selected().key == "openrouter"


def test_inventory_lists_roles_and_never_returns_keys(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-secret")
    data = providers.inventory(probe=True)
    gemini = next(p for p in data["providers"] if p["key"] == "gemini")
    assert gemini["state"] == "online" and "default" in gemini["roles"]
    assert "AIza-secret" not in str(data)


def test_simple_question_skips_agent_but_actions_do_not():
    assert not llm.needs_agent("hi, what can you do?")
    assert llm.needs_agent("find invoices")
    assert llm.needs_agent("archive 1,3")
    assert llm.needs_agent("draft a reply to bob")
