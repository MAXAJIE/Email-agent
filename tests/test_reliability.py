"""Retries, health checks, recovery after timeouts, task frames, draft revisions."""
import threading
import time

import pytest

from mailkeep import agent as agent_mod
from mailkeep import fastllm, frames, llm
from mailkeep.demo import DemoGmailClient
from mailkeep.session import Session
from mailkeep.tasks import TaskPool


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(agent_mod, "resolve_mode", lambda: ("openminion", "test"))
    monkeypatch.setattr(llm, "describe", lambda: [])
    monkeypatch.setattr(agent_mod, "trace", lambda kind, text: None)


def rules_agent(monkeypatch):
    monkeypatch.setattr(agent_mod, "resolve_mode", lambda: ("rules", "test"))
    monkeypatch.setattr(llm, "describe", lambda: [])
    return agent_mod.MailkeepAgent(Session(DemoGmailClient()))


# -- retries -------------------------------------------------------------
def test_retries_stop_after_configured_attempts():
    calls = []

    def boom(n):
        calls.append(n)
        raise TimeoutError("slow")

    with pytest.raises(fastllm.RetriesExhausted) as info:
        fastllm.with_retries(boom, what="t", attempts=3)
    assert calls == [1, 2, 3]
    assert info.value.attempts == 3


def test_retry_failures_are_logged(tmp_path, monkeypatch):
    log = tmp_path / "x.log"
    monkeypatch.setattr(fastllm, "LOG_FILE", log)
    with pytest.raises(fastllm.RetriesExhausted):
        fastllm.with_retries(lambda n: (_ for _ in ()).throw(TimeoutError("x")), what="t", attempts=2)
    text = log.read_text()
    assert "attempt 1/2 failed" in text and "giving up after 2" in text


def test_auth_errors_are_not_retried():
    calls = []

    def bad_key(n):
        calls.append(n)
        raise RuntimeError("HTTP 401: invalid api key")

    with pytest.raises(RuntimeError):
        fastllm.with_retries(bad_key, what="t", attempts=3)
    assert calls == [1]


def test_success_on_second_attempt():
    results = iter([TimeoutError("x"), "ok"])

    def flaky(n):
        value = next(results)
        if isinstance(value, Exception):
            raise value
        return value

    assert fastllm.with_retries(flaky, what="t", attempts=3) == "ok"


# -- recovery after timeouts ---------------------------------------------
def test_abandoned_call_frees_its_slot():
    pool = TaskPool(1)
    stuck = pool.submit(time.sleep, 3)
    with pytest.raises(RuntimeError):
        pool.submit(time.sleep, 0)
    pool.abandon(stuck)
    pool.submit(lambda: None).result(timeout=2)  # usable again immediately


def test_new_message_works_after_a_timeout(quiet, monkeypatch):
    monkeypatch.setattr(fastllm, "attempt_timeout", lambda *_: 0.3)
    monkeypatch.setattr(fastllm, "retry_attempts", lambda: 2)
    agent = agent_mod.MailkeepAgent(Session(DemoGmailClient()))

    class Hang:
        def run_stream(self, *a, **k):
            time.sleep(5)

        def close(self):
            pass

    agent._agent = Hang()
    out = agent.chat("how is my inbox looking")
    assert "timed out" in out.responses[0].text and "gave up after 2 attempts" in out.responses[0].text

    class Fine:
        def run_stream(self, *a, **k):
            return type("R", (), {"text": "All good."})()

        def close(self):
            pass

    agent._agent = Fine()
    out = agent.chat("how is my inbox looking")
    assert out.responses[-1].text == "All good."


def test_offline_model_answers_instantly(quiet, monkeypatch):
    agent = agent_mod.MailkeepAgent(Session(DemoGmailClient()))
    monkeypatch.setattr(agent, "_preset_or_none", lambda: object())
    monkeypatch.setattr(agent.health, "_probe", lambda p: fastllm.Health("offline", 0, "refused", time.time()))
    monkeypatch.setattr(agent, "_run_model", lambda *a: pytest.fail("model must not run while offline"))
    started = time.monotonic()
    out = agent.chat("find invoices please")
    assert time.monotonic() - started < 2
    assert "not reachable" in out.responses[0].text


