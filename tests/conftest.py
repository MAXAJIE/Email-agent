"""Tests never touch the network: the model health probe is stubbed."""
import time

import pytest

from mailkeep import config, fastllm


@pytest.fixture(autouse=True)
def _offline_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(fastllm, "probe", lambda preset, timeout=6.0: fastllm.Health(
        "online", 1, "stub", time.time()))
    monkeypatch.setattr(fastllm, "retry_backoff", lambda: 0.0)
    # Router tests enable it explicitly with a stubbed model.
    monkeypatch.setenv("MAILKEEP_LLM_ROUTER", "off")
    # Legacy one-line draft tests; test_draft_editing.py turns the strict checklist on.
    monkeypatch.setenv("MAILKEEP_DRAFT_STRICT", "off")
    monkeypatch.setattr(fastllm, "LOG_FILE", tmp_path / "mailkeep.log")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "mailkeep-settings.json")
    monkeypatch.setattr(config, "_session_overrides", {})
    monkeypatch.setattr(config, "_saved_cache", (-1.0, {}))
