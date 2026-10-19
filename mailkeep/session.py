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
