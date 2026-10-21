"""An opened Gmail draft must not inherit an older reply's fields."""
import pytest

from mailkeep import agent as agent_mod, llm
from mailkeep.assistant import normalize_body
from mailkeep.demo import DemoGmailClient
from mailkeep.session import Session


BODY = "Hi,\n\nTurn your selfie into a studio-quality profile picture with ChatGPT Images.\n\nBest regards,\nMax Lagume"


def setup_agent(monkeypatch, pending=False):
    monkeypatch.setattr(agent_mod, "resolve_mode", lambda: ("rules", "test"))
    monkeypatch.setattr(llm, "describe", lambda: [])
    monkeypatch.setattr(agent_mod, "trace", lambda *args: None)
    agent = agent_mod.MailkeepAgent(Session(DemoGmailClient()))
    agent.session.run_command("find invoices limit 1")
    row = agent.session.assistant.last_results[0]
    mail = agent.session.backend.get_full(row.id)
    mail.labels = ("DRAFT",)
    mail.to = "chirilingjie274@gmail.com"
    mail.subject = "english too"
    mail.body = BODY
    stale = {"to": "noreply@email.openai.com", "subject": "Old reply", "body": "",
             "thread_id": "old-thread", "in_reply_to": "old-message", "references": "old-reference"}
    agent._last_draft = {"draft": stale, "body": "안녕하세요\n\n감사합니다.", "version": 2}
    if pending:
        agent.session.propose_draft(stale, agent._last_draft["body"])
    agent._focus = mail
    return agent, mail


@pytest.mark.parametrize("pending", [False, True])
def test_screenshot_edit_it_then_change_subject(monkeypatch, pending):
    agent, mail = setup_agent(monkeypatch, pending)
    old_card = agent.session.approval
    opened = agent.chat("edit it")
    assert opened.approval is None
    assert agent._last_draft["origin"] == mail.id
    assert agent.session.backend.drafts == []
    changed = agent.chat("change subject to openai announcement")
    assert changed.approval is not None
    agent.session.resolve(changed.approval.id, True)
    saved = agent.session.backend.drafts[-1]
    assert saved["subject"] == "openai announcement"
    assert saved["to"] == "chirilingjie274@gmail.com"
    assert saved["body"] == normalize_body(BODY)
    assert saved.get("thread_id", "") == ""
    assert mail.subject == "english too" and mail.body == BODY
    if old_card:
        agent.session.resolve(old_card.id, True)
        assert len(agent.session.backend.drafts) == 1


def test_header_edit_without_edit_it_rebinds_new_mail(monkeypatch):
    agent, mail = setup_agent(monkeypatch, True)
    result = agent.chat("change subject to openai announcement")
    assert result.approval is not None
    assert agent._last_draft["origin"] == mail.id
    assert agent._last_draft["draft"]["to"] == "chirilingjie274@gmail.com"


@pytest.mark.parametrize("edit_text", ["edit it", "edit this", "modify that", "edit the draft"])
def test_generic_edit_waits_without_rewriting(monkeypatch, edit_text):
    agent, mail = setup_agent(monkeypatch)
    monkeypatch.setattr(agent, "_write_body", lambda *args: pytest.fail("Generic edit must not write"))
    agent.chat(edit_text)
    assert agent._last_draft["body"] == BODY
    assert agent._last_draft["origin"] == mail.id
    assert agent.session.approval is None


def test_latest_open_beats_old_reader_focus(monkeypatch):
    agent, mail = setup_agent(monkeypatch)
    agent.session.run_command("find invoices limit 2")
    second = agent.session.backend.get_full(agent.session.assistant.last_results[1].id)
    second.labels = ("DRAFT",)
    second.to = "second@example.com"
    second.body = "Second draft body"
    agent.session.run_command("open 2")
    agent.chat("edit it")
    assert agent._last_draft["origin"] == second.id
    assert agent._last_draft["draft"]["to"] == "second@example.com"


def test_header_only_does_not_apply_signature_cleanup(monkeypatch):
    agent, mail = setup_agent(monkeypatch)
    mail.body = "Hello **team**,\n\n[Recipient] is a literal field.\n\nBest,\nOriginal author"
    agent._user_name = "Different author"
    agent.chat("edit it")
    out = agent.chat("change subject to openai announcement")
    agent.session.resolve(out.approval.id, True)
    assert agent.session.backend.drafts[-1]["body"] == normalize_body(mail.body)


def test_new_edit_drops_stale_plan(monkeypatch):
    agent, mail = setup_agent(monkeypatch, True)
    agent._plan = {"waiting": "approval", "next": 1}
    agent._scratch = {"text": "Korean reply"}
    agent.chat("edit it")
    assert agent._plan is None and agent._scratch is None


def test_repeated_revision_keeps_selected_draft(monkeypatch):
    agent, mail = setup_agent(monkeypatch)
    agent.chat("edit it")
    first = agent.chat("change subject to openai announcement")
    second = agent.chat("change subject to Final announcement")
    assert second.approval.id != first.approval.id
    assert agent._last_draft["origin"] == mail.id
    assert agent._last_draft["draft"]["subject"] == "Final announcement"
    assert agent._last_draft["body"] == BODY
