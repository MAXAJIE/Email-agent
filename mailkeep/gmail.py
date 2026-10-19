"""Gmail API backend used by the console assistant.

Only the calls Mailkeep needs are wrapped. ``messages.send`` is intentionally
absent: the product boundary is drafts, and a method that does not exist cannot
be called by accident.
"""

from __future__ import annotations

import base64
import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr

from .models import Attachment, DraftResult, Email, GmailError, Label

_METADATA_HEADERS = [
    "From",
    "To",
    "Cc",
    "Subject",
    "Date",
    "List-Unsubscribe",
    "Message-ID",
    "References",
]


def _walk_parts(payload: dict):
    yield payload
    for part in payload.get("parts") or []:
        yield from _walk_parts(part)


def _b64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?s)<br\s*/?>|</p>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(re.sub(r"[ \t]{2,}", " ", text)).strip()


def _parse_metadata(message: dict) -> Email:
    payload = message.get("payload") or {}
    headers = {
        str(header.get("name", "")).lower(): str(header.get("value", ""))
        for header in payload.get("headers") or []
    }
    display_name, address = parseaddr(headers.get("from", ""))
    sender = display_name or address or headers.get("from", "")
    labels = tuple(message.get("labelIds") or [])
    internal = message.get("internalDate")
    when = (
        datetime.fromtimestamp(int(internal) / 1000).astimezone()
        if internal
        else None
    )
    attachments = [
        Attachment(
            filename=str(part.get("filename") or ""),
            mime_type=str(part.get("mimeType") or ""),
            size=int((part.get("body") or {}).get("size") or 0),
            attachment_id=str((part.get("body") or {}).get("attachmentId") or ""),
        )
        for part in _walk_parts(payload)
        if part.get("filename")
    ]
    return Email(
        id=str(message.get("id", "")),
        thread_id=str(message.get("threadId", "")),
        subject=headers.get("subject", "") or "(no subject)",
        sender=sender,
        sender_email=address or headers.get("from", ""),
        date=when,
        snippet=str(message.get("snippet", "")),
        labels=labels,
        size=int(message.get("sizeEstimate") or 0),
        has_attachment=bool(attachments),
        unsubscribe=bool(headers.get("list-unsubscribe")),
        message_id_header=headers.get("message-id", ""),
        references=headers.get("references", ""),
        to=headers.get("to", ""),
        attachments=attachments,
    )


def _extract_body(payload: dict) -> tuple[str, list[Attachment]]:
    plain: list[str] = []
    rich: list[str] = []
    attachments: list[Attachment] = []
    for part in _walk_parts(payload):
        mime = str(part.get("mimeType") or "")
        filename = str(part.get("filename") or "")
        body = part.get("body") or {}
        data = body.get("data")
        if filename:
            attachments.append(
                Attachment(
                    filename=filename,
                    mime_type=mime,
                    size=int(body.get("size") or 0),
                    attachment_id=str(body.get("attachmentId") or ""),
                )
            )
            continue
        if not data:
            continue
        decoded = _b64(str(data)).decode("utf-8", errors="replace")
        if mime == "text/plain":
            plain.append(decoded)
        elif mime == "text/html":
            rich.append(decoded)
    if plain:
        return "\n".join(plain).strip(), attachments
    if rich:
        return _strip_html("\n".join(rich)), attachments
    return "", attachments


def build_raw_message(
    *,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    thread_id: str = "",
    in_reply_to: str = "",
    references: str = "",
) -> str:
    message = EmailMessage()
    message["To"] = to
    if cc:
        message["Cc"] = cc
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _error_reason(exc) -> str:
    try:
        payload = exc.error_details  # type: ignore[attr-defined]
    except Exception:
        payload = None
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict) and first.get("message"):
            return str(first["message"])
    return str(exc)


