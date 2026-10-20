"""Task-specific model profiles and bounded parallel task execution."""
import os
import threading
import time

import pytest

from mailkeep import config, llm
from mailkeep.tasks import ChatJobs, TaskPool

KEYS = ("OPENROUTER_API_KEY", "OPENROUTER_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "CORTENSOR_API_KEY", "OPENAI_API_KEY", "MAILKEEP_ENV_FILE")


@pytest.fixture
def gmail_env(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MAILKEEP_LLM_", "LLM_", "MAILKEEP_MAX_AGENTS")) or k in KEYS:
            monkeypatch.delenv(k, raising=False)
    env = tmp_path / "gmail.env"
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.setattr(config, "FALLBACK_ENV_FILE", tmp_path / "none1")
    monkeypatch.setattr(config, "EXTRA_ENV_FILES", ())
    monkeypatch.setattr(config, "DOTENV_FILES", ())
    monkeypatch.setattr(llm, "GENERATED_CONFIG", tmp_path / "agent.json")
    return env


def write(gmail_env, *lines):
    gmail_env.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_profiles_inherit_and_override(gmail_env):
    write(gmail_env,
          "MAILKEEP_LLM_PROVIDER=openrouter",
          "MAILKEEP_LLM_MODEL=openai/gpt-4.1-mini",
          "MAILKEEP_LLM_API_KEY=sk-or-default",
          "MAILKEEP_LLM_REASONING_PROVIDER=gemini",
          "MAILKEEP_LLM_REASONING_MODEL=gemini-2.5-pro",
          "MAILKEEP_LLM_REASONING_API_KEY=AIzaTest",
          "MAILKEEP_LLM_REASONING_TIMEOUT=90")
    fast = llm.selected("fast")
    assert fast.profile == "fast" and fast.model == "openai/gpt-4.1-mini"
    reasoning = llm.selected("reasoning")
    assert reasoning.model == "gemini-2.5-pro"
    assert reasoning.timeout_seconds == 90
    # The profile key goes to OpenMinion under its own runtime variable.
    assert llm.export_key(reasoning)
    assert os.environ["MAILKEEP_RUNTIME_REASONING_API_KEY"] == "AIzaTest"
    # No vendor confusion: the fast profile never sees the Gemini key.
    assert llm._api_key(fast) == "sk-or-default"


def test_model_only_profile_inherits_provider(gmail_env):
    write(gmail_env,
          "MAILKEEP_LLM_PROVIDER=openrouter",
          "MAILKEEP_LLM_MODEL=openai/gpt-4.1-mini",
          "MAILKEEP_LLM_API_KEY=sk-or-default",
          "MAILKEEP_LLM_FAST_MODEL=openai/gpt-4.1-nano")
    fast = llm.selected("fast")
    assert fast.model == "openai/gpt-4.1-nano"
    assert llm._api_key(fast) == "sk-or-default"


def test_unknown_profile_provider_and_bad_numbers(gmail_env):
    write(gmail_env,
          "MAILKEEP_LLM_PROVIDER=openrouter",
          "MAILKEEP_LLM_API_KEY=sk-or-x",
          "MAILKEEP_LLM_DRAFT_PROVIDER=nope")
    with pytest.raises(ValueError):
        llm.selected("draft")
    for bad in ("0", "-1", "abc", "17"):
        write(gmail_env, "MAILKEEP_LLM_PROVIDER=openrouter", "MAILKEEP_LLM_API_KEY=sk-or-x",
              f"MAILKEEP_MAX_AGENTS={bad}")
        with pytest.raises(ValueError):
            llm.concurrency()
    write(gmail_env, "MAILKEEP_LLM_PROVIDER=openrouter", "MAILKEEP_LLM_API_KEY=sk-or-x",
          "MAILKEEP_LLM_REASONING_TIMEOUT=0")
    with pytest.raises(ValueError):
        llm.selected("reasoning")


def test_profile_for_picks_the_task_model():
    assert llm.profile_for("draft a reply to Alice") == "draft"
    assert llm.profile_for("plan my week from this inbox") == "reasoning"
    assert llm.profile_for("triage the inbox") == "classifier"
    assert llm.profile_for("find invoices from Acme") == "fast"
    assert llm.profile_for("hello") == "default"


class StubAgent:
    def __init__(self, delay=0.3, fail=False):
        self.delay = delay
        self.fail = fail
        self.cancelled = threading.Event()

    def chat(self, text):
        from mailkeep.session import Outcome, Response
        self.cancelled.wait(self.delay)
        # Like the real agent: a cancelled call aborts instead of succeeding.
        if self.cancelled.is_set():
            raise InterruptedError("Stopped")
        if self.fail:
            raise RuntimeError("boom")
        return Outcome([Response(f"done:{text}")])

    def cancel(self):
        self.cancelled.set()
        return True


def test_task_pool_limits_workers():
    pool = TaskPool(1)
    first = pool.submit(time.sleep, 0.3)
    assert not first.done()
    with pytest.raises(RuntimeError):
        pool.submit(time.sleep, 0.3)
    first.result(timeout=5)


def test_chat_jobs_are_idempotent_and_cancellable():
    jobs = ChatJobs()
    agent = StubAgent()
    assert jobs.submit(agent, "find invoices", "r1") == {"job_id": "r1"}
    # Same id + same text: the same task, never a second run.
    assert jobs.submit(agent, "find invoices", "r1") == {"job_id": "r1"}
    with pytest.raises(ValueError):
        jobs.submit(agent, "other", "r1")
    with pytest.raises(ValueError):
        jobs.submit(agent, "find invoices", "")
    assert jobs.status("r1")["state"] in ("running", "completed")
    time.sleep(0.5)
    assert jobs.status("r1")["state"] == "completed"
    assert jobs.status("r1")["outcome"]["messages"][0]["text"] == "done:find invoices"
    with pytest.raises(KeyError):
        jobs.status("missing")
    # A running task can be cancelled; the call does not replay afterwards.
    slow = StubAgent(delay=5)
    jobs.submit(slow, "slow task", "r2")
    assert jobs.cancel("r2") is True
    time.sleep(0.2)
    assert jobs.status("r2")["state"] == "failed"
    with pytest.raises(KeyError):
        jobs.cancel("missing")
