from mailkeep.demo import DemoGmailClient
from mailkeep.session import Session


def names(backend):
    return {label.name for label in backend.labels()}


def test_write_waits_for_approval_then_applies():
    backend = DemoGmailClient()
    session = Session(backend)
    session.run_command("find invoices")
    outcome = session.run_command("label 1-2 as Finance")
    assert outcome.approval is not None
    assert "Finance" not in names(backend)  # nothing written before approval
    done = session.resolve(outcome.approval.id, True)
    assert "Added to 2 message(s)" in done.responses[0].text
    assert "Finance" in names(backend)


def test_cancel_writes_nothing():
    backend = DemoGmailClient()
    session = Session(backend)
    session.run_command("find invoices")
    outcome = session.run_command("label 1 as Finance")
    assert session.resolve(outcome.approval.id, False).responses[0].text.startswith("Cancelled")
    assert "Finance" not in names(backend)


def test_mark_read_requires_approval():
    session = Session(DemoGmailClient())
    session.run_command("find unread emails")
    assert session.run_command("mark 1 read").approval is not None


def test_wrong_approval_id_is_rejected():
    session = Session(DemoGmailClient())
    session.run_command("find invoices")
    session.run_command("archive 1")
    assert "no longer pending" in session.resolve("nope", True).responses[0].text


def test_draft_followup_then_approve():
    session = Session(DemoGmailClient())
    session.run_command("find invoices")
    ask = session.run_command("draft a reply to 1")
    assert ask.approval is None
    pending = session.run_command("Thanks, will pay Friday.")
    assert pending.approval is not None
    assert "Draft created" in session.resolve(pending.approval.id, True).responses[0].text


def test_agent_falls_back_to_rules_without_model(monkeypatch):
    from mailkeep.agent import MailkeepAgent

    monkeypatch.setenv("MAILKEEP_AGENT", "rules")
    agent = MailkeepAgent(Session(DemoGmailClient()))
    assert agent.mode == "rules"
    out = agent.chat("find invoices")
    assert out.approval is not None
    done = agent.chat("yes")
    assert done.responses[-1].table is not None
