"""Patch 4: action-aware triage intent + draft-feedback revisions."""
import pytest

from mailkeep import agent as agent_mod
from mailkeep import frames, llm
from mailkeep.demo import DemoGmailClient
from mailkeep.intents import is_triage, parse_intent
from mailkeep.session import Session


def rules_agent(monkeypatch):
    monkeypatch.setattr(agent_mod, "resolve_mode", lambda: ("rules", "test"))
    monkeypatch.setattr(llm, "describe", lambda: [])
    return agent_mod.MailkeepAgent(Session(DemoGmailClient()))


def test_triage_phrasing_is_not_the_trash_folder():
    text = "analyze in the mail, and tell me which is most likely a trash"
    assert is_triage(text)
    assert parse_intent(text).kind == "triage"
    assert not is_triage("what's in the trash")
    assert not is_triage("clean up my trash")
    assert parse_intent("what's in the trash").kind == "trash_report"
    assert parse_intent("empty the trash").kind == "trash_report"


def test_triage_proposes_then_runs_after_confirmation(monkeypatch):
    agent = rules_agent(monkeypatch)
    first = agent.chat("analyze in the mail, and tell me which is most likely a trash")
    assert first.approval is not None
    assert first.approval.phase == "interpret"
    assert "look like junk" in first.approval.question
    assert agent.session.assistant.last_results == []  # nothing executed yet
    done = agent.chat("yes")
    assert done.approval is None
    assert "look like junk" in done.responses[0].text
    assert done.responses[0].table is not None and done.responses[0].table.rows


def test_triage_reuses_last_results_when_list_is_empty(monkeypatch):
    agent = rules_agent(monkeypatch)
    agent.chat("find is:unread")
    agent.chat("which of my messages is most likely a trash?")
    agent.chat("yes")
    assistant = agent.session.assistant
    assert isinstance(assistant.last_results, list)


def test_draft_feedback_still_bad_reaches_revision(monkeypatch):
    agent = rules_agent(monkeypatch)
    first = agent.chat("draft a mail to bob@example.com saying the meeting moved to Friday")
    assert first.approval is not None
    revised = agent.chat("still bad")
    assert revised.approval is not None, "the draft card must stay pending"
    text = revised.responses[0].text
    assert "Review the pending action first." not in text
    assert "draft" in text.lower() or "change" in text.lower()


def test_draft_feedback_show_a_better_mail_reaches_revision(monkeypatch):
    agent = rules_agent(monkeypatch)
    agent.chat("draft a mail to bob@example.com saying the meeting moved to Friday")
    revised = agent.chat("show a better mail")
    assert revised.approval is not None
    assert "Review the pending action first." not in revised.responses[0].text


def test_new_draft_request_is_still_not_a_revision(monkeypatch):
    agent = rules_agent(monkeypatch)
    agent.chat("draft a mail to bob@example.com saying the meeting moved to Friday")
    assert not frames.is_revision("draft a new email to alice@example.com")
    assert frames.is_revision("make it more formal")
    assert frames.is_revision("still bad")
    assert frames.is_revision("show a better mail")
