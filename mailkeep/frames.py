"""Task frames: recognise the job, collect enough facts, then act.

A frame is a small checklist for one kind of task ("draft a new email",
"reply to a message"). Each slot has a weight; once the filled weight reaches
MAILKEEP_FRAME_THRESHOLD (default 0.8 = 80 %) Mailkeep stops asking and does
the job. Below it, Mailkeep asks one specific question for the most
important missing slot instead of letting the model deliberate.

Everything here is deterministic (no model call), so recognising the task
and deciding what is missing costs milliseconds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import setting

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DRAFT_VERB = r"(?:draft|write|compose|prepare|create|make|send)"
_NEW_DRAFT = re.compile(
    rf"\b{_DRAFT_VERB}\b.{{0,40}}?\b(?:e-?mail|mail|message|note|letter|draft)\b|"
    r"^\s*(?:please\s+)?(?:draft|compose)\b|"
    r"\b(?:e-?mail|mail|write\s+to|message)\s+[\w.+-]+@",
    re.I,
)
_REPLY = re.compile(r"\b(?:reply|respond)\b", re.I)
_REF = re.compile(r"(?:#\s*(\d+)\b|\b(?:message|email|mail|number|no\.?)\s*#?\s*(\d+)\b|"
                  r"\b(?:to|on)\s+#?(\d+)\b|\b(this|last|it)\b)", re.I)
_TONES = ("formal", "casual", "friendly", "polite", "urgent", "professional", "warm")
_SUBJECT = re.compile(
    r"\b(?:about|regarding|re:|concerning|on the topic of|subject(?:\s+line)?\s*(?:is|:|=)?)\s+"
    r"(?P<s>.+?)(?=\s+(?:saying|telling|asking|that|and\s+(?:say|tell|ask)|to\s+(?:say|tell|ask|inform|remind|confirm|invite|thank|request|let))\b|[.;\n]|$)",
    re.I,
)
_PURPOSE = re.compile(
    r"\b(?:saying|telling(?:\s+(?:him|her|them))?|asking(?:\s+(?:him|her|them))?|that says|with the message|"
    r"to\s+(?:say|tell(?:\s+(?:him|her|them))?|ask(?:\s+(?:him|her|them))?|inform(?:\s+(?:him|her|them))?|"
    r"remind(?:\s+(?:him|her|them))?|confirm|invite(?:\s+(?:him|her|them))?|thank(?:\s+(?:him|her|them))?|"
    r"request|let\s+(?:him|her|them)\s+know))\s+(?P<p>.+)$",
    re.I | re.S,
)
_NAME_TO = re.compile(r"\bto\s+(?P<n>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b")
_REVISION = re.compile(
    r"\b(?:modify|change|revise|rewrite|re-?write|edit|update|tweak|adjust|redo|instead|"
    r"shorter|longer|more\s+\w+|less\s+\w+|tone|add|remove|mention|include|drop|"
    r"make\s+it|subject\s+(?:to|line)|sign\s+(?:it|off)|"
    r"better|worse|improve|polish|bad|not\s+good|don'?t\s+like|"
    r"one\s+more|again|different|version)\b",
    re.I,
)
# "use the first email content", "based on message 3", "forward #2",
# "summarise the latest mail": the body comes from a listed message.
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
             "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
             "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "ninth": 9, "9th": 9,
             "tenth": 10, "10th": 10, "top": 1, "latest": 1, "newest": 1,
             "most recent": 1}
_ORD = "|".join(sorted(map(re.escape, _ORDINALS), key=len, reverse=True))
_MSG = r"(?:e-?mail|mail|message|msg|one|row)"
_SOURCE_VERB = (r"(?:use|using|based\s+on|from|forward|fwd|summari[sz]e|summary\s+of|"
                r"quote|quoting|share|sharing|copy|include|with|content\s+of|info(?:rmation)?\s+(?:in|from))")
_SOURCE_REFS = [
    re.compile(rf"\b(?:the\s+)?({_ORD})\s+(?:\w+\s+)?{_MSG}\b", re.I),        # the first email
    re.compile(rf"\b{_MSG}\s*(?:#|no\.?|number)?\s*(\d{{1,3}})\b", re.I),   # email 3 / message #3
    re.compile(r"#\s*(\d{1,3})\b"),                                          # #3
    re.compile(rf"\b{_SOURCE_VERB}\s+(?:the\s+)?(?:row\s+)?(\d{{1,3}})\b", re.I),  # forward 3
]
_SOURCE_HINT = re.compile(rf"\b{_SOURCE_VERB}\b|\bcontent\b|\bit\s+says\b", re.I)
_FILLER = {"a", "an", "the", "that", "this", "to", "for", "me", "please", "and", "it", "of",
           "send", "sends", "sent", "draft", "write", "compose", "prepare", "create", "make",
           "email", "e-mail", "mail", "message", "note", "letter", "new", "can", "you",
           "could", "would", "i", "want", "need", "my", "him", "her", "them"}

_NEW_REQUEST = re.compile(r"\b(?:new|another|separate|second)\s+(?:draft|e-?mail|mail|message)\b", re.I)


def strict() -> bool:
    """New drafts collect recipient, subject, purpose and details (asked once) before writing.
    MAILKEEP_DRAFT_STRICT=off restores the older 'recipient + a sentence is enough' rule."""
    return (setting("MAILKEEP_DRAFT_STRICT") or "on").strip().lower() not in {"0", "off", "false", "no"}


def threshold() -> float:
    raw = setting("MAILKEEP_FRAME_THRESHOLD")
    try:
        value = float(raw) if raw else 0.8
    except ValueError:
        return 0.8
    return min(max(value, 0.1), 1.0)


WEIGHTS = {
    # A new email needs who / what about / why / the specifics. Details can be
    # skipped ("none"), but Mailkeep asks for them once instead of inventing them.
    "draft_new": {"to": 0.3, "subject": 0.25, "purpose": 0.25, "details": 0.2},
    "draft_reply": {"ref": 0.5, "purpose": 0.5},
}
QUESTIONS = {
    "to": "Who should I send it to? Please give the email address.",
    "purpose": ("What should the email say? A sentence or two about its purpose is enough "
                "(e.g. invite, follow up, request, apologise, thank, inform)."),
    "subject": "What should the subject line be?",
    "details": ("Any specific details to include? Dates, times, places, names, amounts, links, "
                "or what you want the reader to do. Say 'none' to skip."),
    "ref": "Which message should I reply to? Give its number from the last list (e.g. 2).",
}
LABELS = {"to": "recipient", "purpose": "purpose", "subject": "subject", "details": "details",
          "ref": "message to reply to"}
_DETAILS = re.compile(r"\b(?:details?|specifics?|including|include|mention|note\s+that|deadline)\b"
                      r"[:\s]*(?P<d>.+)$", re.I | re.S)
_SKIP = re.compile(r"^\s*(?:no(?:ne|pe)?|skip|nothing|n/?a|nah|that'?s\s+(?:all|it)|no\s+details?)\s*[.!]?\s*$", re.I)


def source_ref(text: str) -> str:
    """Row number of a listed email the user wants the draft built from, or ''."""
    if not _SOURCE_HINT.search(text):
        return ""
    for pattern in _SOURCE_REFS:
        match = pattern.search(text)
        if match:
            value = match.group(1).lower()
            return str(_ORDINALS.get(value, value))
    return ""


def free_purpose(text: str) -> str:
    """Whatever the user said beyond 'draft an email to X' (>= 3 real words)."""
    rest = EMAIL_RE.sub(" ", text)
    rest = re.sub(rf"^\s*(?:please\s+)?(?:can\s+you\s+)?{_DRAFT_VERB}\s+(?:me\s+)?(?:(?:a|an|the)\s+)?"
                  r"(?:new\s+)?(?:e-?mail|mail|message|note|letter|draft)?\s*(?:that\s+)?(?:send\s+)?(?:to|for)?\s*"
                  r"[,;:]?\s*", "", rest, flags=re.I)
    words = re.findall(r"[\w'’-]+", rest)
    content = [w for w in words if w.lower() not in _FILLER]
    return _clean(" ".join(words)) if len(content) >= 3 else ""


@dataclass
class Frame:
    kind: str  # "draft_new" | "draft_reply"
    slots: dict = field(default_factory=dict)
    asked: str = ""
    turns: int = 0

    def score(self, model_available: bool = True) -> float:
        if self.kind == "draft_new" and not strict():
            return self._legacy_score(model_available)
        weights = WEIGHTS[self.kind]
        total = sum(w for slot, w in weights.items() if self.slots.get(slot))
        # Content taken from a listed email covers the message and its subject.
        if self.kind == "draft_new" and self.slots.get("source"):
            for slot in ("purpose", "subject", "details"):
                if not self.slots.get(slot):
                    total += weights[slot]
        # A clear subject lets a model write a sensible body: half credit.
        if self.kind == "draft_new" and model_available and not self.slots.get("purpose") \
                and self.slots.get("subject"):
            total += weights["purpose"] / 2
        return round(total, 2)

    def _legacy_score(self, model_available: bool) -> float:
        """Pre-strict rule (to 0.4, purpose 0.4, subject 0.2) for MAILKEEP_DRAFT_STRICT=off."""
        weights = {"to": 0.4, "purpose": 0.4, "subject": 0.2}
        has = lambda s: self.slots.get(s) or (s != "to" and self.slots.get("source"))
        total = sum(w for slot, w in weights.items() if has(slot))
        if model_available and not has("purpose") and self.slots.get("subject"):
            total += 0.2
        return round(total, 2)

    def missing(self) -> list[str]:
        order = (["to", "ref", "subject", "purpose", "details"] if strict()
                 else ["to", "ref", "purpose", "subject"])
        filled = {"purpose", "subject", "details"} if self.slots.get("source") else set()
        return [s for s in order if s in WEIGHTS[self.kind] and not self.slots.get(s)
                and s not in filled]

    def ready(self, model_available: bool = True) -> bool:
        if self.kind == "draft_new" and not self.slots.get("to"):
            return False  # never guess a recipient
        if self.kind == "draft_reply" and not self.slots.get("ref"):
            return False
        if self.kind == "draft_new" and not strict():
            return self._legacy_score(model_available) >= threshold()
        if self.kind == "draft_new" and not self.slots.get("source") and not self.slots.get("material"):
            # Subject and purpose are always collected (asked for, never guessed);
            # details are asked once and may be skipped with "none".
            if not self.slots.get("subject") or not self.slots.get("purpose"):
                return False
            if not self.slots.get("details") and "details" not in self.slots.get("_asked", ()):
                return False
            return True
        return self.score(model_available) >= threshold()

    def question(self) -> str:
        missing = self.missing()
        slot = missing[0] if missing else "purpose"
        self.asked = slot
        self.slots.setdefault("_asked", [])
        if slot not in self.slots["_asked"]:
            self.slots["_asked"].append(slot)
        if slot == "to" and self.slots.get("to_name"):
            return f"What is {self.slots['to_name']}'s email address?"
        return QUESTIONS[slot]

    def checklist(self) -> str:
        have = lambda s: self.slots.get(s) or (s in {"purpose", "subject", "details"}
                                               and self.slots.get("source"))
        return ", ".join(f"{LABELS[s]} {'✓' if have(s) else '…'}" for s in WEIGHTS[self.kind])


def _clean(text: str) -> str:
    return text.strip().strip(" .,;:'\"").strip()


def is_draft_request(text: str) -> str:
    """'draft_reply', 'draft_new' or '' for a message."""
    stripped = text.strip()
    if not stripped or stripped.startswith("/"):
        return ""
    if _REPLY.search(stripped) and re.search(r"\b(?:draft|write|compose|reply)\b", stripped, re.I):
        return "draft_reply"
    if _NEW_DRAFT.search(stripped):
        return "draft_new"
    # "forward this mail to bob@x.com": Mailkeep cannot send, so a forward is a draft.
    if re.search(r"\b(?:forward|fwd)\b", stripped, re.I) and EMAIL_RE.search(stripped):
        return "draft_new"
    return ""


def extract(kind: str, text: str) -> dict:
    slots: dict = {}
    if kind == "draft_new":
        emails = EMAIL_RE.findall(text)
        if emails:
            slots["to"] = emails[0]
        else:
            name = _NAME_TO.search(text)
            if name:
                slots["to_name"] = name.group("n")
    else:
        for match in _REF.finditer(text):
            value = next((g for g in match.groups() if g), "")
            if value:
                slots["ref"] = value.lower()
                break
    subject = _SUBJECT.search(text)
    if subject:
        slots["subject"] = _clean(subject.group("s"))[:120]
    purpose = _PURPOSE.search(text)
    if purpose:
        slots["purpose"] = _clean(purpose.group("p"))
    if kind == "draft_new":
        details = _DETAILS.search(text)
        if details and len(details.group("d").split()) >= 2:
            slots["details"] = _clean(details.group(0))[:2000]
    tone = next((t for t in _TONES if re.search(rf"\b{t}\b", text, re.I)), "")
    if tone:
        slots["tone"] = tone
    if kind == "draft_new":
        source = source_ref(text)
        if source:
            slots["source"] = source
        if not slots.get("purpose") and not source:
            slots["purpose"] = free_purpose(_SUBJECT.sub(" ", text)) if slots.get("subject") \
                else free_purpose(text)
    if text.strip():
        slots["request"] = text.strip()[:2000]
    return {k: v for k, v in slots.items() if v}


def start(kind: str, text: str) -> Frame:
    return Frame(kind, extract(kind, text))


def merge_answer(frame: Frame, text: str) -> Frame:
    """Fold the user's follow-up into the frame."""
    asked = frame.asked
    explicit_purpose = bool(_PURPOSE.search(text))
    if asked == "details" and _SKIP.match(text):
        frame.slots["details"] = "(none)"
        frame.turns += 1
        return frame
    for key, value in extract(frame.kind, text).items():
        # An answer to "what is the subject?" is not also the purpose (and vice versa).
        if key == "purpose" and asked and asked != "purpose" and not explicit_purpose:
            continue
        if key in ("subject", "details") and asked == "purpose" and frame.slots.get(key):
            continue
        if key == asked and key in ("subject", "details"):
            continue  # the whole answer is stored below
        if key == "request" and frame.slots.get("request"):
            frame.slots["request"] = (frame.slots["request"] + "\n" + value)[:4000]
        else:
            frame.slots[key] = value
    answer = _clean(text)
    if asked in ("subject", "details") and answer:
        frame.slots[asked] = answer[:120] if asked == "subject" else answer[:2000]
    elif asked and not frame.slots.get(asked) and answer:
        if asked == "to":
            email = EMAIL_RE.search(text)
            if email:
                frame.slots["to"] = email.group(0)
        elif asked == "ref":
            digits = re.search(r"\d+", text)
            if digits:
                frame.slots["ref"] = digits.group(0)
        elif asked == "source":
            digits = re.search(r"\d+", text)
            if digits:
                frame.slots["source"] = digits.group(0)
        else:
            frame.slots[asked] = answer[:2000]
    frame.turns += 1
    return frame


