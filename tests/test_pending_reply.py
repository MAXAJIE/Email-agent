"""Messages typed while an approval card is open get an action-based reply."""
from mailkeep import refine
from mailkeep.demo import DemoGmailClient
from mailkeep.session import Session


def _two_row_delete():
    session = Session(DemoGmailClient())
    session.run_command("find invoices")
    outcome = session.run_command("delete 1-2")
    assert outcome.approval is not None
    return session


def _ids(outcome):
    return [row[-1] for r in outcome.responses if r.table for row in r.table.rows]


def test_selection_phrases():
    assert refine.selection("just the first 1", 2) == [1]
    assert refine.selection("only the second one", 3) == [2]
    assert refine.selection("all except 2", 3) == [1, 3]
    assert refine.selection("first two", 3) == [1, 2]
    assert refine.selection("make it more formal", 3) is None


def test_just_the_first_narrows_trash_to_one_message():
    session = _two_row_delete()
    first_id = session.approval.previews[0].table.rows[0][-1]
    out = refine.pending_reply(session, "just the first 1")
    assert out.approval is not None
    assert "Move to Trash 1 message(s)" in out.responses[0].text
    assert _ids(out) == [first_id]
    done = session.resolve(out.approval.id, True)
    assert "1 message" in done.responses[0].text


def test_unrelated_text_names_the_real_action_not_draft():
    session = _two_row_delete()
    out = refine.pending_reply(session, "what is this?")
    text = out.responses[0].text
    assert "Move to Trash 2 message(s)" in text
    assert "draft" not in text.lower()
    assert out.approval is session.approval


def test_typed_yes_and_cancel():
    session = _two_row_delete()
    assert "Cancelled: Move to Trash 2" in refine.pending_reply(session, "cancel").responses[0].text
    assert session.approval is None
    session = _two_row_delete()
    refine.pending_reply(session, "yes")
    assert session.approval is None
