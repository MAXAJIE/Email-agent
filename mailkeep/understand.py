"""Understand what the user wants before acting.

Deterministic helpers (no model call, milliseconds):

* ``resolve_reference`` - which listed/open email does "it", "this mail",
  "the content", "the first one", "#2" point at? Returns a row spec the
  Assistant understands, or a clarifying question when it is ambiguous.
* ``content_task`` - the user wants something done WITH an email's content
  (translate, summarise, analyse, explain, copy, extract...). That is a
  mailbox task, never "off topic".
* ``plan_steps`` - split a multi-action request ("copy the mail, translate it
  and draft it to bob@x.com") into the ordered steps Mailkeep will perform, so
  the user can see that every part was understood.

The router model can also return a multi-step plan (see router.py); these
helpers are the offline / fast path and the safety net under it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .frames import EMAIL_RE, _ORDINALS, _SOURCE_REFS

# Doing something with the content of a mail.
_CONTENT_VERBS = re.compile(
    r"\b(?:translat\w*|summari[sz]\w*|summary|analy[sz]\w*|analysis|explain\w*|interpret\w*|"
    r"tl;?dr|key\s+points?|main\s+points?|gist|extract\w*|paraphras\w*|rephras\w*|"
    r"copy|copied|duplicate|quote|what\s+(?:does|did)\s+(?:it|this|that|the\s+\w+)\s+say|"
    r"what\s+is\s+(?:it|this|that)\s+about|break\s+(?:it|this)\s+down)\b",
    re.I,
)
# Words that point back at an email that is on screen.
_IMPLICIT = re.compile(
    r"\b(?:it|this|that|above|similar|the\s+(?:info|information|content|contents|"
    r"text|body|details?|mail|e-?mail|message|one|letter|newsletter|thread))\b",
    re.I,
)
# Plural references mean "the whole list", not one email.
_PLURAL = re.compile(r"\b(?:emails|mails|messages|all|every|inbox|list)\b", re.I)
# Material produced in an earlier turn ("draft the translation to bob@...").
_SCRATCH = re.compile(
    r"\b(?:translation|translated|summary|summari[sz]ed|analysis|result|output|above|"
    r"what\s+you\s+(?:wrote|said|made|translated|summari[sz]ed)|this|that|it)\b",
    re.I,
)
_STRONG_SOURCE = re.compile(
    r"\b(?:copy|copied|content|contents|forward|fwd|similar|based\s+on|quote|quoting|"
    r"translat\w*|summari[sz]\w*|this\s+(?:e-?mail|mail|message)|that\s+(?:e-?mail|mail|message)|"
    r"the\s+(?:e-?mail|mail|message)\s+above)\b", re.I)
_WEAK_SOURCE = re.compile(r"\b(?:the\s+info|the\s+information|the\s+details)\b", re.I)
_ACTION_WORDS = (r"(?:copy|translat|summari|analy|draft|write|compose|send|forward|reply|explain|"
                 r"extract|archive|label|delete|mark|find|open)")
_STEP_SPLIT = re.compile(rf"\s*(?:,\s*(?:and\s+)?(?:then\s+)?|;\s*|\s+and\s+then\s+|\s+then\s+|"
                         rf"\.\s+|\s+and\s+(?={_ACTION_WORDS}\w*\b))", re.I)


@dataclass(frozen=True)
class Reference:
    spec: str = ""        # row number / "last" understood by Assistant._one
    question: str = ""    # non-empty when we must ask which email
    how: str = ""         # human explanation ("the only message in the list")


def explicit_reference(text: str) -> str:
    """Row number the user named explicitly ("#2", "email 3", "the first mail")."""
    without_addresses = EMAIL_RE.sub(" ", text or "")
    for pattern in _SOURCE_REFS:
        match = pattern.search(without_addresses)
        if match:
            value = match.group(1).lower()
            return str(_ORDINALS.get(value, value))
    bare = re.search(r"\b(?:number|no\.?|row)\s*(\d{1,3})\b", without_addresses, re.I)
    return bare.group(1) if bare else ""


def resolve_reference(text: str, assistant) -> Reference:
    """Which email the message is about, from the list/open mail on screen."""
    rows = list(getattr(assistant, "last_results", None) or [])
    opened = getattr(assistant, "last_opened", None)
    explicit = explicit_reference(text)
    if explicit:
        if explicit.isdigit() and rows and not 1 <= int(explicit) <= len(rows):
            return Reference(question=f"The last list has {len(rows)} message(s); "
                                      f"which one do you mean (1-{len(rows)})?")
        return Reference(spec=explicit, how=f"message {explicit}")
    if not _IMPLICIT.search(text or "") and not _STRONG_SOURCE.search(text or ""):
        return Reference()
    if opened is not None:
        return Reference(spec="last", how="the message you opened")
    if len(rows) == 1:
        return Reference(spec="1", how="the only message in the list")
    if len(rows) > 1:
        return Reference(question=f"Which message do you mean? Give its number from the last list "
                                  f"(1-{len(rows)}).")
    return Reference()


def content_task(text: str) -> bool:
    return bool(_CONTENT_VERBS.search(text or ""))


def has_mail_context(assistant) -> bool:
    return bool(getattr(assistant, "last_results", None) or getattr(assistant, "last_opened", None))


def refers_to_mail(text: str, assistant) -> bool:
    """The message points at an email on screen (so it is a mailbox request)."""
    if not has_mail_context(assistant):
        return False
    return bool(explicit_reference(text) or _IMPLICIT.search(text) or content_task(text))


def wants_single_mail(text: str) -> bool:
    """A content task about one email (not "summarise my emails")."""
    if explicit_reference(text):
        return True
    return bool(_IMPLICIT.search(text)) and not _PLURAL.search(text)


def wants_source(text: str) -> bool:
    """A draft request that should be built from an email on screen."""
    return bool(_STRONG_SOURCE.search(text) or _WEAK_SOURCE.search(text))


def strong_source(text: str) -> bool:
    """Unambiguous 'build it from that email' wording (copy, forward, similar...)."""
    return bool(_STRONG_SOURCE.search(text))


def wants_scratch(text: str) -> bool:
    return bool(_SCRATCH.search(text))


def is_forward(text: str) -> bool:
    return bool(re.search(r"\b(?:forward|fwd|pass\s+(?:it|this)\s+on)\b", text or "", re.I))


def plan_steps(text: str) -> list[str]:
    """Ordered, human-readable steps of a (possibly multi-action) request."""
    parts = [p.strip(" .,;") for p in _STEP_SPLIT.split(text or "") if p and p.strip(" .,;")]
    steps: list[str] = []
    for part in parts:
        low = part.lower()
        if re.search(r"\b(?:draft|write|compose|send|forward|reply)\b", low):
            to = EMAIL_RE.search(part)
            steps.append("draft an email" + (f" to {to.group(0)}" if to else "")
                         + " (saved only after you approve)")
        elif re.search(r"\btranslat", low):
            lang = re.search(r"\b(?:to|into)\s+([A-Za-z]+)", part)
            steps.append(f"translate to {lang.group(1).capitalize()}" if lang else "translate")
        elif re.search(r"\bsummar", low):
            steps.append("summarise")
        elif re.search(r"\banaly", low):
            steps.append("analyse" + (" briefly" if "brief" in low else ""))
        elif re.search(r"\b(?:copy|copied|duplicate|quote)\b", low):
            steps.append("copy the content")
        elif re.search(r"\bexplain", low):
            steps.append("explain")
        elif re.search(r"\bextract", low):
            steps.append("extract the requested details")
        else:
            steps.append(part[:80])
    seen, ordered = set(), []
    for step in steps:
        if step not in seen:
            seen.add(step)
            ordered.append(step)
    return ordered


_MAILBOX_STEP = re.compile(r"\b(?:archive|label|unlabel|delete|trash|restore|mark|find|search|open)\b", re.I)


def needs_plan(text: str) -> bool:
    """Several actions where at least one is a mailbox command (archive, label...)."""
    parts = [p for p in _STEP_SPLIT.split(text or "") if p and p.strip(" .,;")]
    return len(parts) > 1 and any(_MAILBOX_STEP.search(p) for p in parts)


_LANGS = ("english", "chinese", "mandarin", "malay", "bahasa", "korean", "japanese", "french",
          "spanish", "german", "italian", "portuguese", "indonesian", "thai", "vietnamese",
          "arabic", "hindi", "tamil", "russian", "dutch")
_LANG_SUBJECT = re.compile(
    rf"^(?:in(?:to)?\s+)?(?:the\s+)?({'|'.join(_LANGS)})(?:\s+(?:too|as\s+well|also|language|version))*$",
    re.I)


def subject_language(value: str, instruction: str = "") -> str:
    """'english too' / 'in Korean' as a new subject means: translate the subject."""
    match = _LANG_SUBJECT.match((value or "").strip(" .,!'\""))
    if match:
        return match.group(1).capitalize()
    found = re.search(rf"\btranslate\s+(?:the\s+)?subject\b.*?\b(?:to|into)\s+({'|'.join(_LANGS)})\b",
                      instruction or "", re.I)
    return found.group(1).capitalize() if found else ""


_NAME = r"([A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){0,3})"
_NAME_PATTERNS = (
    re.compile(rf"\b(?i:my\s+name\s+is)\s+{_NAME}"),
    re.compile(rf"\b(?i:(?:sign|signed)\s+(?:it\s+|off\s+|the\s+email\s+)?(?:as|with))\s+{_NAME}"),
    re.compile(rf"\b(?i:call\s+me)\s+{_NAME}"),
)


def user_name(text: str) -> str:
    """'My name is Max Lagume' -> 'Max Lagume' (capitalised words only)."""
    for pattern in _NAME_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return match.group(1).strip()
    return ""


_SIGNOFF = re.compile(r"^\s*(?:best(?:\s+regards)?|kind\s+regards|regards|warm\s+regards|"
                      r"sincerely|yours\s+sincerely|thanks|thank\s+you|many\s+thanks|cheers|"
                      r"cordially|respectfully)\s*[,!.]?\s*$", re.I)


def ensure_signature(body: str, name: str) -> str:
    """Put the sender's name under the sign-off when it is not there yet."""
    if not name or not body or name.lower() in body[-200:].lower():
        return body
    lines = body.rstrip().split("\n")
    for index in range(len(lines) - 1, max(len(lines) - 4, -1), -1):
        if _SIGNOFF.match(lines[index]):
            if not lines[index].rstrip().endswith((",", "!", ".")):
                lines[index] = lines[index].rstrip() + ","
            return "\n".join(lines[:index + 1] + [name] + lines[index + 1:])
    return body.rstrip() + "\n\nBest regards,\n" + name


