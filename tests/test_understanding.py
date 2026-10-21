"""Understand the request before acting: references, content tasks, multi-step plans."""
import pytest

from mailkeep import agent as agent_mod
from mailkeep import llm, providers, router, understand
from mailkeep.demo import DemoGmailClient
from mailkeep.session import Session


def _agent(monkeypatch, mode="rules"):
    monkeypatch.setattr(agent_mod, "resolve_mode", lambda: (mode, "test"))
    monkeypatch.setattr(llm, "describe", lambda: [])
    monkeypatch.setattr(agent_mod, "trace", lambda kind, text: None)
    agent = agent_mod.MailkeepAgent(Session(DemoGmailClient()))
    return agent


def _one_row(agent):
    agent.session.run_command("find invoices limit 1")
    rows = agent.session.assistant.last_results
    assert len(rows) == 1
    return rows[0]


def _model(monkeypatch, agent, reply="MODEL OUTPUT"):
    calls = []
    monkeypatch.setattr(providers, "chain", lambda profile: [("p", "")])

    def complete(profile, system, user, **kw):
        calls.append((profile, system, user))
        return reply, None
    monkeypatch.setattr(providers, "complete", complete)
    monkeypatch.setattr(agent, "_model_ready", lambda step=None: (True, ""))
    return calls


# -- screenshot 1: "copy the info and translate to english. Analyze it briefly"
def test_translate_request_is_not_refused_as_unrelated(monkeypatch):
    agent = _agent(monkeypatch, "openminion")
    row = _one_row(agent)
    calls = _model(monkeypatch, agent, "Translation: ...\nAnalysis: ...")
    out = agent.chat("copy the info and translate to english. Analyze it briefly")
    text = out.responses[0].text
    assert llm.OFF_TOPIC_REPLY not in text
    assert "Translation: ..." in text and row.subject in text
    assert calls and row.snippet in calls[-1][2], "the model must see the email content"
    assert "translate to English" in out.responses[0].hint


def test_off_topic_still_refused_without_mail_context(monkeypatch):
    agent = _agent(monkeypatch, "openminion")
    out = agent.chat("tell me a joke about cats please")
    assert out.responses[0].text == llm.OFF_TOPIC_REPLY


def test_copy_works_offline_verbatim(monkeypatch):
    agent = _agent(monkeypatch)
    row = _one_row(agent)
    out = agent.chat("copy the content")
    assert row.snippet in out.responses[0].text


def test_ambiguous_reference_asks_which(monkeypatch):
    agent = _agent(monkeypatch)
    agent.session.run_command("find invoices")
    assert len(agent.session.assistant.last_results) > 1
    out = agent.chat("translate it to english")
    assert "Which message" in out.responses[0].text


# -- screenshot 2: "copy the content and draft a similar mail to x@gmail.com"
def test_similar_draft_uses_the_listed_email(monkeypatch):
    agent = _agent(monkeypatch, "openminion")
    row = _one_row(agent)
    calls = _model(monkeypatch, agent, "Hi,\n\nSimilar body.\n\nBest regards,\n[Your Name]")
    out = agent.chat("copy the content and draft a similar mail to chinlingnie274@gmail.com")
    assert out.approval is not None
    prompt = calls[-1][2]
    assert row.snippet in prompt, "the source email must be in the draft prompt"
    agent.session.resolve(out.approval.id, True)
    saved = agent.session.backend.drafts[-1]
    assert saved["to"] == "chinlingnie274@gmail.com"
    assert saved["subject"] == row.subject  # not "Copy the content and draft a similar"
    assert "[Your Name]" not in saved["body"]


def test_forward_gets_fwd_subject_offline(monkeypatch):
    agent = _agent(monkeypatch)
    row = _one_row(agent)
    out = agent.chat("forward this mail to bob@example.com")
    agent.session.resolve(out.approval.id, True)
    saved = agent.session.backend.drafts[-1]
    assert saved["subject"] == f"Fwd: {row.subject}"
    assert row.snippet in saved["body"]


def test_explicit_purpose_does_not_attach_a_source(monkeypatch):
    agent = _agent(monkeypatch)
    _one_row(agent)
    out = agent.chat("draft a mail to bob@example.com saying the meeting moved to Friday")
    agent.session.resolve(out.approval.id, True)
    assert "meeting moved to Friday" in agent.session.backend.drafts[-1]["body"]


# -- multi-step: content task, then "draft that to ..."
def test_translation_then_draft_carries_material(monkeypatch):
    agent = _agent(monkeypatch, "openminion")
    _one_row(agent)
    calls = _model(monkeypatch, agent, "ENGLISH TRANSLATION")
    agent.chat("translate it to english")
    out = agent.chat("draft the translation to bob@example.com")
    assert out.approval is not None
    assert "ENGLISH TRANSLATION" in calls[-1][2]