def looks_unrelated(text: str) -> bool:
    """A follow-up that is clearly a different command abandons the frame."""
    stripped = text.strip().lower()
    return stripped.startswith("/") or bool(re.match(
        r"^(?:find|search|list|show|open|label|unlabel|archive|delete|restore|mark|analy[sz]e|labels|help|status)\b",
        stripped))


def is_revision(text: str) -> bool:
    return bool(_REVISION.search(text)) and not _NEW_REQUEST.search(text)


def subject_from(purpose: str) -> str:
    words = re.sub(r"\s+", " ", purpose).strip().split(" ")
    subject = " ".join(words[:7]).strip(" .,;:")
    return (subject[:1].upper() + subject[1:]) if subject else "(no subject)"


def template_body(slots: dict) -> str:
    """Plain body used when no model is reachable. Honest, short, editable."""
    name = slots.get("to_name") or ""
    tone = slots.get("tone", "")
    if tone in ("formal", "professional"):
        greeting = f"Dear {name or 'Sir or Madam'},"
    else:
        greeting = f"Hi{(' ' + name) if name else ''},"
    purpose = slots.get("purpose") or f"I am writing regarding {slots.get('subject', 'this')}."
    purpose = purpose[:1].upper() + purpose[1:]
    if not purpose.endswith((".", "!", "?")):
        purpose += "."
    closing = "Kind regards," if tone in ("formal", "professional", "polite") else "Best,"
    return f"{greeting}\n\n{purpose}\n\n{closing}"