def plain_text(body: str) -> str:
    """Model markdown -> plain email text (Gmail shows ** and # literally)."""
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), body or "")
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    return text


def describe_plan(steps: list[str], source_how: str = "") -> str:
    if not steps:
        return ""
    head = f"Understood ({source_how}): " if source_how else "Understood: "
    return head + " → ".join(f"{i}. {s}" for i, s in enumerate(steps, start=1))


_PLACEHOLDER = re.compile(r"\[(?:your|my|sender'?s?|recipient'?s?)?\s*(?:full\s+)?name\]", re.I)


def strip_placeholders(body: str) -> str:
    """Remove template placeholders such as '[Your Name]' a model may emit."""
    lines = []
    for line in (body or "").split("\n"):
        if not _PLACEHOLDER.search(line):
            lines.append(line)
            continue
        cleaned = _PLACEHOLDER.sub("", line).rstrip()
        if cleaned.strip():
            lines.append(cleaned)
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- memory
CONTEXT_TOKENS_DEFAULT = 500_000


def context_budget() -> int:
    """Token budget for remembered conversation (MAILKEEP_CONTEXT_TOKENS)."""
    import os
    try:
        value = int(os.environ.get("MAILKEEP_CONTEXT_TOKENS", "") or CONTEXT_TOKENS_DEFAULT)
    except ValueError:
        value = CONTEXT_TOKENS_DEFAULT
    return max(1_000, value)


