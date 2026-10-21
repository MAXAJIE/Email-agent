"""In-memory Gmail stand-in.

`python -m mailkeep --demo` exercises the real assistant, intent parser and
console without OAuth. It also gives tests a deterministic backend.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .classify import CATEGORY_QUERIES, classify_email
from .models import Attachment, DraftResult, Email, Label


def _when(days_ago: int, hour: int = 9) -> datetime:
    return (datetime.now().astimezone() - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def _mail(
    index: int,
    *,
    sender: str,
    address: str,
    subject: str,
    snippet: str,
    days_ago: int,
    labels: tuple[str, ...] = ("INBOX",),
    size: int = 48_000,
    attachment: str = "",
    unsubscribe: bool = False,
) -> Email:
    attachments = (
        [Attachment(attachment, "application/pdf", 210_000, f"att-{index}")]
        if attachment
        else []
    )
    return Email(
        id=f"demo{index:04d}",
        thread_id=f"thread{index:04d}",
        subject=subject,
        sender=sender,
        sender_email=address,
        date=_when(days_ago),
        snippet=snippet,
        labels=labels,
        size=size + (210_000 if attachment else 0),
        has_attachment=bool(attachment),
        unsubscribe=unsubscribe,
        message_id_header=f"<demo{index:04d}@mailkeep.local>",
        to="demo@mailkeep.app",
        body=f"{snippet}\n\nThis is a demo message body used by --demo mode.",
        attachments=attachments,
    )


def _seed() -> list[Email]:
    return [
        _mail(
            1,
            sender="Amazon",
            address="auto-confirm@amazon.com",
            subject="Your Amazon.com order has shipped (Order 114-882)",
            snippet="Your order is on the way. Invoice attached.",
            days_ago=2,
            labels=("INBOX", "UNREAD"),
            size=52_000,
            attachment="amazon-invoice.pdf",
        ),
        _mail(
            2,
            sender="Northwind Billing",
            address="bills@northwind.example",
            subject="Invoice INV-2041 for September",
            snippet="Payment is due on 30 October. Invoice attached.",
            days_ago=9,
            attachment="INV-2041.pdf",
            size=61_000,
        ),
        _mail(
            3,
            sender="Stripe",
            address="receipts@stripe.com",
            subject="Your October receipt from Acme",
            snippet="Payment of USD 49.00 succeeded. Receipt attached.",
            days_ago=4,
            attachment="receipt-oct.pdf",
        ),
        _mail(
            4,
            sender="The Pragmatic Engineer",
            address="newsletter@substack.com",
            subject="The Pragmatic Engineer - weekly digest",
            snippet="This week: platform teams, AI tooling and hiring.",
            days_ago=1,
            size=38_000,
            unsubscribe=True,
        ),
        _mail(
            5,
            sender="TechCrunch",
            address="daily@techcrunch.com",
            subject="TechCrunch Daily: chips, clouds and startups",
            snippet="Today's top stories. Unsubscribe at any time.",
            days_ago=3,
            size=44_000,
            unsubscribe=True,
        ),
        _mail(
            6,
            sender="Medium",
            address="noreply@medium.com",
            subject="Medium daily digest",
            snippet="Stories picked for you. View in browser.",
            days_ago=6,
            size=51_000,
            unsubscribe=True,
        ),
        _mail(
            7,
            sender="GitHub",
            address="notifications@github.com",
            subject="Security alert for your account",
            snippet="A new sign-in was detected. Review this alert.",
            days_ago=1,
            labels=("INBOX", "UNREAD", "CATEGORY_UPDATES"),
        ),
        _mail(
            8,
            sender="Maybank",
            address="no-reply@bank.example",
            subject="Verify your new sign-in",
            snippet="We noticed a sign-in from a new device.",
            days_ago=5,
            labels=("INBOX", "CATEGORY_UPDATES"),
        ),
        _mail(
            9,
            sender="LinkedIn Jobs",
            address="jobs-noreply@linkedin.com",
            subject="Interview request: Backend Engineer at Acme",
            snippet="A recruiter would like to schedule an interview.",
            days_ago=8,
            labels=("INBOX", "UNREAD", "CATEGORY_UPDATES"),
        ),
        _mail(
            10,
            sender="Acme Careers",
            address="careers@acme.example",
            subject="Your application to Acme",
            snippet="Thanks for applying. We will review your application.",
            days_ago=12,
        ),
        _mail(
            11,
            sender="AirAsia",
            address="bookings@airasia.example",
            subject="Booking confirmation: KL to Singapore",
            snippet="Your itinerary and boarding pass are attached.",
            days_ago=20,
            attachment="itinerary.pdf",
        ),
        _mail(
            12,
            sender="Sarah Chen",
            address="sarah@example.com",
            subject="Dinner on Friday?",
            snippet="Are you free for dinner this Friday?",
            days_ago=1,
            labels=("INBOX", "UNREAD"),
        ),
        _mail(
            13,
            sender="MegaStore",
            address="shop@store.example",
            subject="50% off everything this weekend",
            snippet="Limited time sale. Use coupon SAVE50.",
            days_ago=2,
            labels=("INBOX", "CATEGORY_PROMOTIONS"),
            unsubscribe=True,
        ),
        _mail(
            14,
            sender="Grab",
            address="receipt@grabfood.example",
            subject="Your Grab receipt",
            snippet="Thanks for riding. Payment received.",
            days_ago=3,
        ),
        _mail(
            15,
            sender="Daniel Tan",
            address="daniel@work.example",
            subject="Team offsite notes",
            snippet="Notes and action items from the offsite.",
            days_ago=7,
            attachment="offsite-notes.pdf",
        ),
        _mail(
            16,
            sender="Maybank",
            address="statements@bank.example",
            subject="Statement for account ending 4412",
            snippet="Your monthly statement is ready.",
            days_ago=15,
            attachment="statement.pdf",
            size=77_000,
        ),
        # Trash: a mix of disposable and accidentally deleted mail.
        _mail(
            101,
            sender="MegaStore",
            address="shop@store.example",
            subject="Last chance: 70% off",
            snippet="Final hours of the sale.",
            days_ago=45,
            labels=("TRASH",),
            unsubscribe=True,
        ),
        _mail(
            102,
            sender="The Pragmatic Engineer",
            address="newsletter@substack.com",
            subject="Weekly newsletter #142",
            snippet="Platform engineering deep dive.",
            days_ago=60,
            labels=("TRASH",),
            unsubscribe=True,
        ),
        _mail(
            103,
            sender="The Pragmatic Engineer",
            address="newsletter@substack.com",
            subject="Weekly newsletter #141",
            snippet="Hiring and org design.",
            days_ago=67,
            labels=("TRASH",),
            unsubscribe=True,
        ),
        _mail(
            104,
            sender="Northwind Billing",
            address="bills@northwind.example",
            subject="Your invoice for September",
            snippet="Invoice attached for your records.",
            days_ago=40,
            labels=("TRASH",),
            attachment="invoice-sep.pdf",
        ),
        _mail(
            105,
            sender="Acme Recruiting",
            address="recruiting@acme.example",
            subject="Interview invitation: Platform Engineer",
            snippet="We would like to invite you to interview.",
            days_ago=55,
            labels=("TRASH",),
        ),
        _mail(
            106,
            sender="AirAsia",
            address="bookings@airasia.example",
            subject="Your boarding pass",
            snippet="Boarding pass for flight AK 702.",
            days_ago=90,
            labels=("TRASH",),
            attachment="boarding-pass.pdf",
        ),
        _mail(
            107,
            sender="Shopee",
            address="promo@shopee.example",
            subject="Flash sale today only",
            snippet="Up to 80% off. Limited time deal.",
            days_ago=35,
            labels=("TRASH",),
            unsubscribe=True,
        ),
        _mail(
            108,
            sender="GitHub",
            address="notifications@github.com",
            subject="Security alert for your account",
            snippet="Review this security alert.",
            days_ago=70,
            labels=("TRASH",),
        ),
        _mail(
            109,
            sender="Stripe",
            address="receipts@stripe.com",
            subject="Receipt for your payment",
            snippet="Payment of USD 12.00 received.",
            days_ago=28,
            labels=("TRASH",),
        ),
        _mail(
            110,
            sender="Medium",
            address="noreply@medium.com",
            subject="Medium daily digest",
            snippet="Stories picked for you.",
            days_ago=120,
            labels=("TRASH",),
            unsubscribe=True,
        ),
        _mail(
            111,
            sender="Acme Billing",
            address="billing@acme.example",
            subject="Your subscription renewal invoice",
            snippet="Your subscription renewed. Invoice attached.",
            days_ago=33,
            labels=("TRASH",),
            attachment="renewal.pdf",
        ),
        _mail(
            112,
            sender="Medium",
            address="noreply@medium.com",
            subject="Medium daily digest",
            snippet="Stories picked for you.",
            days_ago=100,
            labels=("TRASH",),
            unsubscribe=True,
        ),
    ]


_SYSTEM_LABELS = {
    "INBOX",
    "TRASH",
    "SENT",
    "DRAFT",
    "SPAM",
    "UNREAD",
    "STARRED",
    "IMPORTANT",
    "CATEGORY_PERSONAL",
    "CATEGORY_SOCIAL",
    "CATEGORY_PROMOTIONS",
    "CATEGORY_UPDATES",
    "CATEGORY_FORUMS",
}

_IGNORED_TOKENS = {
    "what",
    "have",
    "do",
    "i",
    "hi",
    "there",
    "any",
    "for",
    "me",
}


class DemoGmailClient:
    """Same surface as `GmailClient`, backed by an in-memory list."""

    def __init__(self):
        self._emails = _seed()
        self._labels = set(_SYSTEM_LABELS)
        self._label_names: dict[str, str] = {}
        self.drafts: list[dict] = []

    def profile_email(self) -> str:
        return "demo@mailkeep.app"

    def _haystack(self, email: Email) -> str:
        return " ".join(
            (email.sender, email.sender_email, email.subject, email.snippet)
        ).lower()

    def _matches(self, email: Email, query: str) -> bool:
        lowered = query.lower()
        if "in:trash" in lowered:
            if "TRASH" not in email.labels:
                return False
        elif "TRASH" in email.labels:
            return False
        if "in:sent" in lowered and "SENT" not in email.labels:
            return False
        if "is:unread" in lowered and "UNREAD" not in email.labels:
            return False
        if "is:starred" in lowered and "STARRED" not in email.labels:
            return False
        if "has:attachment" in lowered and not email.has_attachment:
            return False

        sent_at = email.date or datetime.now().astimezone()
        for match in re.finditer(r"newer_than:(\d+)d", lowered):
            if sent_at < datetime.now().astimezone() - timedelta(days=int(match.group(1))):
                return False
        for match in re.finditer(r"older_than:(\d+)d", lowered):
            if sent_at > datetime.now().astimezone() - timedelta(days=int(match.group(1))):
                return False
        for match in re.finditer(r"after:(\d{4})/(\d{1,2})/(\d{1,2})", lowered):
            year, month, day = (int(part) for part in match.groups())
            if sent_at.date() <= datetime(year, month, day).date():
                return False
        for match in re.finditer(r"before:(\d{4})/(\d{1,2})/(\d{1,2})", lowered):
            year, month, day = (int(part) for part in match.groups())
            if sent_at.date() >= datetime(year, month, day).date():
                return False

        for category, clause in CATEGORY_QUERIES.items():
            if clause in query and classify_email(email) != category:
                return False

        for match in re.finditer(r'from:("[^"]+"|\S+)', query, re.I):
            sender = match.group(1).strip('"').lower()
            if sender not in self._haystack(email):
                return False

        residual = re.sub(r"\([^)]*\)", " ", query)
        residual = re.sub(
            r"\b(?:from|in|is|has|newer_than|older_than|after|before):\S+",
            " ",
            residual,
        )
        tokens = [
            token.lower()
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9@._'+-]*", residual)
            if len(token) > 1 and token.lower() not in _IGNORED_TOKENS
        ]
        haystack = self._haystack(email)
        return all(token in haystack for token in tokens)

    def search(self, query: str, limit: int = 20) -> list[Email]:
        matched = [email for email in self._emails if self._matches(email, query)]
        matched.sort(
            key=lambda email: email.date.timestamp() if email.date else 0.0,
            reverse=True,
        )
        return matched[:limit]

    def fetch(self, message_ids: list[str]) -> list[Email]:
        by_id = {email.id: email for email in self._emails}
        return [by_id[mid] for mid in message_ids if mid in by_id]

    def search_label(self, label_id: str, limit: int = 20) -> list[Email]:
        matched = [x for x in self._emails if label_id in x.labels
                   and "TRASH" not in x.labels and "SPAM" not in x.labels]
        matched.sort(key=lambda x: x.date.timestamp() if x.date else 0, reverse=True)
        return matched[:limit]

    def rename_label(self, label_id: str, name: str) -> Label:
        if label_id not in self._labels or label_id in _SYSTEM_LABELS:
            raise ValueError("Unknown or system label")
        self._label_names[label_id] = name
        return Label(id=label_id, name=name, type="user")

    def get_full(self, message_id: str) -> Email:
        found = self.fetch([message_id])
        if not found:
            raise KeyError(f"unknown demo message {message_id}")
        return found[0]

    def labels(self) -> list[Label]:
        return [
            Label(
                id=name,
                name=self._label_names.get(name, name),
                type="system" if name in _SYSTEM_LABELS else "user",
            )
            for name in sorted(self._labels)
        ]

    def ensure_label(self, name: str) -> tuple[Label, bool]:
        for label in self.labels():
            if label.name.casefold() == name.casefold():
                return label, False
        created = name not in self._labels
        self._labels.add(name)
        return Label(id=name, name=name, type="user"), created

    def label_messages(
        self,
        message_ids: list[str],
        *,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> None:
        add = set(add_label_ids or [])
        remove = set(remove_label_ids or [])
        for email in self.fetch(message_ids):
            labels = (set(email.labels) - remove) | add
            email.labels = tuple(sorted(labels))
            for label_id in add:
                if label_id not in _SYSTEM_LABELS:
                    self._labels.add(label_id)

    def create_draft(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        thread_id: str = "",
        in_reply_to: str = "",
        references: str = "",
    ) -> DraftResult:
        number = len(self.drafts) + 1
        self.drafts.append(
            {
                "to": to,
                "cc": cc,
                "subject": subject,
                "body": body,
                "thread_id": thread_id,
                "in_reply_to": in_reply_to,
                "references": references,
            }
        )
        return DraftResult(
            draft_id=f"demo-draft-{number}",
            message_id=f"demo-message-{number}",
            thread_id=thread_id or f"demo-thread-{number}",
        )