def test_router_plan_runs_steps_in_order(monkeypatch):
    agent = _agent(monkeypatch, "openminion")
    _one_row(agent)
    monkeypatch.setenv("MAILKEEP_LLM_ROUTER", "on")
    plan = ('{"action":"plan","steps":['
            '{"agent":"reader","args":{"ref":"1","task":"translate to English"}},'
            '{"agent":"drafter","args":{"to":"bob@example.com","instructions":"send the translation"}}]}')
    seen = []
    monkeypatch.setattr(providers, "chain", lambda profile: [("p", "")])

    def complete(profile, system, user, **kw):
        seen.append(user)
        if "coordinator" in system:
            return plan, type("P", (), {"label": "x", "model": "y"})()
        return ("TRANSLATED" if len(seen) == 2 else "Hi Bob,\n\nTRANSLATED\n\nBest,"), None
    monkeypatch.setattr(providers, "complete", complete)
    monkeypatch.setattr(agent, "_model_ready", lambda step=None: (True, ""))
    out = agent.chat("grab that newsletter, put it in english and send it over to bob@example.com")
    assert out.responses[0].text.startswith("Plan: 1.")
    assert out.approval is not None
    assert "TRANSLATED" in seen[-1], "reader output must feed the drafter"


def test_router_parses_plans():
    d = router.parse('{"action":"plan","steps":[{"agent":"reader","args":{"task":"t"}},'
                     '{"agent":"mailbox","args":{"command":"archive 1"}}]}')
    assert d.action == "plan" and [s[0] for s in d.steps] == ["reader", "mailbox"]
    single = router.parse('{"action":"plan","steps":[{"agent":"reader","args":{"task":"t"}}]}')
    assert single.action == "agent" and single.agent == "reader"
    with pytest.raises(ValueError):
        router.parse('{"action":"plan","steps":[]}')


def test_plan_steps_split_multi_action():
    steps = understand.plan_steps("copy a mail, translate it to english and draft it to x@gmail.com")
    assert steps == ["copy the content", "translate to English",
                     "draft an email to x@gmail.com (saved only after you approve)"]


def test_placeholders_are_removed():
    assert understand.strip_placeholders("Hi,\n\nBody.\n\nBest regards,\n[Your Name]") == \
        "Hi,\n\nBody.\n\nBest regards,"


# -- number confirmation -------------------------------------------------
def test_number_answer_runs_the_waiting_task(monkeypatch):
    agent = _agent(monkeypatch)
    agent.session.run_command("find invoices")
    rows = agent.session.assistant.last_results
    ask = agent.chat("copy the content")
    assert "Which message" in ask.responses[0].text
    out = agent.chat("2")
    assert rows[1].snippet in out.responses[0].text


def test_out_of_range_number_asks_again(monkeypatch):
    agent = _agent(monkeypatch)
    agent.session.run_command("find invoices")
    agent.chat("copy the content")
    out = agent.chat("99")
    assert "number from 1 to" in out.responses[0].text


# -- auto-continue after Approve -----------------------------------------
def _plan_agent(monkeypatch, plan):
    agent = _agent(monkeypatch, "openminion")
    _one_row(agent)
    monkeypatch.setenv("MAILKEEP_LLM_ROUTER", "on")
    monkeypatch.setattr(providers, "chain", lambda profile: [("p", "")])

    def complete(profile, system, user, **kw):
        if "coordinator" in system:
            return plan, type("P", (), {"label": "x", "model": "y"})()
        return "Hi Bob,\n\nBody.\n\nBest,", None
    monkeypatch.setattr(providers, "complete", complete)
    monkeypatch.setattr(agent, "_model_ready", lambda step=None: (True, ""))
    return agent


PLAN_ARCHIVE_THEN_DRAFT = ('{"action":"plan","steps":['
    '{"agent":"mailbox","args":{"command":"archive 1"}},'
    '{"agent":"drafter","args":{"to":"bob@example.com","instructions":"tell bob it is archived"}}]}')


def test_plan_continues_after_approve(monkeypatch):
    agent = _plan_agent(monkeypatch, PLAN_ARCHIVE_THEN_DRAFT)
    out = agent.chat("archive it and then draft a note to bob@example.com")
    assert out.approval is not None and "archive" in out.approval.command
    after = agent.session.resolve(out.approval.id, True)  # same entry the UI uses
    texts = " ".join(r.text for r in after.responses)
    assert "Continuing the plan with step 2/2" in texts
    assert after.approval is not None, "the draft step must now show its card"
    agent.session.resolve(after.approval.id, True)
    assert agent.session.backend.drafts[-1]["to"] == "bob@example.com"


def test_plan_stops_when_cancelled(monkeypatch):
    agent = _plan_agent(monkeypatch, PLAN_ARCHIVE_THEN_DRAFT)
    out = agent.chat("archive it and then draft a note to bob@example.com")
    after = agent.session.resolve(out.approval.id, False)
    assert "Plan stopped" in after.responses[-1].text
    assert after.approval is None and not agent.session.backend.drafts


def test_typed_yes_also_continues(monkeypatch):
    agent = _plan_agent(monkeypatch, PLAN_ARCHIVE_THEN_DRAFT)
    agent.chat("archive it and then draft a note to bob@example.com")
    after = agent.chat("yes")
    assert any("Continuing the plan" in r.text for r in after.responses)