class GmailClient:
    """Thin synchronous wrapper around the Gmail v1 API."""

    def __init__(self, service):
        self._service = service
        self._profile_email = ""

    # -- reads ---------------------------------------------------------
    def profile_email(self) -> str:
        if self._profile_email:
            return self._profile_email
        try:
            profile = (
                self._service.users().getProfile(userId="me").execute()
            )
        except Exception as exc:
            raise GmailError(f"Could not read the Gmail profile: {exc}") from exc
        self._profile_email = str(profile.get("emailAddress", ""))
        return self._profile_email

    def _list_ids(self, query: str, limit: int) -> list[str]:
        collected: list[str] = []
        token: str | None = None
        while len(collected) < limit:
            params = {
                "userId": "me",
                "maxResults": min(500, limit - len(collected)),
            }
            if query:
                params["q"] = query
            if token:
                params["pageToken"] = token
            try:
                response = (
                    self._service.users().messages().list(**params).execute()
                )
            except Exception as exc:
                raise GmailError(f"Gmail search failed: {_error_reason(exc)}") from exc
            collected.extend(
                str(item["id"]) for item in response.get("messages") or []
            )
            token = response.get("nextPageToken")
            if not token:
                break
        return collected[:limit]

    def search(self, query: str, limit: int = 20) -> list[Email]:
        message_ids = self._list_ids(query, limit)
        if not message_ids:
            return []
        return self.fetch(message_ids)

    def fetch(self, message_ids: list[str]) -> list[Email]:
        if not message_ids:
            return []
        workers = max(1, min(6, len(message_ids)))

        def get_one(message_id: str) -> Email | None:
            try:
                message = (
                    self._service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=message_id,
                        format="metadata",
                        metadataHeaders=_METADATA_HEADERS,
                        fields=(
                            "id,threadId,labelIds,snippet,internalDate,"
                            "sizeEstimate,payload"
                        ),
                    )
                    .execute()
                )
            except Exception as exc:
                raise GmailError(
                    f"Could not read message {message_id}: {_error_reason(exc)}"
                ) from exc
            return _parse_metadata(message)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(get_one, message_ids))
        return [item for item in results if item is not None]

    def get_full(self, message_id: str) -> Email:
        try:
            message = (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except Exception as exc:
            raise GmailError(
                f"Could not open message {message_id}: {_error_reason(exc)}"
            ) from exc
        email = _parse_metadata(message)
        body, attachments = _extract_body(message.get("payload") or {})
        email.body = body
        email.attachments = attachments
        return email

    def labels(self) -> list[Label]:
        try:
            response = self._service.users().labels().list(userId="me").execute()
        except Exception as exc:
            raise GmailError(f"Could not list labels: {_error_reason(exc)}") from exc
        return [
            Label(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                type=str(item.get("type", "user")),
            )
            for item in response.get("labels") or []
        ]

    # -- writes --------------------------------------------------------
    def ensure_label(self, name: str) -> tuple[Label, bool]:
        existing = {label.name.lower(): label for label in self.labels()}
        if name.lower() in existing:
            return existing[name.lower()], False
        try:
            created = (
                self._service.users()
                .labels()
                .create(
                    userId="me",
                    body={
                        "name": name,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    },
                )
                .execute()
            )
        except Exception as exc:
            raise GmailError(
                f"Could not create label {name!r}: {_error_reason(exc)}"
            ) from exc
        return (
            Label(
                id=str(created.get("id", "")),
                name=str(created.get("name", name)),
                type=str(created.get("type", "user")),
            ),
            True,
        )

    def label_messages(
        self,
        message_ids: list[str],
        *,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> None:
        add = add_label_ids or []
        remove = remove_label_ids or []
        for start in range(0, len(message_ids), 1000):
            chunk = message_ids[start : start + 1000]
            try:
                if len(chunk) == 1:
                    self._service.users().messages().modify(
                        userId="me",
                        id=chunk[0],
                        body={"addLabelIds": add, "removeLabelIds": remove},
                    ).execute()
                else:
                    self._service.users().messages().batchModify(
                        userId="me",
                        body={
                            "ids": chunk,
                            "addLabelIds": add,
                            "removeLabelIds": remove,
                        },
                    ).execute()
            except Exception as exc:
                raise GmailError(
                    f"Could not update labels on {len(chunk)} message(s): "
                    f"{_error_reason(exc)}"
                ) from exc

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
        raw = build_raw_message(
            to=to,
            cc=cc,
            subject=subject,
            body=body,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
            references=references,
        )
        message: dict = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        try:
            draft = (
                self._service.users()
                .drafts()
                .create(userId="me", body={"message": message})
                .execute()
            )
        except Exception as exc:
            raise GmailError(f"Could not create the draft: {_error_reason(exc)}") from exc
        return DraftResult(
            draft_id=str(draft.get("id", "")),
            message_id=str((draft.get("message") or {}).get("id", "")),
            thread_id=str((draft.get("message") or {}).get("threadId", "")),
        )


def build_client(credentials=None) -> GmailClient:
    from googleapiclient.discovery import build

    if credentials is None:
        from .auth import load_credentials

        credentials = load_credentials(interactive=True)
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    return GmailClient(service)
