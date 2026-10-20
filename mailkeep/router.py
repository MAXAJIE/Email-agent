"""The main model decides: answer in chat, or hand the job to an agent.

Before, regexes (``needs_agent``, ``action_intent``, ``profile_for``) guessed
whether a message needed the mailbox, and a guessed "fast" model answered the
rest. Now the main (default-role) model receives the schema of the agents that
exist below and returns one JSON decision. Mailkeep validates it and runs the
chosen agent; every agent runs on its own dedicated role model, or on the main
model when that role has no provider/key/model of its own (providers.chain).

When the main model cannot be reached the router returns ``None`` and the
caller uses the deterministic rule parser, saying so in the reply.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .assistant import HELP_TEXT
from .config import setting

AGENTS: dict[str, dict] = {
    "mailbox": {
        "role": "default",
        "description": "Reads or changes Gmail by running ONE Mailkeep command: find/search, "
                       "open N, labels, analyze trash, label/unlabel, archive, mark read, "
                       "delete (to Trash), restore. Writes stop at an approval card.",
        "args": {"command": "one exact Mailkeep command, e.g. 'find invoices from last month', "
                            "'archive 1,3', 'label 1-2 as Finance'"},
    },
    "drafter": {
        "role": "draft",
        "description": "Writes a NEW email draft or a REPLY draft and shows it for approval. "
                       "Never sends mail.",
        "args": {"to": "recipient email address for a new email, else ''",
                 "reply_to": "row number from the last list when replying, else ''",
                 "subject": "subject line or ''",
                 "instructions": "what the email should say"},
    },
    "triage": {
        "role": "classifier",
        "description": "Classifies / prioritises the current list of messages (or unread mail).",
        "args": {"request": "what to triage"},
    },
    "analyst": {
        "role": "reasoning",
        "description": "Plans, compares, explains or summarises mail in the current list; "
                       "suggests actions but never executes them.",
        "args": {"request": "the question to analyse"},
    },
}


def enabled() -> bool:
    """MAILKEEP_LLM_ROUTER: on by default; off/0/no/false uses the old heuristics."""
    return (setting("MAILKEEP_LLM_ROUTER") or "on").strip().lower() not in ("0", "off", "no", "false")


def schema() -> list[dict]:
    return [{"name": n, "runs_on_role": a["role"], "description": a["description"], "args": a["args"]}
            for n, a in AGENTS.items()]


def system_prompt() -> str:
    return (
        "You are Mailkeep's coordinator, a Gmail assistant on the user's own machine.\n"
        "Decide for each user message: answer it yourself, or dispatch exactly one agent.\n"
        "Dispatch an agent whenever the user wants an action or mailbox data as the output "
        "(searching, opening, labelling, archiving, deleting, restoring, drafting, triage, "
        "analysis). Answer yourself only for conversation or questions about Mailkeep.\n"
        "Never invent emails, senders, counts or results: you have not seen the mailbox; "
        "only agents can read it. Mailkeep never sends email.\n\n"
        f"Available agents (JSON schema):\n{json.dumps(schema(), indent=1)}\n\n"
        f"Mailkeep command grammar for the mailbox agent:\n{HELP_TEXT}\n"
        "Row numbers refer to the most recent list shown to the user.\n\n"
        "Reply with ONE JSON object and nothing else, either\n"
        '{"action":"reply","reply":"<at most three short sentences>"}\n'
        "or\n"
        '{"action":"agent","agent":"<name>","args":{...},"say":"<one short sentence>"}'
    )


@dataclass
class Decision:
    action: str  # "reply" | "agent"
    reply: str = ""
    agent: str = ""
    args: dict = field(default_factory=dict)
    say: str = ""


_JSON = re.compile(r"\{.*\}", re.S)


def parse(text: str) -> Decision:
    """Validate the model's decision. Raises ValueError on anything unusable."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    match = _JSON.search(text)
    if not match:
        raise ValueError("the model did not return a JSON decision")
    try:
        data = json.loads(match.group(0))
    except ValueError as exc:
        raise ValueError(f"the model returned invalid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ValueError("the decision is not a JSON object")
    action = str(data.get("action") or "").strip().lower()
    if action == "reply":
        reply = str(data.get("reply") or "").strip()
        if not reply:
            raise ValueError("empty reply")
        return Decision("reply", reply=reply[:1500])
    if action == "agent":
        name = str(data.get("agent") or "").strip().lower()
        if name not in AGENTS:
            raise ValueError(f"unknown agent {name!r}")
        raw = data.get("args") if isinstance(data.get("args"), dict) else {}
        args = {k: str(v).strip() for k, v in raw.items()
                if k in AGENTS[name]["args"] and v is not None and str(v).strip()}
        if name == "mailbox" and not args.get("command"):
            raise ValueError("mailbox agent needs a command")
        return Decision("agent", agent=name, args=args, say=str(data.get("say") or "").strip()[:300])
    raise ValueError(f"unknown action {action!r}")


def context_block(history: list[tuple[str, str]], rows: list) -> str:
    lines = []
    if rows:
        lines.append(f"Most recent list shown to the user ({len(rows)} rows):")
        for index, email in enumerate(rows[:15], start=1):
            lines.append(f"  {index}. {str(getattr(email, 'sender', ''))[:40]} | "
                         f"{str(getattr(email, 'subject', ''))[:70]}")
    else:
        lines.append("No list has been shown yet.")
    for user, bot in history[-3:]:
        lines.append(f"User said: {user}")
        if bot:
            lines.append(f"Mailkeep said: {bot}")
    return "\n".join(lines)
