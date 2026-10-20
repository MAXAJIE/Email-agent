"""Visible thinking steps, speed routing and the off-topic guard."""
import threading
import time

from mailkeep import llm
from mailkeep.tasks import ChatJobs


def test_off_topic_detection():
    assert llm.is_off_topic("what's the weather like in Kuala Lumpur today?")
    assert llm.is_off_topic("tell me a joke about cats please")
    assert llm.is_off_topic("how do I cook rice properly?")
    assert not llm.is_off_topic("archive all newsletters")
    assert not llm.is_off_topic("how many unread emails do I have?")
    assert not llm.is_off_topic("hi")
    assert not llm.is_off_topic("help")


def test_topic_guard_toggle(monkeypatch):
    assert llm.topic_guard_enabled() is True
    monkeypatch.setattr(
        llm, "setting",
        lambda name: "off" if name == "MAILKEEP_TOPIC_GUARD" else "")
    assert llm.topic_guard_enabled() is False


def test_short_messages_use_the_fast_profile():
    assert llm.profile_for("hello") == "fast"
    assert llm.profile_for("thanks a lot") == "fast"
    assert llm.profile_for("draft a reply") == "draft"
    assert llm.profile_for(
        "can you give me some thoughts about how my week looked overall "
        "and what patterns you notice") == "default"


def test_off_topic_chat_never_calls_the_model(monkeypatch):
    from mailkeep import agent as agent_mod
    from mailkeep.demo import DemoGmailClient
    from mailkeep.session import Session

    monkeypatch.setattr(agent_mod, "resolve_mode", lambda: ("openminion", "test"))
    monkeypatch.setattr(agent_mod, "trace", lambda kind, text: None)
    monkeypatch.setattr(llm, "describe", lambda: [])

    agent = agent_mod.MailkeepAgent(Session(DemoGmailClient()))

    def no_model(*args, **kwargs):
        raise AssertionError("the model must not run for an off-topic request")

    monkeypatch.setattr(agent, "_run_model", no_model)
    steps = []
    out = agent.chat("what's the weather today?", on_step=steps.append)
    assert "Gmail assistant" in out.responses[0].text
    assert out.approval is None
    assert steps  # the topic check was reported as a thinking step


class StepAgent:
    def __init__(self):
        self.cancelled = threading.Event()

    def chat(self, text, on_step=None):
        from mailkeep.session import Outcome, Response
        if on_step:
            on_step("Thinking")
            on_step("Done")
        return Outcome([Response(f"done:{text}")])

    def cancel(self):
        self.cancelled.set()
        return True


def test_job_steps_are_reported():
    jobs = ChatJobs()
    jobs.submit(StepAgent(), "find invoices", "s1")
    time.sleep(0.3)
    status = jobs.status("s1")
    assert status["state"] == "completed"
    assert status["steps"] == ["Thinking", "Done"]
