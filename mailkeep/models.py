"""Typed values shared by the Gmail backend, the assistant and the console."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Email:
    id: str
    thread_id: str
    subject: str
    sender: str
    sender_email: str
    date: datetime | None = None
    snippet: str = ""
    labels: tuple[str, ...] = ()
    size: int = 0
    has_attachment: bool = False
    unsubscribe: bool = False
    message_id_header: str = ""
    references: str = ""
    to: str = ""
    body: str = ""
    attachments: list["Attachment"] = field(default_factory=list)


@dataclass
class Attachment:
    filename: str
    mime_type: str = ""
    size: int = 0
    attachment_id: str = ""


@dataclass
class Label:
    id: str
    name: str
    type: str = "user"


@dataclass
class DraftResult:
    draft_id: str
    message_id: str
    thread_id: str


@dataclass
class Table:
    columns: list[str]
    rows: list[list[str]]


@dataclass
class Response:
    text: str = ""
    table: Table | None = None
    hint: str = ""


class GmailError(RuntimeError):
    """A Gmail call failed in a way worth showing to the user."""
