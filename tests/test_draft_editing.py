"""Editing a draft that is on screen, and the fuller checklist for new drafts."""
import pytest

from mailkeep import agent as agent_mod
from mailkeep import frames, llm, providers
from mailkeep.demo import DemoGmailClient
from mailkeep.session import Session


@pytest.fixture(autouse=True)
def _strict(monkeypatch):
    monkeypatch.setenv("MAILKEEP_DRAFT_STRICT", "on")


def _agent(monkeypatch, mode="rules"):
    monkeypatch.setattr(agent_mod, "resolve_mode", lambda: (mode, "test"))
    monkeypatch.setattr(llm, "describe", lambda: [])
    monkeypatch.setattr(agent_mod, "trace", lambda kind, text: None)
    return agent_mod.MailkeepAgent(Session(DemoGmailClient()))


def _draft_on_screen(agent):
    """One listed message that is a Gmail draft to max@example.com, shown in full."""
    agent.session.run_command("find invoices limit 1")
    row = agent.session.assistant.last_results[0]
    for email in agent.session.backend.fetch([row.id]):
        email.labels = tuple(set(email.labels) | {"DRAFT"})
        email.to = "Max <max@example.com>"
        email.body = "Hi Max,\n\nThe invoice is attached.\n\nBest regards,"
    agent._focus = agent.session.backend.get_full(row.id)  # as after "tell me the info"
    return row


# -- new drafts: subject, purpose and details are collected ------------------
def test_new_draft_asks_subject_purpose_then_details():
    fr = frames.start("draft_new", "draft a mail to bob@example.com")
    assert not fr.ready(True)
    assert "subject" in fr.question().lower()
    frames.merge_answer(fr, "Q4 budget review")
    assert fr.slots["subject"] == "Q4 budget review" and not fr.slots.get("purpose")
    assert "say" in fr.question().lower()
    frames.merge_answer(fr, "invite him to the review meeting")
    assert not fr.ready(True)
    assert "details" in fr.question().lower()
    frames.merge_answer(fr, "Friday 3pm in room 2, bring the spreadsheet")
    assert fr.slots["details"] == "Friday 3pm in room 2, bring the spreadsheet"
    assert fr.ready(True)


def test_details_can_be_skipped():
    fr = frames.start("draft_new", "draft a mail to bob@example.com about lunch saying lunch moved to 1pm")
    assert not fr.ready(True)
    assert "details" in fr.question().lower()
    frames.merge_answer(fr, "none")
    assert fr.slots["details"] == "(none)" and fr.ready(True)


def test_recipient_still_never_guessed():
    assert not frames.start("draft_new", "draft about lunch saying lunch moved, details: 1pm at Joe's").ready(True)


def test_strict_off_keeps_old_one_line_rule(monkeypatch):
    monkeypatch.setenv("MAILKEEP_DRAFT_STRICT", "off")
    assert frames.start("draft_new", "draft a mail to bob@example.com saying the report is late").ready()


def test_full_new_draft_flow_ends_in_card(monkeypatch):
    agent = _agent(monkeypatch)
    assert "subject" in agent.chat("draft a mail to bob@example.com").responses[0].text.lower()
    agent.chat("Team lunch")
    agent.chat("tell the team lunch moved to 1pm")
    out = agent.chat("none")
    assert out.approval is not None
    agent.session.resolve(out.approval.id, True)
    saved = agent.session.backend.drafts[-1]
    assert saved["to"] == "bob@example.com" and saved["subject"] == "Team lunch"


# -- editing the draft on screen (screenshot: "change the subject") ----------
def test_change_the_subject_of_draft_on_screen_asks_for_value(monkeypatch):
    agent = _agent(monkeypatch)
    _draft_on_screen(agent)
    ask = agent.chat("change the subject")
    assert ask.approval is None
    assert "new subject" in ask.responses[0].text.lower()
    out = agent.chat("Invoice for October")
    assert out.approval is not None
    agent.session.resolve(out.approval.id, True)
    saved = agent.session.backend.drafts[-1]
    assert saved["subject"] == "Invoice for October"
    assert saved["to"] == "max@example.com"
    assert "The invoice is attached." in saved["body"]


def test_change_subject_with_value_in_one_go(monkeypatch):
    agent = _agent(monkeypatch)
    _draft_on_screen(agent)
    out = agent.chat("change the subject to Payment reminder")
    assert out.approval is not None
    agent.session.resolve(out.approval.id, True)
    assert agent.session.backend.drafts[-1]["subject"] == "Payment reminder"


def test_thats_the_draft_reuses_previous_instruction(monkeypatch):
    agent = _agent(monkeypatch)
    row = _draft_on_screen(agent)
    agent._focus = None
    agent.session.assistant.last_results = []
    agent.chat("change the subject to Hello")  # nothing on screen yet
    agent._focus = agent.session.backend.get_full(row.id)
    out = agent.chat("thats the draft")
    assert out.approval is not None
    agent.session.resolve(out.approval.id, True)
    assert agent.session.backend.drafts[-1]["subject"] == "Hello"


def test_model_writes_subject_when_asked_to_improve_it(monkeypatch):
    agent = _agent(monkeypatch, "openminion")
    monkeypatch.setattr(providers, "chain", lambda profile: [("p", "")])
    monkeypatch.setattr(agent, "_model_ready", lambda step=None: (True, ""))

    def complete(profile, system, user, **kw):
        if "into JSON" in system:
            return '{"subject": "October invoice attached", "to": null, "body_change": false}', None
        return "", None
    monkeypatch.setattr(providers, "complete", complete)
    _draft_on_screen(agent)
    out = agent.chat("make the subject clearer")
    assert out.approval is not None
    agent.session.resolve(out.approval.id, True)
    assert agent.session.backend.drafts[-1]["subject"] == "October invoice attached"


def test_received_mail_edit_asks_for_recipient(monkeypatch):
    agent = _agent(monkeypatch)
    agent.session.run_command("find invoices limit 1")
    row = agent.session.assistant.last_results[0]
    agent._focus = agent.session.backend.get_full(row.id)
    out = agent.chat("change the subject to Re-sent invoice")
    assert out.approval is None and "email address" in out.responses[0].text
    out = agent.chat("carol@example.com")
    assert out.approval is not None
    agent.session.resolve(out.approval.id, True)
    saved = agent.session.backend.drafts[-1]
    assert saved["to"] == "carol@example.com" and saved["subject"] == "Re-sent invoice"
