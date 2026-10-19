import pytest

from mailkeep.assistant import Assistant, AssistantError
from mailkeep.demo import DemoGmailClient


def make_assistant(*, confirm: bool = True):
    backend = DemoGmailClient()
    previews = []
    assistant = Assistant(
        backend,
        confirm=lambda _question: confirm,
        notify=previews.append,
        limit=20,
    )
    return backend, assistant, previews


def test_find_invoices_lists_three_messages():
    _, assistant, _ = make_assistant()
    response = assistant.handle("find invoices")
    assert response.table is not None
    assert len(response.table.rows) == 3
    assert "3 message(s)" in response.text


def test_label_creates_label_and_verifies_preview():
    backend, assistant, previews = make_assistant()
    assistant.handle("find invoices")
    response = assistant.handle("label 1-2 as Finance")
    assert "Added to 2 message(s)" in response.text
    assert any("About to add label" in item.text for item in previews)
    assert "Finance" in [label.name for label in backend.labels()]
    assert "Finance" in assistant.last_results[0].labels
    assert "Finance" in assistant.last_results[1].labels


def test_label_can_be_cancelled_without_creating_it():
    backend, assistant, _ = make_assistant(confirm=False)
    assistant.handle("find invoices")
    response = assistant.handle("label 1-2 as Nope")
    assert "Cancelled" in response.text
    assert "Nope" not in [label.name for label in backend.labels()]
    assert "Nope" not in assistant.last_results[0].labels


def test_unlabel_removes_the_label():
    backend, assistant, _ = make_assistant()
    assistant.handle("find invoices")
    assistant.handle("label 1 as Finance")
    response = assistant.handle("unlabel 1 from Finance")
    assert "Removed label" in response.text
    assert "Finance" not in assistant.last_results[0].labels


def test_label_all_newsletters():
    _, assistant, _ = make_assistant()
    response = assistant.handle("label all newsletters as Mailkeep/Newsletters")
    assert "Added to 3 message(s)" in response.text


def test_label_without_name_asks_then_applies():
    _, assistant, _ = make_assistant()
    assistant.handle("find invoices")
    question = assistant.handle("label 1")
    assert "What label" in question.text
    response = assistant.handle("Finance")
    assert "Added to 1 message(s)" in response.text


def test_draft_reply_stays_in_thread_and_is_not_sent():
    backend, assistant, _ = make_assistant()
    assistant.handle("find invoices")
    response = assistant.handle("draft a reply to 1 saying Thanks for the update")
    assert "Draft created" in response.text
    assert "not sent" in response.text
    assert len(backend.drafts) == 1
    draft = backend.drafts[0]
    assert draft["to"] == "auto-confirm@amazon.com"
    assert draft["subject"].startswith("Re: ")
    assert draft["thread_id"] == "thread0001"
    assert draft["in_reply_to"] == "<demo0001@mailkeep.local>"
    assert draft["body"] == "Thanks for the update"
    assert not hasattr(backend, "send")


def test_draft_reply_asks_for_a_body_when_missing():
    backend, assistant, _ = make_assistant()
    assistant.handle("find invoices")
    question = assistant.handle("draft a reply to 1")
    assert "What should the reply say?" in question.text
    response = assistant.handle("Thanks a lot")
    assert "Draft created" in response.text
    assert backend.drafts[0]["body"] == "Thanks a lot"


def test_new_draft_to_explicit_recipient():
    backend, assistant, _ = make_assistant()
    response = assistant.handle(
        "draft to bob@example.com about Q4 numbers saying Here are the numbers"
    )
    assert "Draft created" in response.text
    assert backend.drafts[0]["to"] == "bob@example.com"
    assert backend.drafts[0]["subject"] == "Q4 numbers"


def test_trash_report_numbers_restore_candidates():
    _, assistant, _ = make_assistant()
    response = assistant.handle("analyze trash")
    assert response.table is not None
    assert len(response.table.rows) == 5
    assert "Restore candidates: 5" in response.text
    assert "Duplicate subject groups: 1" in response.text


def test_restore_moves_a_candidate_out_of_trash():
    _, assistant, _ = make_assistant()
    assistant.handle("analyze trash")
    target = assistant.last_results[0]
    response = assistant.handle("restore 1")
    assert "Restored: 1 message(s)" in response.text
    assert "TRASH" not in target.labels


def test_delete_moves_to_trash_after_confirmation():
    _, assistant, _ = make_assistant()
    assistant.handle("find invoices")
    response = assistant.handle("delete 3")
    assert "Moved to Trash: 1 message(s)" in response.text
    assert "TRASH" in assistant.last_results[2].labels


def test_mark_read():
    _, assistant, _ = make_assistant()
    assistant.handle("find unread emails")
    response = assistant.handle("mark 1 read")
    assert "Marked 1 message(s) as read" in response.text
    assert "UNREAD" not in assistant.last_results[0].labels


def test_open_shows_body_and_sets_last_opened():
    _, assistant, _ = make_assistant()
    assistant.handle("find invoices")
    response = assistant.handle("open 1")
    assert "From: Amazon" in response.text
    assert "This is a demo message body" in response.text
    assert assistant.last_opened is not None
    assert assistant.last_opened.id == "demo0001"


def test_out_of_range_reference_is_rejected():
    _, assistant, _ = make_assistant()
    assistant.handle("find invoices")
    with pytest.raises(AssistantError):
        assistant.handle("delete 9")


def test_help_and_status():
    _, assistant, _ = make_assistant()
    assert "analyze trash" in assistant.handle("help").text
    status = assistant.handle("status")
    assert "demo@mailkeep.app" in status.text