def estimate_tokens(text: str) -> int:
    """Cheap token estimate: ~4 ASCII chars per token, 1 token per CJK/other char."""
    text = text or ""
    wide = sum(1 for ch in text if ord(ch) > 127)
    return (len(text) - wide + 3) // 4 + wide


def trim_history(history: list, budget: int | None = None) -> list:
    """Drop the oldest turns until the history fits the token budget."""
    budget = context_budget() if budget is None else budget
    kept, total = [], 0
    for user, bot in reversed(history):
        cost = estimate_tokens(user) + estimate_tokens(bot) + 8
        if kept and total + cost > budget:
            break
        kept.append((user, bot))
        total += cost
    return list(reversed(kept))


# ------------------------------------------------------ list selection
_LIST_TRASH = re.compile(r"\b(trash|bin|delete|remove (?:them|the rest|others?) )", re.I)
_LIST_ARCHIVE = re.compile(r"\barchive\b", re.I)
_LIST_EXCLUDE = re.compile(
    r"\b(others?|the rest|rest of|remaining|except|but not|apart from|other than|keep|"
    r"(?:remove|take|drop|exclude)\b.{0,60}\b(?:from|off|out of)\s+(?:here|this|the list|it)|"
    r"exclude|not\s+(?:the\s+)?\w+|useful|important)\b", re.I)
