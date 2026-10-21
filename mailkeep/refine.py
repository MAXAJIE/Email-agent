"""Answer chat messages that arrive while an approval card is pending.

Before, every such message got the same canned draft-revision text, even when
the pending card was "Move these messages to Trash?" and the user was
narrowing it ("just the first 1"). Here the reply is built from the pending
action itself: typed approve/cancel resolve it, a narrower selection rebuilds
the preview for only those rows, and anything else names the real action.
"""

from __future__ import annotations

import re

from .intents import parse_intent
from .session import _preview_ids

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
             "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_APPROVE = re.compile(r"^\s*(?:yes|yep|yeah|ok(?:ay)?|sure|approved?|confirm(?:ed)?|go(?: ahead)?|"
                      r"do it|proceed)\b[\s!.]*(?:please)?[\s!.]*$", re.I)
_CANCEL = re.compile(r"^\s*(?:no|nope|cancel|stop|abort|never ?mind|don'?t|do not)\b", re.I)
_CUE = re.compile(r"\b(?:only|just|first|top|last|except|but|not|instead|keep|exclude|skip|"
                  + "|".join(_ORDINALS) + r")\b|^\s*#?\d", re.I)
_NEGATE = re.compile(r"\b(?:except|but not|not|exclude|skip|keep)\b", re.I)

# Writes whose target is a row list the user can narrow.
_VERBS = {"delete": "Move to Trash", "archive": "Archive", "mark_read": "Mark as read",
          "restore": "Restore from Trash", "label": "Label"}


def is_draft(approval) -> bool:
    pending = (getattr(approval, "snapshot", None) or {}).get("pending") or {}
    return pending.get("stage") == "draft_body"


def action_phrase(approval) -> str:
    """'Move to Trash 2 message(s)' style description of the pending card."""
    if is_draft(approval):
        return "create the Gmail draft shown above"
    intent = parse_intent(getattr(approval, "command", "") or "")
    rows = sum(len(ids) for ids in _preview_ids(getattr(approval, "previews", None) or []))
    verb = _VERBS.get(intent.kind)
    if verb == "Label":
        verb = f"Label as '{intent.label}'"
    if verb and rows:
        return f"{verb} {rows} message(s)"
    return (getattr(approval, "question", "") or getattr(approval, "command", "") or "the pending action").rstrip("?")


def wants_approve(text: str) -> bool:
    return bool(_APPROVE.match(text))


def wants_cancel(text: str) -> bool:
    return bool(_CANCEL.match(text))


def _numbers(text: str) -> list[int]:
    for word, value in _WORDS.items():
        text = re.sub(rf"\b{word}\b", str(value), text)
    return [int(n) for n in re.findall(r"\d+", text)]


def selection(text: str, count: int) -> list[int] | None:
    """Preview row numbers (1-based) the user now wants, or None if not a selection."""
    if count <= 0 or not _CUE.search(text):
        return None
    ords = "|".join(_ORDINALS)
    # "the second one" -> "the second": "one" is a filler word there, not row 1.
    lowered = re.sub(rf"\b({ords})\s+ones?\b", r"\1", text.lower())
    rows = list(range(1, count + 1))
    picked: list[int] = []
    top = re.search(r"\b(?:first|top)\s+(\d+|" + "|".join(_WORDS) + r")\b", lowered)
    if top:
        picked = rows[:_numbers(top.group(1))[0]]
    elif re.search(r"\blast\s+(?:one|1)\b|\bthe\s+last\b", lowered):
        picked = rows[-1:]
    else:
        picked = [v for w, v in _ORDINALS.items() if re.search(rf"\b{w}\b", lowered)]
        picked += _numbers(re.sub(r"\b(?:" + "|".join(_ORDINALS) + r")\b", " ", lowered))
    if picked and _NEGATE.search(lowered):
        picked = [r for r in rows if r not in picked]
    picked = sorted({p for p in picked if 1 <= p <= count})
    return picked or None


def narrowed_command(approval, text: str) -> tuple[str, list[int]] | None:
    """Rebuild the pending write for the preview rows the user named.

    Preview rows are mapped through message IDs to the list the user last saw,
    so the new command targets exactly those messages. None = not a narrowing.
    """
    if is_draft(approval):
        return None
    intent = parse_intent(getattr(approval, "command", "") or "")
    if intent.kind not in _VERBS:
        return None
    previews = _preview_ids(getattr(approval, "previews", None) or [])
    ids = previews[0] if previews else []
    picked = selection(text, len(ids))
    if not picked:
        return None
    listed = [getattr(e, "id", None) for e in (getattr(approval, "snapshot", None) or {}).get("last_results", [])]
    try:
        refs = [listed.index(ids[p - 1]) + 1 for p in picked]
    except ValueError:
        return None  # e.g. an "all matching" preview whose rows were never listed
    spec = ",".join(str(r) for r in refs)
    command = {
        "delete": f"delete {spec}",
        "archive": f"archive {spec}",
        "mark_read": f"mark {spec} as read",
        "restore": f"restore {spec}",
        "label": f"label {spec} as {intent.label}",
    }[intent.kind]
    return command, picked


def pending_reply(session, text: str, step=lambda _m: None, log=lambda _k, _m: None):
    """Outcome for a chat message sent while ``session.approval`` is open."""
    from .models import Response
    from .session import Outcome

    approval = session.approval
    action = action_phrase(approval)
    if wants_approve(text):
        step(f"Approving: {action}")
        log("approval", f"typed approve -> {action}")
        return session.resolve(approval.id, True)
    narrowed = narrowed_command(approval, text)
    if narrowed is not None:
        command, rows = narrowed
        step(f"Updating the plan to row(s) {', '.join(map(str, rows))}")
        session.discard_approval()
        outcome = session.run_command(command)
        log("approval", f"narrowed '{approval.command}' -> '{command}'")
        if outcome.approval is None:
            return outcome
        head = Response(f"Updated the plan: {action_phrase(outcome.approval)} (was: {action}). "
                        "Review the new card and press Approve, or Cancel to keep everything unchanged.")
        return Outcome([head] + outcome.responses, outcome.approval)
    if wants_cancel(text):
        log("approval", f"typed cancel -> {action}")
        session.resolve(approval.id, False)
        return Outcome([Response(f"Cancelled: {action}. Nothing changed in Gmail.")])
    if is_draft(approval):
        hint = "To change the draft, tell me what to change (e.g. 'make it more formal')."
    elif getattr(approval, "phase", "write") == "interpret":
        hint = "If that is not your intended action, tell me the action and target instead."
    elif parse_intent(getattr(approval, "command", "")).kind == "rename_label":
        hint = "Cancel and tell me the existing label and its intended new name to revise this rename."
    else:
        hint = "To change which messages are affected, name the rows (e.g. 'only 1' or 'all except 2')."
    return Outcome([Response(f"You have a pending approval: {action}. Press Approve or Cancel "
                             f"(or type yes / cancel). {hint}")], approval)
