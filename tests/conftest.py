"""Tests never touch the network: the model health probe is stubbed."""
import time

import pytest

from mailkeep import fastllm


@pytest.fixture(autouse=True)
def _offline_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(fastllm, "probe", lambda preset, timeout=6.0: fastllm.Health(
        "online", 1, "stub", time.time()))
    monkeypatch.setattr(fastllm, "retry_backoff", lambda: 0.0)
    monkeypatch.setattr(fastllm, "LOG_FILE", tmp_path / "mailkeep.log")
