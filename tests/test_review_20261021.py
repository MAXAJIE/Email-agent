"""Regression tests for mailkeep-bug-review-20261021 (B1-B9)."""
import os
import stat
import sys

import pytest

from mailkeep import config, router
from mailkeep.gmail import GmailClient, GmailError


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Messages:
    def __init__(self, bad):
        self.bad = bad

    def get(self, userId, id, **kw):
        def run():
            if id in self.bad:
                raise RuntimeError("404 not found")
            return {"id": id, "threadId": id, "labelIds": [], "snippet": "",
                    "internalDate": "0", "payload": {"headers": []}}
        return _Req(run)


class _Users:
    def __init__(self, bad):
        self.m = _Messages(bad)

    def messages(self):
        return self.m


class _Service:
    def __init__(self, bad=()):
        self.u = _Users(set(bad))

    def users(self):
        return self.u


def _client(bad=()):
    c = GmailClient(_Service(bad))
    c._read_retry = lambda call: call()
    return c


def test_b1_one_failed_message_does_not_drop_batch():
    got = _client(bad={"b"}).fetch(["a", "b", "c"])
    assert [e.id for e in got] == ["a", "c"]


def test_b1_all_failed_still_raises():
    with pytest.raises(GmailError):
        _client(bad={"a"}).fetch(["a"])


def test_b3_bad_worker_setting_does_not_crash(monkeypatch):
    monkeypatch.setenv("MAILKEEP_GMAIL_WORKERS", "lots")
    assert len(_client().fetch(["a"])) == 1


def test_b2_crlf_header_becomes_gmail_error():
    c = _client()
    with pytest.raises(GmailError):
        c.create_draft(to="x@example.com", subject="hi", body="b",
                       in_reply_to="<id@x>\r\nBcc: evil@example.com")


def test_b7_router_ignores_braces_after_json():
    d = router.parse('{"action": "reply", "reply": "hello"} note: {not json}')
    assert d.action == "reply" and d.reply == "hello"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_b5_private_file_created_0600(tmp_path):
    old = os.umask(0o022)
    try:
        p = tmp_path / "secret.json"
        config.write_private(p, "{}")
    finally:
        os.umask(old)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


# --- message count understood from the user's words (preset 20) -------------
from mailkeep.query import extract_limit
from mailkeep.intents import parse_intent


@pytest.mark.parametrize("text,expected", [
    ("give me the most recent unread mail", 1),
    ("show 5 emails", 5),
    ("five unread messages", 5),
    ("top 10", 10),
    ("twenty-one emails", 21),
    ("a dozen emails", 12),
    ("last 3 from amazon", 3),
    ("give me three", 3),
    ("unread emails", 20),
    ("emails from last 5 days", 20),
])
def test_limit_detected_from_words(text, expected):
    assert extract_limit(text, 20) == expected


def test_count_words_are_not_search_terms():
    intent = parse_intent("show me five unread emails", default_limit=20)
    assert intent.limit == 5 and "five" not in intent.query
    assert parse_intent("give me the most recent unread mail").query == "is:unread"


def test_router_rewrite_keeps_users_count():
    from mailkeep.agent import _carry_count
    intent = parse_intent(_carry_count("give me the most recent unread mail", "find unread emails"))
    assert intent.limit == 1 and intent.query == "is:unread"
    assert _carry_count("unread emails please", "find unread emails") == "find unread emails"
    assert _carry_count("archive the latest mail", "archive 1") == "archive 1"



# --- drafts understand what the user already said ----------------------------
from mailkeep import frames


def test_draft_from_listed_email_needs_no_more_questions():
    fr = frames.start("draft_new", "use the first email content, draft a email that send to chinlingjie274@gmail.com")
    assert fr.slots["source"] == "1" and fr.ready(True)


@pytest.mark.parametrize("text,ref", [
    ("forward email 3 to a@b.com", "3"),
    ("summarise the latest mail and send it to a@b.com", "1"),
    ("draft to a@b.com based on #2", "2"),
    ("based on the second message, write to a@b.com", "2"),
])
def test_source_email_detected(text, ref):
    assert frames.start("draft_new", text).slots.get("source") == ref


def test_free_text_is_the_message():
    fr = frames.start("draft_new", "draft an email to a@b.com, I cannot come to the meeting tomorrow")
    assert fr.slots["purpose"] == "I cannot come to the meeting tomorrow" and fr.ready(True)


def test_still_asks_when_nothing_to_say():
    assert not frames.start("draft_new", "draft a email that send to a@b.com").ready(True)
