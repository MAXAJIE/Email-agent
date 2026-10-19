from datetime import date

from mailkeep.classify import CATEGORY_QUERIES
from mailkeep.query import build_find_query, extract_sender, time_clause


def test_invoice_query_keeps_sender_and_time_but_no_time_words():
    query, categories = build_find_query(
        "find invoices from amazon last month", today=date(2026, 10, 19)
    )
    assert categories == ["invoices"]
    assert CATEGORY_QUERIES["invoices"] in query
    assert "from:amazon" in query
    assert "after:2026/08/31" in query
    assert "before:2026/10/01" in query
    assert "last month" not in query


def test_find_in_trash_does_not_add_a_stray_trash_keyword():
    query, _ = build_find_query("find emails in trash", today=date(2026, 10, 19))
    assert query == "in:trash"


def test_unread_and_attachments_are_flags_not_keywords():
    query, _ = build_find_query(
        "find unread emails with attachments", today=date(2026, 10, 19)
    )
    assert "is:unread" in query
    assert "has:attachment" in query
    assert "emails" not in query


def test_sender_email_is_extracted_whole():
    assert extract_sender("show mail from alice@example.com") == "alice@example.com"
    assert extract_sender("find invoices from amazon last week") == "amazon"


def test_yesterday_is_a_two_day_window():
    clause = time_clause("show me yesterday", today=date(2026, 10, 19))
    assert "newer_than:2d" in clause
    assert "older_than:1d" in clause


def test_plain_keywords_survive():
    query, categories = build_find_query("find emails about the hackathon")
    assert categories == []
    assert "hackathon" in query
