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


_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_IDIOMS = {"a couple of": 2, "a couple": 2, "couple of": 2, "a dozen": 12,
           "dozen": 12, "a pair of": 2, "a single": 1, "single": 1}
_NUM_WORD = "|".join(sorted([*_UNITS, *_TENS, "hundred"], key=len, reverse=True))
_WORD_NUMBER_RE = re.compile(
    rf"\b(?:{_NUM_WORD})(?:(?:[\s-]+|\s+and\s+)(?:{_NUM_WORD}))*\b", re.I)
_IDIOM_RE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, _IDIOMS), key=len, reverse=True)) + r")\b", re.I)

_NOUN = r"(?:e-?mails?|mails?|messages?|msgs?|results?|threads?|ones?|items?|letters?)"
_ADJ = r"(?:[a-z][\w'-]*\s+){0,3}?"  # "5 most recent unread emails"
_TIME_UNIT = r"(?:minutes?|hours?|days?|weeks?|months?|years?|mins?|hrs?)"
_COUNT_RES = [
    # "5 emails", "five unread messages", "3 most recent ones"
    re.compile(rf"\b(\d{{1,3}})\s+{_ADJ}{_NOUN}\b", re.I),
    # "top 5", "latest 10", "last 3", "limit 1" (not "last 5 days")
    re.compile(rf"\b(?:top|latest|newest|recent|last|first|limit(?:\s+to)?|max(?:imum)?|up\s+to)\s+(\d{{1,3}})\b(?!\s*{_TIME_UNIT}\b)", re.I),
    # "show 5", "give me 3", "list 10"
    re.compile(rf"\b(?:show|give|get|list|fetch|find|display|pull|read|see|open)\s+(?:me\s+)?(?:the\s+)?(\d{{1,3}})\b(?!\s*{_TIME_UNIT}\b)", re.I),
]
# "the most recent unread mail", "my latest email", "the last message" -> 1
_SINGULAR_RE = re.compile(
    r"\b(?:the|my|a|an|one|that|this)\s+(?:(?:very|single|only|most|recent|latest|newest|"
    r"last|first|top|unread|new|starred|important|[a-z]+'s)\s+){0,4}"
    r"(?:e-?mail|mail|message|msg|thread|letter)\b(?!s)", re.I)


def _words_to_number(phrase: str) -> int | None:
    current = 0
    for word in re.split(r"[\s-]+", phrase.lower()):
        if not word or word == "and":
            continue
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        else:
            return None
    return current


def _normalise_numbers(text: str) -> str:
    """Rewrite number words ("twenty-one", "a dozen") as digits."""
    text = _IDIOM_RE.sub(lambda m: str(_IDIOMS[m.group(1).lower()]), text)

    def repl(match: re.Match) -> str:
        value = _words_to_number(match.group(0))
        return match.group(0) if value is None else str(value)

    return _WORD_NUMBER_RE.sub(repl, text)


def extract_limit(text: str, default: int = 20) -> int:
    """How many messages the user asked for; ``default`` when they did not say.

    Understands digits ("5 emails", "top 10"), number words ("five", "twenty
    one", "a dozen") and singular requests ("the most recent unread mail" -> 1).
    Time spans ("last 5 days") are not counts.
    """
    normalised = _normalise_numbers(text or "")
    for pattern in _COUNT_RES:
        match = pattern.search(normalised)
        if match:
            return max(1, min(200, int(match.group(1))))
    if _SINGULAR_RE.search(text or ""):
        return 1
    return default


# Words that only describe how many / which end of the list, never search terms.
_COUNT_WORDS = {"most", "recent", "latest", "newest", "top", "first", "last",
                "single", "only", "dozen", "couple", "few", "limit"}


def _residual_keywords(text: str, consumed: str) -> str:
    text = _TIME_PHRASE_RE.sub(" ", _normalise_numbers(text)).lower()
    for phrase in consumed.split():
        text = text.replace(phrase.lower(), " ")
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9@._'+-]{1,}", text)
    kept = [
        token
        for token in tokens
        if token.lower() not in _STOPWORDS
        and token.lower() not in _QUERY_ONLY_WORDS
        and not token.lower().isdigit()
        and token.lower() not in _COUNT_WORDS
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