def test_health_monitor_recovers():
    states = iter(["offline", "online"])
    monitor = fastllm.HealthMonitor(lambda: object(),
                                    lambda p: fastllm.Health(next(states), 0, "", time.time()))
    assert monitor.check_now().state == "offline"
    ok, health = monitor.usable()  # negative verdict is re-probed
    assert ok and health.state == "online"


# -- frames ----------------------------------------------------------------
def test_draft_request_is_recognised():
    assert frames.is_draft_request("draft a mail to bob@example.com about Q4") == "draft_new"
    assert frames.is_draft_request("write an email to Alice") == "draft_new"
    assert frames.is_draft_request("draft a reply to 2 saying thanks") == "draft_reply"
    assert frames.is_draft_request("find invoices") == ""


def test_frame_threshold_is_80_percent():
    assert frames.threshold() == 0.8
    full = frames.start("draft_new", "draft a mail to bob@example.com saying the report is late")
    assert full.score() == 0.8 and full.ready()
    no_purpose = frames.start("draft_new", "draft a mail to bob@example.com")
    assert no_purpose.score() == 0.4 and not no_purpose.ready()
    # A subject alone is enough when a model can write the body ...
    subject_only = frames.start("draft_new", "draft a mail to bob@example.com about the Q4 budget")
    assert subject_only.ready(model_available=True)
    # ... but not for the offline template.
    assert not subject_only.ready(model_available=False)


def test_recipient_is_never_guessed():
    frame = frames.start("draft_new", "write an email to Alice saying lunch is moved to 1pm")
    assert not frame.ready()
    assert "Alice" in frame.question()


def test_draft_asks_then_shows_approval_card(monkeypatch):
    agent = rules_agent(monkeypatch)
    ask = agent.chat("draft a mail to bob@example.com")
    assert ask.approval is None and "What should the email say" in ask.responses[0].text
    out = agent.chat("the quarterly report will be one week late")
    assert out.approval is not None, "a draft request must end in an approval card"
    assert "Draft v1" in out.responses[0].text
    done = agent.session.resolve(out.approval.id, True)
    assert "Draft created" in done.responses[0].text
    assert agent.session.backend.drafts[-1]["to"] == "bob@example.com"


def test_draft_never_just_replies_in_chat(monkeypatch):
    agent = rules_agent(monkeypatch)
    out = agent.chat("draft a mail to bob@example.com saying the meeting moved to Friday")
    assert out.approval is not None
    assert not agent.session.backend.drafts  # nothing written before Approve


def test_revision_replaces_pending_draft(monkeypatch):
    agent = rules_agent(monkeypatch)
    first = agent.chat("draft a mail to bob@example.com saying the meeting moved to Friday")
    revised = agent.chat("make it more formal")
    assert revised.approval is not None and revised.approval.id != first.approval.id
    assert "Draft v2 replaces v1" in revised.responses[0].text
    # The first card is dead; only v2 can be approved.
    stale = agent.session.resolve(first.approval.id, True)
    assert "no longer pending" in stale.responses[0].text
    agent.session.resolve(revised.approval.id, True)
    saved = agent.session.backend.drafts[-1]
    assert saved["body"].startswith("Dear") and "Kind regards" in saved["body"]
    assert len(agent.session.backend.drafts) == 1


def test_subject_change_revision(monkeypatch):
    agent = rules_agent(monkeypatch)
    agent.chat("draft a mail to bob@example.com saying the meeting moved to Friday")
    out = agent.chat("change the subject to Meeting update")
    agent.session.resolve(out.approval.id, True)
    assert agent.session.backend.drafts[-1]["subject"] == "Meeting update"


def test_frame_can_be_cancelled(monkeypatch):
    agent = rules_agent(monkeypatch)
    agent.chat("draft a mail to bob@example.com")
    out = agent.chat("cancel")
    assert "Nothing was created" in out.responses[0].text
    assert agent._frame is None


def test_runtime_caps_hidden_retries():
    preset = llm.PRESETS["openai"]
    cfg = llm.build_config(preset)
    assert cfg["runtime"]["provider_retry_max_attempts"] == 1
    assert cfg["runtime"]["agent_loop_max_steps"] <= 10
