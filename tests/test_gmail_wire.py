import base64
from email import message_from_bytes
from datetime import datetime

from mailkeep.gmail import (
    GmailClient,
    _extract_body,
    _parse_metadata,
    build_raw_message,
)


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_build_raw_message_sets_reply_headers():
    raw = build_raw_message(
        to="alice@example.com",
        subject="Re: Hello",
        body="Thanks!",
        thread_id="thread-1",
        in_reply_to="<one@example.com>",
        references="<zero@example.com> <one@example.com>",
    )
    message = message_from_bytes(base64.urlsafe_b64decode(raw + "==="))
    assert message["To"] == "alice@example.com"
    assert message["Subject"] == "Re: Hello"
    assert message["In-Reply-To"] == "<one@example.com>"
    assert message["References"] == "<zero@example.com> <one@example.com>"
    assert "Thanks!" in message.get_payload(decode=True).decode()


def test_parse_metadata_reads_headers_attachment_and_unsubscribe():
    payload = {
        "headers": [
            {"name": "From", "value": "Amazon <auto-confirm@amazon.com>"},
            {"name": "To", "value": "me@example.com"},
            {"name": "Subject", "value": "Order shipped"},
            {"name": "List-Unsubscribe", "value": "<https://example.com/u>"},
            {"name": "Message-ID", "value": "<abc@example.com>"},
            {"name": "References", "value": "<parent@example.com>"},
        ],
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": _b64("hello")},
            },
            {
                "mimeType": "application/pdf",
                "filename": "invoice.pdf",
                "body": {"size": 1234, "attachmentId": "att-1"},
            },
        ],
    }
    message = {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "hello",
        "internalDate": str(int(datetime(2026, 10, 1, 9).timestamp() * 1000)),
        "sizeEstimate": 4096,
        "payload": payload,
    }
    email = _parse_metadata(message)
    assert email.id == "m1"
    assert email.sender == "Amazon"
    assert email.sender_email == "auto-confirm@amazon.com"
    assert email.subject == "Order shipped"
    assert email.has_attachment is True
    assert email.attachments[0].filename == "invoice.pdf"
    assert email.unsubscribe is True
    assert email.message_id_header == "<abc@example.com>"
    assert email.labels == ("INBOX", "UNREAD")


def test_extract_body_prefers_text_plain():
    payload = {
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>rich</p>")}},
            {"mimeType": "text/plain", "body": {"data": _b64("plain body")}},
        ]
    }
    body, attachments = _extract_body(payload)
    assert body == "plain body"
    assert attachments == []


def test_extract_body_strips_html_when_only_html_exists():
    payload = {
        "parts": [
            {
                "mimeType": "text/html",
                "body": {"data": _b64("<div>Hello <b>there</b></div>")},
            }
        ]
    }
    body, _ = _extract_body(payload)
    assert "Hello" in body and "there" in body
    assert "<b>" not in body


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _Drafts:
    def __init__(self):
        self.captured = None

    def create(self, **kwargs):
        self.captured = kwargs
        return _Exec({"id": "d1", "message": {"id": "m1", "threadId": "t1"}})


class _Messages:
    def __init__(self):
        self.modified = []
        self.batched = []

    def modify(self, **kwargs):
        self.modified.append(kwargs)
        return _Exec({})

    def batchModify(self, **kwargs):
        self.batched.append(kwargs)
        return _Exec({})


class _Users:
    def __init__(self):
        self._drafts = _Drafts()
        self._messages = _Messages()

    def drafts(self):
        return self._drafts

    def messages(self):
        return self._messages


class _Service:
    def __init__(self):
        self._users = _Users()

    def users(self):
        return self._users


def test_create_draft_sends_thread_id_and_raw_message():
    service = _Service()
    client = GmailClient(service)
    result = client.create_draft(
        to="alice@example.com",
        subject="Re: Hello",
        body="Thanks",
        thread_id="thread-1",
    )
    assert result.draft_id == "d1"
    assert result.message_id == "m1"
    message = service._users._drafts.captured["body"]["message"]
    assert message["threadId"] == "thread-1"
    decoded = message_from_bytes(
        base64.urlsafe_b64decode(message["raw"] + "===")
    )
    assert decoded["Subject"] == "Re: Hello"


def test_label_messages_uses_single_modify_for_one_and_batch_for_many():
    service = _Service()
    client = GmailClient(service)
    client.label_messages(["m1"], add_label_ids=["Label_1"])
    client.label_messages(["m1", "m2"], add_label_ids=["Label_1"])
    assert service._users._messages.modified[0]["id"] == "m1"
    assert service._users._messages.batched[0]["body"]["ids"] == ["m1", "m2"]
