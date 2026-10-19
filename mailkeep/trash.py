"""Aggregate a trash listing into something a person can act on."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from .classify import classify_email, is_disposable, is_restorable
from .models import Email


@dataclass
class TrashReport:
    total: int
    total_bytes: int
    by_category: dict[str, tuple[int, int]] = field(default_factory=dict)
    top_senders: list[tuple[str, int, int]] = field(default_factory=list)
    restore_candidates: list[Email] = field(default_factory=list)
    disposable: list[Email] = field(default_factory=list)
    duplicates: list[tuple[str, list[Email]]] = field(default_factory=list)
    oldest: Email | None = None
    newest: Email | None = None
    categories: dict[str, str] = field(default_factory=dict)


def _age_days(email: Email, today: date) -> int:
    if email.date is None:
        return 0
    return max(0, (today - email.date.date()).days)


def _subject_key(subject: str) -> str:
    value = re.sub(r"^\s*(?:re|fwd?|fw)\s*:\s*", "", subject, flags=re.I)
    value = re.sub(r"[^a-z0-9 ]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def analyze_trash(emails: list[Email], today: date | None = None) -> TrashReport:
    today = today or date.today()
    report = TrashReport(total=len(emails), total_bytes=sum(e.size for e in emails))
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    senders: Counter[str] = Counter()
    sender_sizes: Counter[str] = Counter()
    by_subject: dict[str, list[Email]] = {}

    for email in emails:
        category = classify_email(email)
        report.categories[email.id] = category
        counts[category] += 1
        sizes[category] += email.size
        sender = email.sender or email.sender_email or "(unknown)"
        senders[sender] += 1
        sender_sizes[sender] += email.size
        if _subject_key(email.subject):
            by_subject.setdefault(_subject_key(email.subject), []).append(email)
        if is_restorable(category):
            report.restore_candidates.append(email)
        if is_disposable(category) and _age_days(email, today) >= 30:
            report.disposable.append(email)

    report.by_category = {
        category: (counts[category], sizes[category])
        for category in sorted(counts, key=lambda c: (-sizes[c], c))
    }
    report.top_senders = [
        (sender, count, sender_sizes[sender])
        for sender, count in senders.most_common(8)
    ]
    report.duplicates = [
        (key, group)
        for key, group in by_subject.items()
        if len(group) > 1 and key
    ]
    report.duplicates.sort(key=lambda item: -len(item[1]))

    dated = [email for email in emails if email.date is not None]
    if dated:
        report.oldest = min(dated, key=lambda email: email.date or 0)
        report.newest = max(dated, key=lambda email: email.date or 0)

    report.restore_candidates.sort(
        key=lambda email: email.date.timestamp() if email.date else 0.0,
        reverse=True,
    )
    report.disposable.sort(key=lambda email: _age_days(email, today), reverse=True)
    return report


def human_bytes(size: int) -> str:
    value = float(max(size, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
