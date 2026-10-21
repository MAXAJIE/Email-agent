"""Gmail API backend used by the console assistant.

Only the calls Mailkeep needs are wrapped. ``messages.send`` is intentionally
absent: the product boundary is drafts, and a method that does not exist cannot
be called by accident.
"""

from __future__ import annotations

import base64
import html
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from .config import setting
from .llm import positive_seconds
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

    def __init__(self, service, service_factory=None):
        self._shared_service = service
        self._service_factory = service_factory
        self._local = threading.local()
        self._profile_email = ""

    @property
    def _service(self):
        # httplib2.Http is not thread-safe. Each fetch worker needs its own
        # authenticated connection, not a shared discovery resource.
        if self._service_factory is None:
            return self._shared_service
        if not hasattr(self._local, "service"):
            self._local.service = self._service_factory()
        return self._local.service

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

    def _list_ids(self, query: str, limit: int, *, label_id: str = "") -> list[str]:
        collected: list[str] = []
        token: str | None = None
        while len(collected) < limit:
            params = {
                "userId": "me",
                "maxResults": min(500, limit - len(collected)),
            }
            if query:
                params["q"] = query
            if label_id:
                params["labelIds"] = [label_id]
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

    def search_label(self, label_id: str, limit: int = 20) -> list[Email]:
        """Use the API's labelIds filter, not an invented label-ID query."""
        return self.fetch(self._list_ids("", limit, label_id=label_id))

    def fetch(self, message_ids: list[str]) -> list[Email]:
        if not message_ids:
            return []
        try:
            requested = int(setting("MAILKEEP_GMAIL_WORKERS") or "6")
        except ValueError:
            requested = 6
        workers = max(1, min(16, requested, len(message_ids)))
        # Injected services without a factory cannot safely fetch in parallel.
        if self._service_factory is None:
            workers = 1

        failures: list[GmailError] = []

        def get_one(message_id: str) -> Email | None:
            try:
                message = self._read_retry(lambda: (
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
                ))
            except Exception as exc:
                # One vanished/unreadable message must not discard the rest
                # of the batch; only an all-failed batch is an error.
                error = GmailError(
                    f"Could not read message {message_id}: {_error_reason(exc)}"
                )
                error.__cause__ = exc
                failures.append(error)
                return None
            return _parse_metadata(message)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(get_one, message_ids))
        emails = [item for item in results if item is not None]
        if failures and not emails:
            raise failures[0]
        return emails

    def _read_retry(self, call):
        # At most two retries on transient failures; authentication/validation
        # errors fail immediately. Transport timeout bounds each attempt.
        for attempt in range(3):
            try:
                return call()
            except Exception as exc:
                status = getattr(getattr(exc, "resp", None), "status", None)
                transient = status in {429, 500, 502, 503, 504} or isinstance(exc, (TimeoutError, ConnectionError, OSError))
                if not transient or attempt == 2:
                    raise
                time.sleep(0.25 * 2 ** attempt)

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
        try:
            body, attachments = _extract_body(message.get("payload") or {})
        except ValueError as exc:  # binascii.Error on malformed base64
            raise GmailError(
                f"Could not decode message {message_id}: {exc}"
            ) from exc
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
    def rename_label(self, label_id: str, name: str) -> Label:
        try:
            updated = self._service.users().labels().patch(
                userId="me", id=label_id, body={"name": name}).execute()
        except Exception as exc:
            raise GmailError(f"Could not rename label: {_error_reason(exc)}") from exc
        return Label(id=str(updated.get("id", label_id)),
                     name=str(updated.get("name", name)), type="user")

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
        try:
            raw = build_raw_message(
                to=to,
                cc=cc,
                subject=subject,
                body=body,
                thread_id=thread_id,
                in_reply_to=in_reply_to,
                references=references,
            )
        except ValueError as exc:
            # e.g. CR/LF smuggled into Message-ID/References of the original.
            raise GmailError(f"Could not build the draft: {exc}") from exc
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
    from google_auth_httplib2 import AuthorizedHttp
    import httplib2

    if credentials is None:
        from .auth import load_credentials

        credentials = load_credentials(interactive=True)
    timeout = positive_seconds("MAILKEEP_GMAIL_TIMEOUT", 20)

    def service_factory():
        http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=timeout))
        return build("gmail", "v1", http=http, cache_discovery=False)

    return GmailClient(service_factory(), service_factory=service_factory)
