"""Draft preview matches the saved draft; subject translation; sender name."""
from mailkeep import agent as agent_mod
from mailkeep import llm, providers, understand
from mailkeep.assistant import normalize_body
from mailkeep.demo import DemoGmailClient
from mailkeep.session import Session

BODY = "Hi Bob,\r\n\r\n*** Line one.   \n\n\n\nLine two, with punctuation!\n***\n\nBest regards,"


def _agent(monkeypatch, mode="rules"):
    monkeypatch.setattr(agent_mod, "resolve_mode", lambda: (mode, "test"))
    monkeypatch.setattr(llm, "describe", lambda: [])
    monkeypatch.setattr(agent_mod, "trace", lambda kind, text: None)
    return agent_mod.MailkeepAgent(Session(DemoGmailClient()))


def test_preview_shows_the_exact_saved_body(monkeypatch):
    agent = _agent(monkeypatch)
    draft = {"to": "bob@example.com", "subject": "S", "body": "", "thread_id": "",
             "in_reply_to": "", "references": ""}
    out = agent.session.propose_draft(draft, BODY)
    preview = out.responses[0].text
    agent.session.resolve(out.approval.id, True)
    saved = agent.session.backend.drafts[-1]["body"]
    assert saved == normalize_body(BODY)
    assert preview.endswith("\n\n" + saved), "preview body must be byte-identical to the draft"
    assert "Line one.\n\nLine two" in saved  # line breaks kept, runs of blanks collapsed


def test_long_body_is_not_truncated_in_preview(monkeypatch):
    agent = _agent(monkeypatch)
    body = "Hi,\n\n" + ("word " * 300).strip() + "\n\nBest,"
    out = agent.session.propose_draft({"to": "a@b.co", "subject": "S", "body": "", "thread_id": "",
                                       "in_reply_to": "", "references": ""}, body)
    assert out.responses[0].text.endswith(body)


def _model(monkeypatch, agent, fn):
    monkeypatch.setattr(providers, "chain", lambda profile: [("p", "")])
    monkeypatch.setattr(providers, "complete", lambda profile, system, user, **kw: (fn(system, user), None))
    monkeypatch.setattr(agent, "_model_ready", lambda step=None: (True, ""))


def test_subject_to_english_translates_and_signs_name(monkeypatch):
    agent = _agent(monkeypatch, "openminion")
    _model(monkeypatch, agent, lambda system, user:
           "Studio-quality photo from one selfie" if "Translate this email subject" in system
           else "Hi,\n\nBody text.\n\nBest regards,\n[Your Name]")
    first = agent.chat("draft a mail to bob@example.com about 셀카 한 장으로 스튜디오급 완성")
    out = agent.chat("change the subject to english too. My name is Max Lagume")
    agent.session.resolve(out.approval.id, True)
    saved = agent.session.backend.drafts[-1]
    assert first.approval is not None
    assert saved["subject"] == "Studio-quality photo from one selfie"
    assert saved["body"].endswith("Best regards,\nMax Lagume")
    assert "[Your Name]" not in saved["body"]


def test_literal_subject_change_still_literal(monkeypatch):
    agent = _agent(monkeypatch)
    agent.chat("draft a mail to bob@example.com saying the meeting moved to Friday")
    out = agent.chat("change the subject to Meeting update")
    agent.session.resolve(out.approval.id, True)
    assert agent.session.backend.drafts[-1]["subject"] == "Meeting update"


def test_helpers():
    assert understand.subject_language("english too") == "English"
    assert understand.subject_language("Meeting update") == ""
    assert understand.user_name("ok. My name is Max Lagume") == "Max Lagume"
    assert understand.ensure_signature("Hi,\n\nX.\n\nBest regards", "Max") == "Hi,\n\nX.\n\nBest regards,\nMax"
    assert understand.plain_text("**Hello** there\n## Title") == "Hello there\nTitle"
