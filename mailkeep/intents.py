"""Rule-based intent parsing for the console chat.

No model call is involved, so the assistant is usable before an LLM provider
is configured. Everything the parser cannot recognise is treated as a search,
which is the least surprising fallback in a mail console.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .query import build_find_query, extract_limit

_FIND_START = re.compile(
    r"^(?:please\s+)?(?:find|search|look\s+for|show(?:\s+me)?|list|get|give\s+me|"
    r"display|pull\s+up|what)\b",
    re.I,
)
_TRASH_VERBS = re.compile(
    r"\b(?:analy[sz]e|analysis|report|audit|review|scan|clean(?: ?up)?|inspect)\b",
    re.I,
)
_GREETING = re.compile(r"^(?:hi|hello|hey|yo|good (?:morning|afternoon|evening))\b", re.I)
_REF_CHARS = r"#?\d+(?:\s*[-,]\s*#?\d+)*"
_DRAFT_VERBS = r"(?:draft|write|compose|make|create|send)"


@dataclass(frozen=True)
class Intent:
    kind: str
    text: str = ""
    query: str = ""
    refs: str = ""
    label: str = ""
    new_label: str = ""
    body: str = ""
    to: str = ""
    subject: str = ""
    limit: int = 20
    all_matching: bool = False
    scope: str = ""  # human description of where a junk review looks


def _clean_refs(value: str) -> str:
    value = value.strip().strip("'\"").lower()
    value = re.sub(r"\b(?:emails?|messages?|the)\b", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .,;:")
    return value


def _clean_label(value: str) -> str:
    value = value.strip().strip("'\"")
    value = re.sub(r"\s+(?:label|folder)$", "", value, flags=re.I)
    return value.strip()


def _body_after(text: str) -> str:
    parts = re.split(
        r"\b(?:saying|with\s+(?:the\s+)?(?:text|body|message)|body|text)\b",
        text,
        maxsplit=1,
        flags=re.I,
    )
    return parts[1].strip() if len(parts) == 2 else ""


def _reply_intent(stripped: str, limit: int) -> Intent | None:
    if not re.search(r"\b(?:reply|respond)\b", stripped, re.I):
        return None
    match = re.search(
        rf"\b(?:to\s+)?(?:email\s+|message\s+)?(?:the\s+)?(?P<ref>{_REF_CHARS}|last|this)\b",
        stripped,
        re.I,
    )
    refs = _clean_refs(match.group("ref")) if match else ""
    return Intent(
        kind="draft_reply",
        text=stripped,
        refs=refs,
        body=_body_after(stripped),
        limit=limit,
    )


def _new_draft_intent(stripped: str, limit: int) -> Intent | None:
    if not re.search(rf"\b{_DRAFT_VERBS}\b", stripped, re.I):
        return None
    match = re.search(r"\bto\s+(?P<to>[\w.+-]+@[\w.-]+)", stripped, re.I)
    if not match:
        return None
    body = _body_after(stripped)
    about = re.search(
        r"\babout\s+(?P<subject>.+?)(?=\s+(?:saying|and\s+saying)\b|$)",
        stripped,
        re.I,
    )
    subject = about.group("subject").strip(" .,;:") if about else ""
    if not subject and body:
        subject = " ".join(body.split()[:8])
    return Intent(
        kind="draft_new",
        text=stripped,
        to=match.group("to"),
        subject=subject[:120],
        body=body,
        limit=limit,
    )


def _label_intent(stripped: str, limit: int) -> Intent | None:
    refs = ""
    label = ""

    match = re.match(
        r"^(?:please\s+)?(?:label|tag|apply\s+label)\s+(?P<rest>.+)$", stripped, re.I
    )
    if match:
        rest = match.group("rest")
        split = re.split(r"\s+(?:as|with|to|under)\s+", rest, maxsplit=1, flags=re.I)
        refs = split[0]
        label = split[1] if len(split) == 2 else ""

    if not label:
        match = re.match(
            r"^(?:add|apply)\s+(?:the\s+)?label\s+(?P<label>.+?)\s+to\s+(?P<refs>.+)$",
            stripped,
            re.I,
        )
        if match:
            label, refs = match.group("label"), match.group("refs")

    if not label:
        match = re.match(r"^move\s+(?P<refs>.+?)\s+to\s+(?P<label>.+)$", stripped, re.I)
        if match:
            refs, label = match.group("refs"), match.group("label")

    if not label and not refs:
        return None

    refs = _clean_refs(refs)
    all_matching = bool(re.match(r"^all\b", refs))
    query = ""
    if all_matching:
        scope = re.sub(r"^all\b", "", refs).strip()
        query, _ = build_find_query(scope or stripped, today=None)
        refs = "all"
    return Intent(
        kind="label",
        text=stripped,
        refs=refs,
        label=_clean_label(label),
        query=query,
        all_matching=all_matching,
        limit=max(limit, 50) if all_matching else limit,
    )


def _unlabel_intent(stripped: str, limit: int) -> Intent | None:
    match = re.match(
        r"^remove\s+(?:the\s+)?label\s+(?P<label>.+?)\s+from\s+(?P<refs>.+)$",
        stripped,
        re.I,
    )
    if match:
        return Intent(
            kind="unlabel",
            text=stripped,
            refs=_clean_refs(match.group("refs")),
            label=_clean_label(match.group("label")),
            limit=limit,
        )
    match = re.match(
        r"^(?:unlabel|untag)\s+(?P<refs>.+?)\s+"
        r"(?:from|as|with)\s+(?P<label>.+)$",
        stripped,
        re.I,
    )
    if match:
        return Intent(
            kind="unlabel",
            text=stripped,
            refs=_clean_refs(match.group("refs")),
            label=_clean_label(match.group("label")),
            limit=limit,
        )
    return None


# Gmail inbox tabs. "primary inbox", "social tab", "the updates category".
_TABS = {"primary": "Primary", "social": "Social", "promotions": "Promotions",
         "promotion": "Promotions", "updates": "Updates", "forums": "Forums"}
_TAB_RE = re.compile(
    r"\b(primary|social|promotions?|updates|forums)\b(?:\s+(?:inbox|tab|category|folder|mail|e-?mails?))?",
    re.I)
_JUNK_VERB = re.compile(
    r"\b(?:seek|seeking|look(?:ing)?\s+for|search(?:ing)?\s+for|hunt|find|finding|spot|detect|identify|"
    r"flag|pick\s+out|sort\s+out|weed\s+out|analy[sz]e|analy[sz]ing|review|scan|go\s+through|check)\b"
    r"[^.?!]{0,80}?\b(?:trash|junk|spam|rubbish|clutter)\b", re.I)


def junk_scope(text: str) -> tuple[str, str]:
    """(gmail_query, description) for where a junk review should look.

    "" means: the last list shown, else the newest inbox messages.
    """
    from .classify import category_query, detect_categories
    from .query import extract_sender, time_clause

    clauses, names = [], []
    lowered = text.lower()
    for match in _TAB_RE.finditer(text):
        tab = _TABS[match.group(1).lower()]
        clause = f"category:{tab.lower()}"
        if clause not in clauses:
            clauses.append(clause)
            names.append(f"{tab} tab")
    categories = [c for c in detect_categories(text) if c != "promotions" or not clauses]
    if categories:
        clauses.append(category_query(categories))
        names.extend(categories)
    if re.search(r"\bunread\b", lowered):
        clauses.append("is:unread"); names.append("unread")
    if re.search(r"\bstarred\b", lowered):
        clauses.append("is:starred"); names.append("starred")
    sender = extract_sender(text)
    if sender:
        clauses.append(f"from:{sender}"); names.append(f"from {sender}")
    when = time_clause(text)
    if when:
        clauses.append(when); names.append(when)
    if clauses and not re.search(r"\b(?:all\s+mail|everything|archived?)\b", lowered):
        clauses.insert(0, "in:inbox")
    elif re.search(r"\binbox\b", lowered):
        clauses.append("in:inbox"); names.append("inbox")
    return " ".join(clauses), ", ".join(names)


def triage_intent(text: str, limit: int) -> Intent:
    query, scope = junk_scope(text)
    return Intent(kind="triage", text=text, query=query, scope=scope, limit=limit)


def is_triage(text: str) -> bool:
    """Junk-likelihood triage phrasing, not a request about the Trash folder."""
    lowered = text.lower()
    if "trash" not in lowered and "junk" not in lowered and "spam" not in lowered:
        return False
    if re.search(r"\bin\s+the\s+trash\b|\btrash\s+folder\b|\bin\s+trash\b|\bmy\s+trash\b"
                 r"|\bthe\s+trash\b|\bempty\s+(?:my\s+|the\s+)?trash\b", lowered):
        return False
    # "analyze trash", "seek for trash mail in primary": find junk, not open Trash.
    if _JUNK_VERB.search(text):
        return True
    return bool(re.search(
        r"\b(?:most\s+|more\s+)?(?:likely|probably|possibly|plausibly|apt)\s+(?:to\s+be\s+)?"
        r"(?:like\s+)?(?:a\s+|an\s+)?(?:trash|junk|spam)\b"
        r"|\b(?:which|what)\s+(?:of\s+(?:my|the)\s+)?(?:e-?mails?|messages?|mail|ones?)\b"
        r"[^?!.]*\b(?:trash|junk|spam)\b"
        r"|\b(?:looks?|seems?|feels?)\s+(?:like\s+)?(?:trash|junk|spam)\b"
        r"|\b(?:should|could|can)\s+be\s+(?:moved\s+to\s+)?(?:trash(?:ed)?|junk|spam)\b"
        r"|\b(?:trash|junk|spam)\s+(?:candidates?|nominees?|picks?|material)\b"
        r"|\b(?:e-?mails?|messages?|mail)\s+(?:that|which)\s+(?:look|are)\s+like\s+(?:trash|junk)\b",
        text, re.I))


def _trash_intent(stripped: str, limit: int) -> Intent | None:
    lowered = stripped.lower()
    if "trash" not in lowered:
        return None
    if is_triage(stripped):
        return None
    if re.search(r"\b(?:emails?|messages?|search|find|list)\b", lowered):
        return None
    if _TRASH_VERBS.search(stripped) or re.match(
        r"^(?:what'?s|what\s+is|show|check)\b", lowered
    ):
        return Intent(kind="trash_report", text=stripped, limit=max(limit, 200))
    if re.search(r"\b(?:empty|purge|clear)\b", lowered):
        return Intent(kind="trash_report", text=stripped, limit=max(limit, 200))
    return None


def label_request(text: str, *, default_limit: int = 20, labels=()) -> Intent | None:
    """Recognize label management/browsing before generic search or chat.

    An implicit name ("what is included in to-do") is only a label when
    the actual mailbox contains that name. Unknown explicit names stay labels,
    so the assistant asks instead of searching for the words 'label NAME'.
    """
    text = text.strip().rstrip("?.").strip()
    rename = re.fullmatch(
        r"(?:please\s+)?(?:rename|change)\s+(?:the\s+)?(?:label\s+)?"
        r"(.+?)\s+to\s+(.+)", text, re.I)
    if rename and (re.search(r"\blabel\b", text, re.I) or
                   any(_clean_label(rename.group(1)).casefold() == x.name.casefold() for x in labels)):
        return Intent("rename_label", text=text, label=_clean_label(rename.group(1)),
                      new_label=_clean_label(rename.group(2)))
    canonical = re.fullmatch(r"(?:show|browse)\s+label\s+(.+)", text, re.I)
    match = canonical or re.fullmatch(
        r"(?:show\s+me\s+)?what(?:'s|\s+is)\s+(?:included\s+)?(?:in|under)\s+"
        r"(?:the\s+)?(.+)", text, re.I) or re.fullmatch(
        r"(?:show(?:\s+me)?|list|find|get)\s+(?:all\s+)?(?:the\s+)?"
        r"(?:emails?|messages?|contents|everything)\s+(?:in|under|with)\s+"
        r"(?:the\s+)?(.+)", text, re.I)
    if match:
        name = _clean_label(re.sub(r"^label\s+", "", match.group(1), flags=re.I))
        explicit = canonical or re.search(r"\blabel\b", match.group(1), re.I)
        if explicit or any(name.casefold() == x.name.casefold() for x in labels):
            return Intent("browse_label", text=text, label=name,
                          limit=extract_limit(text, default_limit))
    return None


def parse_intent(text: str, *, default_limit: int = 20) -> Intent:
    stripped = text.strip()
    if not stripped:
        return Intent(kind="noop", limit=default_limit)

    lowered = stripped.lower()
    limit = extract_limit(stripped, default_limit)

    # Slash commands are explicit: no guessing.
    if stripped.startswith("/"):
        command, _, rest = stripped[1:].partition(" ")
        command = command.lower()
        rest = rest.strip()
        if command in {"help", "h", "?"}:
            return Intent(kind="help", limit=limit)
        if command in {"quit", "exit", "q"}:
            return Intent(kind="quit", limit=limit)
        if command in {"status", "whoami"}:
            return Intent(kind="status", limit=limit)
        if command in {"labels", "label-list"}:
            return Intent(kind="labels", limit=limit)
        if command in {"junk", "triage", "spam"}:
            return triage_intent(rest, limit)
        if command in {"trash"}:
            return Intent(
                kind="trash_report", limit=max(limit, 200)
            ) if not rest else Intent(
                kind="trash_report", limit=extract_limit(rest, max(limit, 200))
            )
        if command in {"find", "search"}:
            return Intent(kind="find_raw", query=rest, limit=limit)
        if command in {"open", "read"}:
            return Intent(kind="open", refs=_clean_refs(rest), limit=limit)
        if command in {"rebuild", "refresh"}:
            return Intent(kind="status", limit=limit)
        if command in {"cancel"}:
            return Intent(kind="cancel", limit=limit)

    if lowered in {"quit", "exit", "bye", "q"}:
        return Intent(kind="quit", limit=limit)
    if lowered in {"help", "?", "commands"}:
        return Intent(kind="help", limit=limit)
    if lowered in {"cancel", "never mind", "nevermind"}:
        return Intent(kind="cancel", limit=limit)
    if _GREETING.match(stripped):
        return Intent(kind="greeting", limit=limit)
    if re.search(r"\b(?:who\s+am\s+i|what\s+account|account.*connected|status)\b", lowered):
        return Intent(kind="status", limit=limit)
    if re.fullmatch(r"(?:list|show|what)?\s*(?:my\s+)?labels\??", lowered):
        return Intent(kind="labels", limit=limit)

    managed = label_request(stripped, default_limit=default_limit)
    if managed:
        return managed

    if lowered in {"trash", "my trash", "the trash"}:
        return Intent(kind="trash_report", limit=max(limit, 200))

    if is_triage(stripped):
        return triage_intent(stripped, limit)

    trash = _trash_intent(stripped, limit)
    if trash:
        return trash

    reply = _reply_intent(stripped, limit)
    if reply:
        return reply

    draft = _new_draft_intent(stripped, limit)
    if draft:
        return draft

    unlabel = _unlabel_intent(stripped, limit)
    if unlabel:
        return unlabel

    label = _label_intent(stripped, limit)
    if label:
        return label

    restore = re.match(r"^(?:restore|recover|undelete)\s+(?P<refs>.+)$", stripped, re.I)
    if restore:
        return Intent(
            kind="restore", refs=_clean_refs(restore.group("refs")), limit=limit
        )

    archive = re.match(r"^archive\s+(?P<refs>.+)$", stripped, re.I)
    if archive:
        return Intent(
            kind="archive", refs=_clean_refs(archive.group("refs")), limit=limit
        )

    mark_read = re.match(
        r"^mark\s+(?P<refs>.+?)\s+(?:as\s+)?read$", stripped, re.I
    ) or re.match(r"^mark\s+as\s+read\s+(?P<refs>.+)$", stripped, re.I)
    if mark_read:
        return Intent(
            kind="mark_read",
            refs=_clean_refs(mark_read.group("refs")),
            limit=limit,
        )

    open_match = re.fullmatch(
        r"(?:open|read|view)\s+(?:the\s+)?(?:email|message)?\s*(?P<refs>#?\d+)",
        stripped,
        re.I,
    )
    if open_match:
        return Intent(
            kind="open", refs=_clean_refs(open_match.group("refs")), limit=limit
        )

    delete = re.match(r"^(?:delete|trash|bin)\s+(?P<refs>.+)$", stripped, re.I)
    if delete:
        return Intent(
            kind="delete", refs=_clean_refs(delete.group("refs")), limit=limit
        )

    if _FIND_START.match(stripped) or re.search(
        r"\b(?:emails?|messages?)\b", lowered
    ):
        query, _ = build_find_query(stripped, today=None)
        return Intent(kind="find", query=query, text=stripped, limit=limit)

    if len(lowered.split()) <= 6:
        query, _ = build_find_query(stripped, today=None)
        return Intent(kind="find", query=query, text=stripped, limit=limit)

    return Intent(kind="unknown", text=stripped, limit=limit)