_STOP = {"the", "team", "inc", "mail", "email", "from", "noreply", "no-reply", "support", "info"}


def _compact(numbers: list[int]) -> str:
    parts, start = [], None
    for i, n in enumerate(numbers):
        if start is None:
            start = n
        if i + 1 == len(numbers) or numbers[i + 1] != n + 1:
            parts.append(str(start) if start == n else f"{start}-{n}")
            start = None
    return ",".join(parts)


def list_selection(text: str, rows: list) -> tuple[str, list[int]] | None:
    """'remove github and google from here, put the others into trash' on a
    shown list -> ('delete 1-8', [9, 10]). Returns (command, kept rows) or None."""
    if not rows or not text or EMAIL_RE.search(text):
        return None
    verb = "delete" if _LIST_TRASH.search(text) else "archive" if _LIST_ARCHIVE.search(text) else ""
    if not verb or not _LIST_EXCLUDE.search(text):
        return None
    low = text.lower()
    words = set(re.findall(r"[\w\u00c0-\uffff]+", low))
    kept: set[int] = set()
    for index, email in enumerate(rows, start=1):
        sender = str(getattr(email, "sender", "") or "").lower()
        names = {w for w in re.findall(r"[\w\u00c0-\uffff]+", sender) if len(w) >= 3 and w not in _STOP}
        if names & words:
            kept.add(index)
    for match in re.finditer(r"\b(?:except|keep|not|but|apart from|other than)\s+((?:#?\d+(?:\s*(?:-|to|,|and)\s*)?)+)", low):
        for a, b in re.findall(r"(\d+)(?:\s*(?:-|to)\s*(\d+))?", match.group(1)):
            for n in range(int(a), int(b or a) + 1):
                if 1 <= n <= len(rows):
                    kept.add(n)
    if not kept:
        return None
    chosen = [n for n in range(1, len(rows) + 1) if n not in kept]
    if not chosen:
        return None
    return f"{verb} {_compact(chosen)}", sorted(kept)


_KEEP_MORE = re.compile(
    r"\b(not (?:a )?(?:trash|junk|spam)|isn'?t (?:trash|junk)|is useful|are useful|important|"
    r"keep|don'?t (?:trash|delete|archive|remove)|do not (?:trash|delete|archive|remove)|"
    r"leave|save|exclude|skip|spare|need (?:it|them|this)|也保留|保留|不是垃圾|别删|不要删)\b", re.I)
_DROP_MORE = re.compile(r"\b(also|too|as well|and)\b.*\b(trash|delete|archive|remove|bin)\b|"
                        r"\b(trash|delete|archive|remove|bin)\b.*\b(too|as well|also)\b", re.I)


def _named_rows(text: str, rows: list) -> set[int]:
    low = text.lower()
    words = set(re.findall(r"[\w\u00c0-\uffff]+", low))
    hits: set[int] = set()
    for index, email in enumerate(rows, start=1):
        sender = str(getattr(email, "sender", "") or "").lower()
        names = {w for w in re.findall(r"[\w\u00c0-\uffff]+", sender) if len(w) >= 3 and w not in _STOP}
        if names & words:
            hits.add(index)
    for a, b in re.findall(r"#?\b(\d{1,3})\b(?:\s*(?:-|to)\s*(\d{1,3}))?", low):
        for n in range(int(a), int(b or a) + 1):
            if 1 <= n <= len(rows):
                hits.add(n)
    return hits


def refine_selection(text: str, rows: list, kept: list[int], verb: str) -> tuple[str, list[int]] | None:
    """Follow-up on a pending 'trash the rest' card: 'shopee is not trash' adds
    Shopee to the kept rows (earlier kept rows stay kept); 'trash github too'
    removes it. Returns (new command, kept rows) or None."""
    if not rows or not text or EMAIL_RE.search(text):
        return None
    named = _named_rows(text, rows)
    if not named:
        return None
    keep = set(kept)
    if _KEEP_MORE.search(text):
        keep |= named
    elif _DROP_MORE.search(text):
        keep -= named
    else:
        return None
    if keep == set(kept):
        return None
    chosen = [n for n in range(1, len(rows) + 1) if n not in keep]
    if not chosen:
        return None
    return f"{verb} {_compact(chosen)}", sorted(keep)
