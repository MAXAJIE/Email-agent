"""Truthful health, dedicated roles with main-model fallback, LLM router."""
import io
import json
import os
import urllib.error

import pytest

from mailkeep import config, fastllm, llm, providers, router


def _clean(monkeypatch):
    for name in list(os.environ):
        if "API_KEY" in name or name.startswith(("MAILKEEP_LLM", "LLM_", "MAILKEEP_UI_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "env_files", lambda: ())
    monkeypatch.setattr(llm, "env_files", lambda: ())
    monkeypatch.setattr(llm, "env_values", lambda: {})
    monkeypatch.setattr(providers, "_health", {})


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self, _n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_http(models_status, models, chat_status):
    def urlopen(request, timeout=0):
        url = request.full_url
        if url.endswith("/models"):
            if models_status != 200:
                raise urllib.error.HTTPError(url, models_status, "x", {}, io.BytesIO(b""))
            return _Resp(200, json.dumps({"data": [{"id": m} for m in models]}).encode())
        if chat_status != 200:
            raise urllib.error.HTTPError(url, chat_status, "x", {}, io.BytesIO(b"404 page not found"))
        return _Resp(200, b'{"choices":[{"message":{"content":"p"}}]}')
    return urlopen


REAL_PROBE = fastllm.probe  # captured before the conftest stub replaces it


@pytest.fixture
def real_probe(monkeypatch):
    monkeypatch.setattr(fastllm, "probe", REAL_PROBE)
    return fastllm


def _nim(model="nvidia/fake-model"):
    return llm.Preset("nvidia", "NVIDIA NIM", "openai", "https://integrate.api.nvidia.com/v1",
                      "NVIDIA_API_KEY", model, profile="probe", api_key="nvapi-x")


def test_unknown_model_is_not_reported_online(real_probe, monkeypatch):
    monkeypatch.setattr(fastllm.urllib.request, "urlopen", _fake_http(200, ["meta/llama"], 404))
    health = real_probe.probe(_nim())
    assert health.state == "model_missing"


def test_listed_model_is_online(real_probe, monkeypatch):
    monkeypatch.setattr(fastllm.urllib.request, "urlopen", _fake_http(200, ["nvidia/fake-model"], 404))
    assert real_probe.probe(_nim()).state == "online"


def test_404_is_not_retried():
    assert not fastllm.is_retryable(RuntimeError("openai request failed with HTTP 404"))


def test_model_only_role_falls_back_to_main(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-x")
    monkeypatch.setenv("MAILKEEP_LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("MAILKEEP_LLM_MODEL", "main-model")
    monkeypatch.setenv("MAILKEEP_LLM_FAST_MODEL", "Qwen/qwen3.5")  # no provider -> not dedicated
    preset, note = providers.effective("fast")
    assert preset.model == "main-model" and "PROVIDER is not set" in note


def test_role_whose_model_fails_falls_back_to_main(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("MAILKEEP_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-main")
    monkeypatch.setenv("MAILKEEP_LLM_DRAFT_PROVIDER", "openrouter")
    monkeypatch.setenv("MAILKEEP_LLM_DRAFT_MODEL", "x/y")
    monkeypatch.setenv("MAILKEEP_LLM_DRAFT_API_KEY", "sk-or-d")
    assert providers.effective("draft")[0].key == "openrouter"
    used = []

    def complete(preset, *a, **k):
        used.append(preset.key)
        if preset.key == "openrouter":
            raise fastllm.NotRetryable("HTTP 404: no such model")
        return "body"

    monkeypatch.setattr(fastllm, "complete", complete)
    text, preset = providers.complete("draft", "s", "u")
    assert text == "body" and preset.key == "gemini" and used == ["openrouter", "gemini"]
    assert providers.effective("draft")[0].key == "gemini"  # marked unusable


def test_router_parses_agent_and_rejects_unknown():
    d = router.parse('```json\n{"action":"agent","agent":"mailbox","args":{"command":"archive 1"}}\n```')
    assert d.agent == "mailbox" and d.args["command"] == "archive 1"
    with pytest.raises(ValueError):
        router.parse('{"action":"agent","agent":"hacker","args":{}}')
    assert router.parse('{"action":"reply","reply":"hi"}').reply == "hi"


def test_router_dispatches_mailbox_agent(monkeypatch):
    from mailkeep.agent import MailkeepAgent
    from mailkeep.demo import DemoGmailClient
    from mailkeep.session import Session

    _clean(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-main")
    monkeypatch.setenv("MAILKEEP_LLM_ROUTER", "on")
    agent = MailkeepAgent(Session(DemoGmailClient()))
    agent.mode = "openminion"
    monkeypatch.setattr(fastllm, "complete", lambda *a, **k:
                        '{"action":"agent","agent":"mailbox","args":{"command":"find invoices"}}')
    try:
        outcome = agent.chat("can you look for my invoices please")
    finally:
        agent.close()
    assert any(r.table for r in outcome.responses)
