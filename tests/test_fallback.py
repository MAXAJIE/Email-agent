"""A provider failure answers with the rule parser instead of aborting the turn."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mailkeep import agent as agent_mod
from mailkeep import llm
from mailkeep.demo import DemoGmailClient
from mailkeep.session import Session


@pytest.fixture
def openminion():
    return pytest.importorskip("openminion")


@pytest.fixture
def quiet(monkeypatch):
    """Keep the agent deterministic: no config probing, no trace spam."""
    monkeypatch.setattr(agent_mod, "resolve_mode", lambda: ("openminion", "test"))
    monkeypatch.setattr(llm, "describe", lambda: [])
    monkeypatch.setattr(agent_mod, "trace", lambda kind, text: None)


def failing_agent(exc, session) -> agent_mod.MailkeepAgent:
    """A MailkeepAgent whose OpenMinion turn raises `exc`."""

    class Boom:
        def run_stream(self, *args, **kwargs):
            raise exc

        def close(self):
            pass

    agent = agent_mod.MailkeepAgent(session)
    agent._agent = Boom()
    return agent


def test_provider_error_falls_back_to_rules(openminion, quiet):
    session = Session(DemoGmailClient())
    error = openminion.ProviderError("upstream returned no content", code="PROVIDER_TIMEOUT")
    out = failing_agent(error, session).chat("find invoices")

    texts = [response.text for response in out.responses]
    assert texts[0] == (
        "Model provider failed (PROVIDER_TIMEOUT): upstream returned no content. "
        "Answered with the rule-based parser instead."
    )
    # The user still gets an answer from the deterministic parser.
    assert len(texts) > 1
    assert any("invoice" in text.lower() for text in texts[1:])


def test_timeout_falls_back_to_rules(openminion, quiet):
    session = Session(DemoGmailClient())
    out = failing_agent(openminion.TurnTimeoutError("turn exceeded 180s"), session).chat(
        "find invoices"
    )

    texts = [response.text for response in out.responses]
    assert texts[0].startswith("Agent turn timed out (turn exceeded 180s).")
    assert len(texts) > 1


def test_other_errors_are_not_swallowed(quiet):
    session = Session(DemoGmailClient())
    with pytest.raises(ValueError, match="boom"):
        failing_agent(ValueError("boom"), session).chat("find invoices")


def test_no_rule_answer_while_a_write_is_pending(openminion, quiet):
    class PendingSession:
        assistant = SimpleNamespace(pending=None)
        approval = object()

        def __init__(self):
            self.calls: list[str] = []

        def run_command(self, text):
            self.calls.append(text)
            raise AssertionError("the rule parser must not run while approval is pending")

    session = PendingSession()
    out = failing_agent(openminion.ProviderError("no model"), session).chat("archive 1")

    assert session.calls == []
    # A pending approval blocks the turn before the model is even attempted.
    assert len(out.responses) == 1 and "pending approval" in out.responses[0].text
