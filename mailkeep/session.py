"""One mailbox conversation with deferred, ID-checked approvals.

Shared by the web UI and the OpenMinion agent tool. Every mailbox write goes
through ``Assistant``; here its ``confirm`` callback raises ``ApprovalRequired``
so nothing is written until the user presses Approve. Approval replays the
same command from the pre-command snapshot and proceeds only when the message
IDs in the fresh preview equal the IDs the user reviewed.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field

from .assistant import ApprovalRequired, Assistant
from .models import Response


def _preview_ids(previews: list[Response]) -> list[list[str]]:
    return [
        [row[-1] for row in r.table.rows]
        for r in previews
        if r.table and r.table.columns and r.table.columns[-1] == "ID"
    ]


@dataclass
class Approval:
    id: str
    command: str
    question: str
    snapshot: dict
    previews: list[Response] = field(default_factory=list)
    phase: str = "write"
    target_label_id: str = ""

    def public(self) -> dict:
        return {"id": self.id, "question": self.question, "command": self.command}


@dataclass
class Outcome:
    responses: list[Response]
    approval: Approval | None = None


class Session:
    def __init__(self, backend, *, limit: int = 20):
        self.backend = backend
        self.assistant = Assistant(backend, limit=limit)
        self.approval: Approval | None = None
        self.lock = threading.RLock()

    def propose_command(self, text: str) -> Outcome:
        """Ground an interpreted command and ask before executing reads.

        Writes already have concrete preview/approval in Assistant. Explicit
        slash commands use run_command instead, preserving their exact grammar.
        """
        from .intents import parse_intent
        import re
        with self.lock:
            if self.approval is not None:
                return Outcome([Response("Review the pending action first.")], self.approval)
            intent = parse_intent(text, default_limit=self.assistant.limit)
            if intent.kind == "find" and re.match(r"^(?:how|why|can|could|would|should)\b", text, re.I):
                return Outcome([Response("I cannot confidently identify that mailbox action. "
                                         "What data should I show, or what should I change?")])
            if intent.kind in {"unknown", "noop"}:
                return Outcome([Response("Which mailbox action do you want? "
                                         "Tell me the target and the change, or the data to show.")])
            if intent.kind not in {"find", "find_raw", "browse_label", "open", "labels",
                                   "trash_report", "triage"}:
                return self.run_command(text)
            target_id = ""
            if intent.kind == "browse_label":
                label = self.assistant.existing_label(intent.label)
                target_id = label.id
                action = f"show up to {intent.limit} messages in label {label.name!r} ({label.id})"
            elif intent.kind in {"find", "find_raw"}:
                action = f"search Gmail for {intent.query or '(all mail)'!r}, up to {intent.limit} messages"
            elif intent.kind == "open":
                email = self.assistant._one(intent.refs)
                action = f"open message {intent.refs}: {email.subject!r} ({email.id})"
            elif intent.kind == "labels":
                action = "list your Gmail labels"
            elif intent.kind == "triage":
                action = "review your messages and list the ones that look like junk"
            else:
                action = f"analyze up to {intent.limit} messages in Trash"
            question = f"Did you mean to {action}?"
            self.approval = Approval(secrets.token_hex(8), text, question,
                                     self.assistant.snapshot(), phase="interpret",
                                     target_label_id=target_id)
            return Outcome([Response(question + " Approve to run, or Cancel.")], self.approval)

    def run_command(self, text: str) -> Outcome:
        """Run one Mailkeep command. Writes stop at a pending approval."""
        with self.lock:
            snapshot = self.assistant.snapshot()
            previews: list[Response] = []

            def defer(question: str) -> bool:
                raise ApprovalRequired(question)

            self.assistant.notify = previews.append
            self.assistant.confirm = defer
            try:
                response = self.assistant.handle(text)
            except ApprovalRequired as pending:
                self.approval = Approval(
                    id=secrets.token_hex(8),
                    command=text,
                    question=pending.question,
                    snapshot=snapshot,
                    previews=previews,
                )
                return Outcome(previews, self.approval)
            return Outcome(previews + [response])

    def propose_draft(self, draft: dict, body: str) -> Outcome:
        """Preview a fully composed draft and stop at an approval card.

        The body goes through the assistant's own "draft_body" follow-up
        stage, so approval replays exactly the reviewed text (no re-parsing
        of a command string that could split on words like "text" or "body").
        """
        body = body.strip().lstrip("/").strip()
        with self.lock:
            self.assistant.pending = {"stage": "draft_body", "draft": dict(draft)}
            return self.run_command(body)

    def discard_approval(self) -> bool:
        """Drop a pending approval (used when the user revises the draft)."""
        with self.lock:
            if self.approval is None:
                return False
            self.assistant.restore(self.approval.snapshot)
            self.assistant.pending = None
            self.approval = None
            return True

    def resolve(self, approval_id: str, approve: bool) -> Outcome:
        with self.lock:
            approval = self.approval
            if approval is None or approval.id != approval_id:
                return Outcome([Response("That approval is no longer pending. Nothing changed.")])
            self.approval = None
            self.assistant.restore(approval.snapshot)
            if not approve:
                self.assistant.pending = None
                return Outcome([Response("Cancelled. Nothing changed.")])

            if approval.phase == "interpret":
                if approval.target_label_id:
                    from .intents import parse_intent
                    intent = parse_intent(approval.command)
                    matches = [x for x in self.backend.labels()
                               if x.id == approval.target_label_id and x.name == intent.label]
                    if len(matches) != 1:
                        return Outcome([Response("The label changed since the proposal. "
                                                 "Ask again to review the current target.")])
                return self.run_command(approval.command)

            fresh: list[Response] = []
            expected = _preview_ids(approval.previews)

            def confirm(question: str) -> bool:
                # The mailbox may change between preview and approval (e.g.
                # "label all newsletters" re-searches). Refuse in that case.
                return question == approval.question and _preview_ids(fresh) == expected

            self.assistant.notify = fresh.append
            self.assistant.confirm = confirm
            response = self.assistant.handle(approval.command)
            if _preview_ids(fresh) != expected:
                response = Response(
                    "The matching messages changed since you reviewed them, so nothing was "
                    "changed. Run the command again to see the new preview."
                )
            return Outcome([response])
