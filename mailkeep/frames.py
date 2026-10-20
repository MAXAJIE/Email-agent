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
    r"make\s+it|subject\s+(?:to|line)|sign\s+(?:it|off))\b",
    re.I,
)
_NEW_REQUEST = re.compile(r"\b(?:new|another|separate|second)\s+(?:draft|e-?mail|mail|message)\b", re.I)


def threshold() -> float:
    raw = setting("MAILKEEP_FRAME_THRESHOLD")
    try:
        value = float(raw) if raw else 0.8
    except ValueError:
        return 0.8
    return min(max(value, 0.1), 1.0)


WEIGHTS = {
    "draft_new": {"to": 0.4, "purpose": 0.4, "subject": 0.2},
    "draft_reply": {"ref": 0.5, "purpose": 0.5},
}
QUESTIONS = {
    "to": "Who should I send it to? Please give the email address.",
    "purpose": "What should the email say? A sentence or two about its purpose is enough.",
    "subject": "What is the subject?",
    "ref": "Which message should I reply to? Give its number from the last list (e.g. 2).",
}
LABELS = {"to": "recipient", "purpose": "message", "subject": "subject", "ref": "message to reply to"}


@dataclass
class Frame:
    kind: str  # "draft_new" | "draft_reply"
    slots: dict = field(default_factory=dict)
    asked: str = ""
    turns: int = 0

    def score(self, model_available: bool = True) -> float:
        weights = WEIGHTS[self.kind]
        total = sum(w for slot, w in weights.items() if self.slots.get(slot))
        # A clear subject lets a model write a sensible body: half credit.
        if self.kind == "draft_new" and model_available and not self.slots.get("purpose") \
                and self.slots.get("subject"):
            total += weights["purpose"] / 2
        return round(total, 2)

    def missing(self) -> list[str]:
        order = ["to", "ref", "purpose", "subject"]
        return [s for s in order if s in WEIGHTS[self.kind] and not self.slots.get(s)]

    def ready(self, model_available: bool = True) -> bool:
        if self.kind == "draft_new" and not self.slots.get("to"):
            return False  # never guess a recipient
        if self.kind == "draft_reply" and not self.slots.get("ref"):
            return False
        return self.score(model_available) >= threshold()

    def question(self) -> str:
        missing = self.missing()
        slot = missing[0] if missing else "purpose"
        self.asked = slot
        if slot == "to" and self.slots.get("to_name"):
            return f"What is {self.slots['to_name']}'s email address?"
        return QUESTIONS[slot]

    def checklist(self) -> str:
        return ", ".join(f"{LABELS[s]} {'✓' if self.slots.get(s) else '…'}" for s in WEIGHTS[self.kind])


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
    tone = next((t for t in _TONES if re.search(rf"\b{t}\b", text, re.I)), "")
    if tone:
        slots["tone"] = tone
    return {k: v for k, v in slots.items() if v}


def start(kind: str, text: str) -> Frame:
    return Frame(kind, extract(kind, text))


def merge_answer(frame: Frame, text: str) -> Frame:
    """Fold the user's follow-up into the frame."""
    for key, value in extract(frame.kind, text).items():
        frame.slots[key] = value
    asked = frame.asked
    answer = _clean(text)
    if asked and not frame.slots.get(asked) and answer:
        if asked == "to":
            email = EMAIL_RE.search(text)
            if email:
                frame.slots["to"] = email.group(0)
        elif asked == "ref":
            digits = re.search(r"\d+", text)
            if digits:
                frame.slots["ref"] = digits.group(0)
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


def revision_fields(instruction: str) -> dict:
    """Explicit subject / recipient changes inside a revision request."""
    changes = {}
    subject = re.search(r"\bsubject(?:\s+line)?\s+(?:to|as|=|:)\s*(.+?)(?=[.;\n]|$)", instruction, re.I)
    if subject:
        changes["subject"] = _clean(subject.group(1))[:120]
    if re.search(r"\b(?:recipient|send\s+it|address\s+it|instead|change\s+(?:the\s+)?to)\b", instruction, re.I):
        email = EMAIL_RE.search(instruction)
        if email:
            changes["to"] = email.group(0)
    return changes
