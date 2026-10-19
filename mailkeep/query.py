"""Turn a short natural-language request into a Gmail search query.

This is deliberately rule-based: the console assistant has to work before any
LLM provider is configured. ``/search <gmail query>`` bypasses the rules for
anything they do not cover.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .classify import category_query, detect_categories

_STOPWORDS = {
    "a",
    "about",
    "all",
    "an",
    "and",
    "any",
    "are",
    "at",
    "be",
    "by",
    "can",
    "do",
    "email",
    "emails",
    "find",
    "for",
    "from",
    "get",
    "give",
    "in",
    "inbox",
    "is",
    "list",
    "look",
    "mail",
    "mails",
    "me",
    "message",
    "messages",
    "my",
    "of",
    "on",
    "please",
    "search",
    "show",
    "some",
    "that",
    "the",
    "to",
    "with",
    "you",
}

_SENDER_TRAILING = _STOPWORDS | {
    "last",
    "this",
    "yesterday",
    "today",
    "week",
    "month",
    "year",
    "days",
    "day",
    "unread",
    "attachment",
    "attachments",
    "newer",
    "older",
}

_MONTH_RE = re.compile(r"\b(?:last|past)\s+(\d{1,3})\s+months?\b", re.I)
_DAYS_RE = re.compile(r"\b(?:last|past)\s+(\d{1,3})\s+days?\b", re.I)
_ISO_RE = re.compile(r"\b(?:since|after)\s+(\d{4})-(\d{1,2})-(\d{1,2})", re.I)
_BEFORE_RE = re.compile(r"\bbefore\s+(\d{4})-(\d{1,2})-(\d{1,2})", re.I)
_SENDER_RE = re.compile(
    r"\bfrom\s+(?P<who>[^\s,;]+@[^\s,;]+|[\w.&'+-]+(?:\s+[\w.&'+-]+){0,2})", re.I
)
_LIMIT_RE = re.compile(r"\b(\d{1,3})\s+(?:emails?|messages?|results?)\b", re.I)
_TIME_PHRASE_RE = re.compile(
    r"\b(?:(?:last|past|this)\s+(?:\d+\s+)?(?:days?|weeks?|months?|years?)"
    r"|today|yesterday|since\s+\d{4}-\d{1,2}-\d{1,2}"
    r"|(?:after|before)\s+\d{4}-\d{1,2}-\d{1,2})\b",
    re.I,
)
_QUERY_ONLY_WORDS = {"trash", "sent", "unread", "starred", "attachments", "attachment"}


def _after_clause(day: date) -> str:
    # Gmail's after: is exclusive and coarse; step back one day to include it.
    return f"after:{(day - timedelta(days=1)).strftime('%Y/%m/%d')}"


def time_clause(text: str, today: date | None = None) -> str:
    today = today or date.today()
    lowered = text.lower()

    if match := _ISO_RE.search(text):
        year, month, day = (int(part) for part in match.groups())
        return _after_clause(date(year, month, day))
    if match := _BEFORE_RE.search(text):
        year, month, day = (int(part) for part in match.groups())
        return f"before:{date(year, month, day).strftime('%Y/%m/%d')}"
    if match := _MONTH_RE.search(text):
        return f"newer_than:{int(match.group(1)) * 30}d"
    if match := _DAYS_RE.search(text):
        return f"newer_than:{int(match.group(1))}d"

    if "yesterday" in lowered:
        return "newer_than:2d older_than:1d"
    if "today" in lowered:
        return "newer_than:1d"
    if "this year" in lowered:
        return _after_clause(date(today.year, 1, 1))
    if "last year" in lowered:
        return (
            f"{_after_clause(date(today.year - 1, 1, 1))} "
            f"before:{date(today.year, 1, 1).strftime('%Y/%m/%d')}"
        )
    if "last month" in lowered:
        first_this = today.replace(day=1)
        end_previous = first_this
        start_previous = (first_this - timedelta(days=1)).replace(day=1)
        return (
            f"{_after_clause(start_previous)} "
            f"before:{end_previous.strftime('%Y/%m/%d')}"
        )
    if "this month" in lowered:
        return _after_clause(today.replace(day=1))
    if "last week" in lowered or "past week" in lowered or "this week" in lowered:
        return "newer_than:7d"
    return ""


def extract_sender(text: str) -> str:
    match = _SENDER_RE.search(text)
    if not match:
        return ""
    tokens = [token.strip(".,;:!?\"'") for token in match.group("who").split()]
    while tokens and tokens[-1].lower() in _SENDER_TRAILING:
        tokens.pop()
    if not tokens:
        return ""
    sender = " ".join(tokens)
    return sender if "@" in sender else sender.lower()


def extract_limit(text: str, default: int = 20) -> int:
    match = _LIMIT_RE.search(text)
    if not match:
        return default
    return max(1, min(200, int(match.group(1))))


def _residual_keywords(text: str, consumed: str) -> str:
    text = _TIME_PHRASE_RE.sub(" ", text).lower()
    for phrase in consumed.split():
        text = text.replace(phrase.lower(), " ")
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9@._'+-]{1,}", text)
    kept = [
        token
        for token in tokens
        if token.lower() not in _STOPWORDS
        and token.lower() not in _QUERY_ONLY_WORDS
        and not token.lower().isdigit()
    ]
    return " ".join(kept[:8])


def build_find_query(
    text: str,
    *,
    default_categories: list[str] | None = None,
    today: date | None = None,
) -> tuple[str, list[str]]:
    """Return ``(gmail_query, categories)`` for a free-text find request."""
    lowered = text.lower()
    categories = detect_categories(text) or list(default_categories or [])
    clauses: list[str] = []

    if categories:
        clauses.append(category_query(categories))

    sender = extract_sender(text)
    if sender:
        clauses.append(f'from:"{sender}"' if " " in sender else f"from:{sender}")

    window = time_clause(text, today=today)
    if window:
        clauses.append(window)

    if re.search(r"\bunread\b", lowered):
        clauses.append("is:unread")
    if re.search(r"\bstarred\b", lowered):
        clauses.append("is:starred")
    if re.search(r"\battachments?\b", lowered):
        clauses.append("has:attachment")
    if "in trash" in lowered or "from trash" in lowered:
        clauses.append("in:trash")
    elif re.search(r"\bsent\b", lowered):
        clauses.append("in:sent")

    consumed = " ".join(
        [
            *categories,
            *([sender] if sender else []),
            window,
            "unread" if "is:unread" in clauses else "",
            "starred" if "is:starred" in clauses else "",
            "attachments" if "has:attachment" in clauses else "",
        ]
    )
    residual = _residual_keywords(text, consumed)
    if residual:
        clauses.append(residual)

    return " ".join(part for part in clauses if part), categories
