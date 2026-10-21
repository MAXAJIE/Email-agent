"""Junk review looks where the user says; suggestion chips are slash commands."""
from pathlib import Path

import pytest

from mailkeep.intents import is_triage, parse_intent


def test_screenshot_request_reviews_primary_not_trash_folder():
    intent = parse_intent("analyze the file in primary inbox and seek for trash mail")
    assert intent.kind == "triage"
    assert "category:primary" in intent.query and "in:trash" not in intent.query


@pytest.mark.parametrize("text,clause", [
    ("find junk in social", "category:social"),
    ("which newsletters look like junk", "newsletter"),
    ("scan unread mail for spam", "is:unread"),
    ("/junk updates", "category:updates"),
    ("find junk from amazon", "from:amazon"),
])
def test_named_scope_becomes_the_search(text, clause):
    intent = parse_intent(text)
    assert intent.kind == "triage" and clause in intent.query


def test_bare_analyze_trash_is_a_junk_review():
    assert parse_intent("analyze trash").kind == "triage"


def test_trash_folder_still_reachable():
    for text in ("/trash", "what's in my trash", "empty the trash", "find emails in trash"):
        assert not is_triage(text), text
    assert parse_intent("/trash").kind == "trash_report"


def test_junk_review_runs_on_named_scope():
    from mailkeep.assistant import Assistant
    from mailkeep.demo import DemoGmailClient

    backend = DemoGmailClient()
    seen = []
    real = backend.search
    backend.search = lambda q, limit=20: seen.append(q) or real(q, limit)
    Assistant(backend).handle("/junk promotions")
    assert seen and "category:promotions" in seen[0]


def test_suggestion_chips_are_slash_commands():
    html = (Path(__file__).parent.parent / "mailkeep" / "static" / "index.html").read_text(encoding="utf-8")
    line = next(l for l in html.splitlines() if "forEach((s)" in l and "/help" in l)
    chips = [c.strip(' "') for c in line.split("[", 1)[1].split("]", 1)[0].split(",")]
    assert chips and all(c.startswith("/") for c in chips)
