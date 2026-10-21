from mailkeep.classify import CATEGORY_QUERIES
from mailkeep.intents import parse_intent


def test_label_range():
    intent = parse_intent("label 1-3 as Finance")
    assert intent.kind == "label"
    assert intent.refs == "1-3"
    assert intent.label == "Finance"
    assert intent.all_matching is False


def test_label_everything_matching_a_category():
    intent = parse_intent("label all newsletters as Mailkeep/Newsletters")
    assert intent.kind == "label"
    assert intent.all_matching is True
    assert intent.label == "Mailkeep/Newsletters"
    assert intent.query == CATEGORY_QUERIES["newsletters"]


def test_remove_label_form_is_not_read_backwards():
    intent = parse_intent("remove label Finance from 1-3")
    assert intent.kind == "unlabel"
    assert intent.refs == "1-3"
    assert intent.label == "Finance"

    intent = parse_intent("unlabel 2 from Finance")
    assert intent.kind == "unlabel"
    assert intent.refs == "2"
    assert intent.label == "Finance"


def test_reply_captures_reference_and_body():
    intent = parse_intent("draft a reply to 2 saying thanks for the update")
    assert intent.kind == "draft_reply"
    assert intent.refs == "2"
    assert intent.body == "thanks for the update"


def test_new_draft_captures_recipient_subject_and_body():
    intent = parse_intent(
        "draft to bob@example.com about Q4 numbers saying Here are the numbers"
    )
    assert intent.kind == "draft_new"
    assert intent.to == "bob@example.com"
    assert intent.subject == "Q4 numbers"
    assert intent.body == "Here are the numbers"


def test_trash_report_forms():
    for text in ("/trash", "what's in my trash", "trash report", "empty trash"):
        intent = parse_intent(text)
        assert intent.kind == "trash_report", text


def test_find_in_trash_is_still_a_search():
    intent = parse_intent("find emails in trash")
    assert intent.kind == "find"
    assert "in:trash" in intent.query


def test_actions_and_single_message_open():
    assert parse_intent("restore 1-2").kind == "restore"
    assert parse_intent("archive 3").kind == "archive"
    assert parse_intent("mark 1-3 read").kind == "mark_read"
    assert parse_intent("delete 4").kind == "delete"
    intent = parse_intent("open email 2")
    assert intent.kind == "open" and intent.refs == "2"


def test_control_commands():
    assert parse_intent("quit").kind == "quit"
    assert parse_intent("/help").kind == "help"
    assert parse_intent("/search from:github.com has:attachment").query == (
        "from:github.com has:attachment"
    )
    assert parse_intent("hello").kind == "greeting"


def test_unknown_long_sentence():
    assert parse_intent(
        "please think about whether this whole mailbox needs a completely new strategy"
    ).kind == "unknown"