def revise_offline(body: str, instruction: str) -> tuple[str, str]:
    """Rule-based revision when no model is reachable. Returns (body, change note)."""
    low = instruction.lower()
    lines = body.split("\n")
    if "formal" in low or "professional" in low:
        if lines and lines[0].lower().startswith("hi"):
            rest = lines[0][2:].strip(" ,")
            lines[0] = f"Dear {rest or 'Sir or Madam'},"
        return "\n".join(lines).replace("Best,", "Kind regards,"), "tone → formal"
    if "casual" in low or "friendly" in low:
        if lines and lines[0].lower().startswith("dear"):
            lines[0] = "Hi" + lines[0][4:]
        return "\n".join(lines).replace("Kind regards,", "Cheers,"), "tone → casual"
    if "shorter" in low or "brief" in low or "concise" in low:
        paragraphs = [p for p in body.split("\n\n") if p.strip()]
        if len(paragraphs) > 3:
            return "\n\n".join([paragraphs[0], paragraphs[1], paragraphs[-1]]), "shortened"
        return body, ""
    added = re.search(r"\b(?:add|mention|include)\s+(?:that\s+)?(.+)$", instruction, re.I)
    if added:
        sentence = _clean(added.group(1))
        sentence = sentence[:1].upper() + sentence[1:] + ("" if sentence.endswith((".", "!", "?")) else ".")
        paragraphs = body.split("\n\n")
        paragraphs.insert(max(len(paragraphs) - 1, 1), sentence)
        return "\n\n".join(paragraphs), f"added: {sentence[:60]}"
    return body, ""


