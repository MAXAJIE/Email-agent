"""The console assistant: intent -> Gmail action -> a response to render."""

from __future__ import annotations

import re

from .classify import classify_email
from .intents import Intent, parse_intent
from .models import DraftResult, Email, Response, Table
from .trash import analyze_trash, human_bytes

HELP_TEXT = """Mailkeep commands (plain English or slash commands)

  find invoices from last month      search mail (also: find newsletters, jobs,
                                     travel, receipts, promotions, attachments,
                                     unread, starred, "in trash")
  /search <gmail query>              exact Gmail query
  analyze trash                      trash report + restore candidates
  label 1-3 as Finance               add a label (creates it if needed)
  unlabel 1-3 from Finance           remove a label
  draft a reply to 2 saying thanks   create a draft in the same thread
  draft to bob@example.com about Q4 saying ...   new draft
  restore 1-2 / archive 3 / mark 1 read / delete 4
  open 2                             read one message
  labels / status / help / quit

Numbered references point at the last list you saw (find results or the trash
report's restore candidates). Writes are previewed and need a y/N confirmation.
Mailkeep can create drafts but cannot send mail: the send tool is not present.
"""


class AssistantError(RuntimeError):
    """A user-facing problem, not a crash."""


def _clip(value: str, width: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= width else value[: width - 1] + "..."


def _table(emails: list[Email], *, start: int = 1) -> Table:
    rows = []
    for index, email in enumerate(emails, start=start):
        rows.append(
            [
                str(index),
                email.date.strftime("%Y-%m-%d") if email.date else "?",
                _clip(email.sender, 26),
                _clip(email.subject, 52),
                classify_email(email),
                email.id,
            ]
        )
    return Table(["#", "Date", "From", "Subject", "Type", "ID"], rows)


def _resolve_refs(spec: str, count: int) -> list[int]:
    spec = (spec or "").strip().lower()
    if spec in {"all", "*"}:
        return list(range(1, count + 1))
    if spec in {"last", "this", "it"}:
        return [count] if count else []
    spec = re.sub(r"\s*-\s*", "-", spec)
    spec = re.sub(r"\bto\b", "-", spec)
    numbers: list[int] = []
    for token in re.split(r"[\s,]+", spec):
        token = token.lstrip("#")
        if not token:
            continue
        if re.fullmatch(r"\d+-\d+", token):
            low, high = (int(part) for part in token.split("-"))
            if low > high:
                low, high = high, low
            numbers.extend(range(low, high + 1))
        elif token.isdigit():
            numbers.append(int(token))
    invalid = [number for number in numbers if not 1 <= number <= count]
    if invalid:
        raise AssistantError(
            f"These numbers are outside 1-{count}: "
            + ", ".join(str(number) for number in invalid)
        )
    ordered = sorted({number for number in numbers if 1 <= number <= count})
    if not ordered:
        raise AssistantError(
            f"No usable message numbers in {spec!r}. Use 1-{count} or 'all'."
        )
    return ordered


class Assistant:
    def __init__(
        self,
        backend,
        *,
        confirm=None,
        notify=None,
        limit: int = 20,
    ):
        self.backend = backend
        self.limit = limit
        self.confirm = confirm or (lambda _question: False)
        self.notify = notify or (lambda _response: None)
        self.last_results: list[Email] = []
        self.last_query = ""
        self.last_opened: Email | None = None
        self.pending: dict | None = None

    # -- entry point ---------------------------------------------------
    def handle(self, text: str) -> Response:
        intent = parse_intent(text, default_limit=self.limit)

        if self.pending and intent.kind not in {"cancel", "quit"}:
            if not text.strip().startswith("/"):
                return self._continue_pending(text)
            self.pending = None

        if intent.kind == "cancel":
            self.pending = None
            return Response("Cancelled. Nothing changed.")
        if intent.kind == "quit":
            return Response("Bye.")
        if intent.kind == "noop":
            return Response("", hint="Type 'help' for examples.")
        if intent.kind == "greeting":
            return Response(
                "Mailkeep here. I can find mail, label it, draft replies and audit "
                "your trash. Try 'find invoices from last month'.",
            )
        if intent.kind == "help":
            return Response(HELP_TEXT)
        if intent.kind == "status":
            return self._status()
        if intent.kind == "labels":
            return self._labels()
        if intent.kind == "unknown":
            return Response(
                "I did not understand that.",
                hint="Type 'help' for commands, or /search followed by a Gmail query.",
            )
        if intent.kind in {"find", "find_raw"}:
            return self._find(intent)
        if intent.kind == "trash_report":
            return self._trash_report(intent)
        if intent.kind == "open":
            return self._open(intent)
        if intent.kind == "label":
            return self._label(intent)
        if intent.kind == "unlabel":
            return self._unlabel(intent)
        if intent.kind == "restore":
            return self._bulk_label_change(
                intent,
                add=[],
                remove=["TRASH"],
                verb="Restored",
                question="Move these messages out of Trash?",
            )
        if intent.kind == "archive":
            return self._bulk_label_change(
                intent,
                add=[],
                remove=["INBOX"],
                verb="Archived",
                question="Archive these messages (remove them from the inbox)?",
            )
        if intent.kind == "mark_read":
            return self._mark_read(intent)
        if intent.kind == "delete":
            return self._bulk_label_change(
                intent,
                add=["TRASH"],
                remove=[],
                verb="Moved to Trash",
                question="Move these messages to Trash? (reversible)",
            )
        if intent.kind == "draft_reply":
            return self._draft_reply(intent)
        if intent.kind == "draft_new":
            return self._draft_new(intent)
        return Response("I did not understand that.", hint="Type 'help'.")

    # -- pending questions --------------------------------------------
    def _continue_pending(self, text: str) -> Response:
        pending = self.pending or {}
        stage = pending.get("stage")
        self.pending = None
        if stage == "draft_body":
            draft = dict(pending["draft"])
            draft["body"] = text.strip()
            return self._create_draft(draft)
        if stage == "label_name":
            return self._label_refs(
                pending.get("refs", ""),
                text.strip(),
                query=pending.get("query", ""),
                all_matching=bool(pending.get("all_matching")),
            )
        if stage == "label_refs":
            return self._label_refs(
                text.strip(),
                pending.get("label", ""),
                query="",
                all_matching=False,
            )
        return Response("State reset.", hint="Type 'help'.")

    # -- reads ---------------------------------------------------------
    def _find(self, intent: Intent) -> Response:
        query = intent.query.strip()
        emails = self.backend.search(query, intent.limit)
        self.last_results = emails
        self.last_query = query
        self.last_opened = None
        if not emails:
            return Response(
                f"No messages matched: {query or '(everything)'}.",
                hint="Try fewer words, or use /search with a raw Gmail query.",
            )
        return Response(
            f"Found {len(emails)} message(s) for: {query or '(everything)'}",
            table=_table(emails),
            hint="Use the numbers with open / label / draft / archive.",
        )

    def _labels(self) -> Response:
        labels = self.backend.labels()
        rows = [[label.name, label.id, label.type] for label in labels]
        return Response(f"{len(labels)} labels.", table=Table(["Name", "ID", "Type"], rows))

    def _status(self) -> Response:
        lines = [f"Account: {self.backend.profile_email()}"]
        lines.append(f"Last query: {self.last_query or '(none)'}")
        lines.append(f"Last list: {len(self.last_results)} message(s)")
        if self.pending:
            lines.append(f"Waiting for: {self.pending.get('stage')}")
        return Response("\n".join(lines))

    def _open(self, intent: Intent) -> Response:
        email = self._one(intent.refs)
        full = self.backend.get_full(email.id)
        self.last_opened = full
        lines = [
            f"From: {full.sender} <{full.sender_email}>",
            f"To: {full.to or '(unknown)'}",
            f"Date: {full.date.strftime('%Y-%m-%d %H:%M') if full.date else '?'}",
            f"Subject: {full.subject}",
            f"Labels: {', '.join(full.labels) or '(none)'}",
        ]
        if full.attachments:
            names = ", ".join(
                f"{item.filename} ({human_bytes(item.size)})"
                for item in full.attachments
            )
            lines.append(f"Attachments: {names}")
        body = (full.body or full.snippet).strip()
        if len(body) > 4000:
            body = body[:4000] + "\n...[truncated]"
        lines.append("")
        lines.append(body or "(no text body)")
        return Response(
            "\n".join(lines),
            hint="Draft a reply with: draft a reply to this saying ...",
        )

    def _trash_report(self, intent: Intent) -> Response:
        emails = self.backend.search("in:trash", intent.limit)
        if not emails:
            self.last_results = []
            return Response("Trash is empty.")
        report = analyze_trash(emails)
        self.last_results = report.restore_candidates
        self.last_opened = None

        lines = [
            f"Trash: {report.total} message(s), {human_bytes(report.total_bytes)}",
        ]
        if report.oldest and report.newest:
            oldest_days = (
                (report.newest.date - report.oldest.date).days
                if report.oldest.date and report.newest.date
                else 0
            )
            lines.append(
                f"Range: {report.oldest.date:%Y-%m-%d} to "
                f"{report.newest.date:%Y-%m-%d} ({oldest_days} days span)"
            )
        if report.by_category:
            parts = [
                f"{category} {count} ({human_bytes(size)})"
                for category, (count, size) in report.by_category.items()
            ]
            lines.append("Types: " + ", ".join(parts))
        if report.top_senders:
            parts = [
                f"{sender} x{count} ({human_bytes(size)})"
                for sender, count, size in report.top_senders[:5]
            ]
            lines.append("Top senders: " + ", ".join(parts))
        lines.append(
            f"Restore candidates: {len(report.restore_candidates)} | "
            f"Likely safe to purge: {len(report.disposable)} | "
            f"Duplicate subject groups: {len(report.duplicates)}"
        )
        if len(emails) >= intent.limit:
            lines.append(
                f"Note: analysed the newest {len(emails)} trash messages (limit). "
                "Increase --limit for a wider sample."
            )
        if report.duplicates:
            sample = "; ".join(
                f"{group[0].subject!r} x{len(group)}"
                for _key, group in report.duplicates[:3]
            )
            lines.append(f"Duplicates: {sample}")

        table = None
        if report.restore_candidates:
            shown = report.restore_candidates[:15]
            rows = [
                [
                    str(index),
                    email.date.strftime("%Y-%m-%d") if email.date else "?",
                    _clip(email.sender, 24),
                    _clip(email.subject, 46),
                    report.categories.get(email.id, "other"),
                    email.id,
                ]
                for index, email in enumerate(shown, start=1)
            ]
            table = Table(["#", "Date", "From", "Subject", "Why", "ID"], rows)

        hint = (
            "restore 1-3 moves candidates back to the inbox; numbers refer to the "
            "restore table. Mailkeep never empties Trash permanently."
        )
        return Response("\n".join(lines), table=table, hint=hint)

    # -- references ----------------------------------------------------
    def _one(self, spec: str) -> Email:
        if spec.strip().lower() in {"last", "this", "it"} and self.last_opened:
            return self.last_opened
        return self._resolve(spec)[0]

    def _resolve(self, spec: str) -> list[Email]:
        if spec.strip().lower() in {"last", "this", "it"}:
            if self.last_opened:
                return [self.last_opened]
            if not self.last_results:
                raise AssistantError("No message is open or listed yet.")
            return [self.last_results[-1]]
        if not self.last_results:
            raise AssistantError(
                "No current list. Run a find or 'analyze trash' first."
            )
        indexes = _resolve_refs(spec, len(self.last_results))
        return [self.last_results[index - 1] for index in indexes]

    # -- label writes --------------------------------------------------
    def _label(self, intent: Intent) -> Response:
        if not intent.refs and not intent.all_matching:
            return Response("Which messages should I label?")
        if not intent.label:
            self.pending = {
                "stage": "label_name",
                "refs": intent.refs,
                "query": intent.query,
                "all_matching": intent.all_matching,
            }
            return Response(
                "What label should I use?",
                hint="Try a name like Finance, Receipts or Mailkeep/Invoices. /cancel aborts.",
            )
        return self._label_refs(
            intent.refs,
            intent.label,
            query=intent.query,
            all_matching=intent.all_matching,
        )

    def _label_refs(
        self,
        refs: str,
        label_name: str,
        *,
        query: str = "",
        all_matching: bool = False,
    ) -> Response:
        label_name = label_name.strip().strip("'\"")
        if not label_name:
            return Response("A label name is required.")

        if all_matching:
            emails = self.backend.search(query, self.limit)
            self.last_results = emails
            target = f"all messages matching {query!r}"
        else:
            emails = self._resolve(refs)
            target = f"{len(emails)} selected message(s)"
        if not emails:
            return Response("Nothing matched, so no label was applied.")

        existing = {item.name.lower(): item for item in self.backend.labels()}
        label = existing.get(label_name.lower())
        self.notify(
            Response(
                f"About to add label {label_name!r} to {target}."
                + (
                    ""
                    if label
                    else " The label does not exist yet and will be created."
                ),
                table=_table(emails),
                hint="Answer y to apply, anything else to cancel.",
            )
        )
        if not self.confirm(f"Apply label {label_name!r} to {len(emails)} message(s)?"):
            return Response("Cancelled. Nothing changed.")

        label, created = self.backend.ensure_label(label_name)
        self.backend.label_messages([email.id for email in emails], add_label_ids=[label.id])
        verification = self._verify_label(emails[0].id, label)
        verb = "Created" if created else "Used existing"
        text = f"{verb} label {label.name!r} ({label.id}). Added to {len(emails)} message(s)."
        if not verification:
            text += " Warning: the first message did not show the label on re-read."
        return Response(text)

    def _unlabel(self, intent: Intent) -> Response:
        emails = self._resolve(intent.refs)
        existing = {item.name.lower(): item for item in self.backend.labels()}
        label = existing.get(intent.label.lower())
        if label is None:
            return Response(f"No label named {intent.label!r} exists.")
        self.notify(
            Response(
                f"About to remove label {label.name!r} from {len(emails)} message(s).",
                table=_table(emails),
                hint="Answer y to apply, anything else to cancel.",
            )
        )
        if not self.confirm(f"Remove {label.name!r} from {len(emails)} message(s)?"):
            return Response("Cancelled. Nothing changed.")
        self.backend.label_messages(
            [email.id for email in emails], remove_label_ids=[label.id]
        )
        return Response(f"Removed label {label.name!r} from {len(emails)} message(s).")

    def _bulk_label_change(
        self,
        intent: Intent,
        *,
        add: list[str],
        remove: list[str],
        verb: str,
        question: str,
    ) -> Response:
        emails = self._resolve(intent.refs)
        self.notify(
            Response(
                f"{question} ({len(emails)} message(s))",
                table=_table(emails),
                hint="Answer y to apply, anything else to cancel.",
            )
        )
        if not self.confirm(question):
            return Response("Cancelled. Nothing changed.")
        self.backend.label_messages(
            [email.id for email in emails], add_label_ids=add, remove_label_ids=remove
        )
        return Response(f"{verb}: {len(emails)} message(s).")

    def _mark_read(self, intent: Intent) -> Response:
        emails = self._resolve(intent.refs)
        self.backend.label_messages(
            [email.id for email in emails], remove_label_ids=["UNREAD"]
        )
        return Response(f"Marked {len(emails)} message(s) as read.")

    def _verify_label(self, message_id: str, label) -> bool:
        try:
            updated = self.backend.fetch([message_id])
        except Exception:
            return False
        if not updated:
            return False
        return label.id in updated[0].labels or label.name in updated[0].labels

    # -- drafts --------------------------------------------------------
    def _draft_reply(self, intent: Intent) -> Response:
        email = self._one(intent.refs)
        full = self.backend.get_full(email.id)
        if not full.sender_email:
            raise AssistantError("The sender address is missing, so a reply cannot be addressed.")
        subject = full.subject
        if not re.match(r"^\s*re\s*:", subject, re.I):
            subject = f"Re: {subject}"
        references = " ".join(
            part for part in [full.references, full.message_id_header] if part
        )
        draft = {
            "to": full.sender_email,
            "subject": subject,
            "body": intent.body.strip(),
            "thread_id": full.thread_id,
            "in_reply_to": full.message_id_header,
            "references": references,
        }
        if not draft["body"]:
            self.pending = {"stage": "draft_body", "draft": draft}
            return Response(
                "What should the reply say?",
                hint="Type the body, or /cancel to abort. It will be a draft only.",
            )
        return self._create_draft(draft)

    def _draft_new(self, intent: Intent) -> Response:
        draft = {
            "to": intent.to,
            "subject": intent.subject or "(no subject)",
            "body": intent.body.strip(),
            "thread_id": "",
            "in_reply_to": "",
            "references": "",
        }
        if not draft["body"]:
            self.pending = {"stage": "draft_body", "draft": draft}
            return Response(
                "What should the draft say?",
                hint="Type the body, or /cancel to abort. It will be a draft only.",
            )
        return self._create_draft(draft)

    def _create_draft(self, draft: dict) -> Response:
        body = draft.get("body", "").strip()
        if not body:
            return Response("The draft is empty, so nothing was created.")
        preview = [
            f"To: {draft['to']}",
            f"Subject: {draft['subject']}",
        ]
        if draft.get("thread_id"):
            preview.append(f"Thread: {draft['thread_id']} (reply in place)")
        preview.append("")
        preview.append(_clip(body, 600))
        self.notify(
            Response(
                "Draft preview (nothing has been created yet):\n" + "\n".join(preview),
                hint="Answer y to create the draft, anything else to cancel.",
            )
        )
        if not self.confirm(f"Create this draft to {draft['to']}?"):
            return Response("Draft discarded. Nothing was created.")
        result: DraftResult = self.backend.create_draft(
            to=draft["to"],
            subject=draft["subject"],
            body=body,
            thread_id=draft.get("thread_id", ""),
            in_reply_to=draft.get("in_reply_to", ""),
            references=draft.get("references", ""),
        )
        return Response(
            f"Draft created: {result.draft_id} (message {result.message_id}). "
            "It is in Gmail Drafts and was not sent.",
            hint="Review it in Gmail, then send it yourself.",
        )
