"""Intent -> grounded proposal -> explicit approval -> actual result."""
from types import SimpleNamespace

import pytest

from mailkeep.agent import MailkeepAgent
from mailkeep.demo import DemoGmailClient
from mailkeep.gmail import GmailClient
from mailkeep.intents import parse_intent
from mailkeep.models import Label
from mailkeep.session import Session


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setenv("MAILKEEP_AGENT", "rules")
    backend = DemoGmailClient()
    backend._labels.add("Label_1")
    backend._label_names["Label_1"] = "to-do"
    backend._emails[0].labels += ("Label_1",)
    session = Session(backend)
    agent = MailkeepAgent(session)
    session.run_command("labels")
    yield backend, session, agent
    agent.close()


@pytest.mark.parametrize("utterance", ["what is included in to-do",
    "show me what is included in to-do label", "show label to-do",
    "list messages under the to-do label"])
def test_label_contents_wait_for_confirmation(mailbox, utterance):
    backend, session, agent = mailbox
    outcome = agent.chat(utterance)
    assert outcome.approval is not None
    assert outcome.approval.target_label_id == "Label_1"
    assert session.assistant.last_results == []
    done = agent.chat("yes")
    assert done.approval is None
    assert [x.id for x in session.assistant.last_results] == [backend._emails[0].id]


@pytest.mark.parametrize("utterance", ["change the to-do label to to-complete",
    "rename label to-do to to-complete", "rename to-do to to-complete"])
def test_rename_is_not_search_and_preserves_id(mailbox, utterance):
    backend, session, agent = mailbox
    memberships = [x.labels for x in backend._emails]
    outcome = agent.chat(utterance)
    assert outcome.approval is not None
    assert parse_intent(outcome.approval.command).kind == "rename_label"
    assert backend._label_names["Label_1"] == "to-do"
    agent.chat("yes")
    assert backend._label_names["Label_1"] == "to-complete"
    assert [x.labels for x in backend._emails] == memberships
    browse = agent.chat("what is included in to-complete")
    assert browse.approval.target_label_id == "Label_1"


def test_rename_cancel_and_stale_approval(mailbox):
    backend, session, agent = mailbox
    approval = agent.chat("change the to-do label to to-complete").approval
    agent.chat("cancel")
    session.resolve(approval.id, True)
    assert backend._label_names["Label_1"] == "to-do"


def test_label_identity_change_blocks_write(mailbox):
    backend, session, agent = mailbox
    approval = agent.chat("rename label to-do to to-complete").approval
    backend._labels.remove("Label_1")
    backend._labels.add("Label_2")
    backend._label_names["Label_2"] = "to-do"
    session.resolve(approval.id, True)
    assert backend._label_names["Label_2"] == "to-do"


def test_label_name_change_blocks_read(mailbox):
    backend, session, agent = mailbox
    approval = agent.chat("what is included in to-do").approval
    backend._label_names["Label_1"] = "other"
    session.resolve(approval.id, True)
    assert session.assistant.last_results == []


def test_unknown_label_asks_instead_of_search(mailbox):
    backend, session, agent = mailbox
    out = agent.chat("show label missing")
    assert out.approval is None
    assert session.assistant.last_results == []
    assert "missing" not in {x.name for x in backend.labels()}


def test_system_label_cannot_be_renamed(mailbox):
    backend, session, agent = mailbox
    out = agent.chat("rename label INBOX to to-complete")
    assert out.approval is None
    assert "INBOX" in backend._labels


def test_collision_cannot_be_renamed(mailbox):
    backend, session, agent = mailbox
    backend.ensure_label("to-complete")
    assert agent.chat("rename label to-do to to-complete").approval is None
    assert backend._label_names["Label_1"] == "to-do"


def test_generic_read_proposal_cancel_and_slash_escape(mailbox):
    backend, session, agent = mailbox
    assert agent.chat("find invoices").approval.phase == "interpret"
    assert session.assistant.last_results == []
    agent.chat("cancel")
    assert session.assistant.last_results == []
    assert agent.chat("/search invoice").approval is None
    assert session.assistant.last_results


def test_model_tool_proposes_not_executes(mailbox):
    import threading
    import time
    from mailkeep.agent import _build_tool
    backend, session, agent = mailbox
    outcomes = []
    command = _build_tool(session, outcomes, threading.Event(), time.monotonic() + 10)
    command("find invoices")
    assert session.approval.phase == "interpret"
    assert session.assistant.last_results == []


def test_gmail_uses_label_ids_and_patch_contract():
    calls = []

    class Resource:
        def users(self): return self
        def messages(self): return self
        def labels(self): return self
        def list(self, **params):
            calls.append(params)
            return SimpleNamespace(execute=lambda: {"messages": []})
        def patch(self, **params):
            calls.append(params)
            return SimpleNamespace(execute=lambda: {"id": "Label_1", "name": "to-complete"})

    client = GmailClient(Resource())
    assert client.search_label("Label_1", 20) == []
    assert calls[0] == {"userId": "me", "maxResults": 20, "labelIds": ["Label_1"]}
    assert client.rename_label("Label_1", "to-complete") == Label("Label_1", "to-complete", "user")
    assert calls[1] == {"userId": "me", "id": "Label_1", "body": {"name": "to-complete"}}