_SUBJECT_EDIT = [
    re.compile(r"\b(?:subject|title)(?:\s+line)?\s*(?:to|as|=|:|into|with|should\s+be|is\s+now)\s*"
               r"(?P<v>.+?)(?=\.\s|[\n;]|\.?$)", re.I),
    re.compile(r"\b(?:new|different)\s+(?:subject|title)(?:\s+line)?\s*(?:is|:|=)?\s+(?P<v>.+?)(?=\.\s|[\n;]|\.?$)", re.I),
    re.compile(r"\b(?:rename|retitle)\s+(?:it|the\s+(?:email|mail|draft))\s+(?:to\s+)?(?P<v>.+?)(?=\.\s|[\n;]|\.?$)", re.I),
]
_FIELD_WORDS = {
    "subject": re.compile(r"\b(?:subject|title|heading)\b", re.I),
    "to": re.compile(r"\b(?:recipient|addressee|send\s+it\s+to\s+someone\s+else)\b", re.I),
}


def mentioned_fields(instruction: str) -> set[str]:
    """Header fields a revision talks about ('change the subject' -> {'subject'})."""
    return {name for name, rx in _FIELD_WORDS.items() if rx.search(instruction)}


def wants_body_change(instruction: str) -> bool:
    """False when the instruction only touches headers (subject / recipient)."""
    rest = instruction
    for rx in _SUBJECT_EDIT:
        rest = rx.sub(" ", rest)
    rest = EMAIL_RE.sub(" ", rest)
    rest = re.sub(r"\b(?:please|can|could|you|change|update|edit|modify|set|make|replace|the|a|an|it|"
                  r"subject|title|line|recipient|address|to|and|just|only|of|this|that|draft|mail|e-?mail|"
                  r"clearer|better|shorter|nicer|new|different)\b", " ", rest, flags=re.I)
    return bool(re.search(r"[A-Za-z]{3,}", rest))


def revision_fields(instruction: str) -> dict:
    """Explicit subject / recipient changes inside a revision request."""
    changes = {}
    for rx in _SUBJECT_EDIT:
        subject = rx.search(instruction)
        if subject:
            value = _clean(subject.group("v"))
            if value and value.lower() not in {"something", "something else", "it", "this", "that"}:
                changes["subject"] = value[:120]
            break
    if re.search(r"\b(?:recipient|send\s+it|address\s+it|instead|change\s+(?:the\s+)?to)\b", instruction, re.I):
        email = EMAIL_RE.search(instruction)
        if email:
            changes["to"] = email.group(0)
    return changes
