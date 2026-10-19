"""Heuristic mail typing.

Gmail's own category labels are used when present; otherwise sender, subject
and snippet keywords decide. The same categories drive search queries, label
presets and the trash report, so a "newsletter" means the same thing
everywhere.
"""

from __future__ import annotations

import re

from .models import Email

CATEGORY_ORDER = (
    "invoices",
    "receipts",
    "newsletters",
    "notifications",
    "jobs",
    "travel",
    "promotions",
    "personal",
    "other",
)

CATEGORY_QUERIES = {
    "invoices": (
        '(invoice OR "order confirmation" OR "tax invoice" OR bill OR statement)'
    ),
    "receipts": '(receipt OR payment OR transaction OR "order confirmation")',
    "newsletters": '(newsletter OR unsubscribe OR digest OR "view in browser")',
    "notifications": (
        '(notification OR alert OR "security alert" OR "verify your" OR "sign-in")'
    ),
    "jobs": '(application OR interview OR recruiter OR "job offer")',
    "travel": (
        '(itinerary OR booking OR "boarding pass" OR flight OR reservation OR hotel)'
    ),
    "promotions": '(sale OR discount OR coupon OR promo OR "limited time")',
}

CATEGORY_LABELS = {
    "invoices": "Mailkeep/Invoices",
    "receipts": "Mailkeep/Receipts",
    "newsletters": "Mailkeep/Newsletters",
    "notifications": "Mailkeep/Notifications",
    "jobs": "Mailkeep/Jobs",
    "travel": "Mailkeep/Travel",
    "promotions": "Mailkeep/Promotions",
}

_SYNONYMS = {
    "invoices": ("invoice", "invoices", "bill", "bills", "statement", "statements", "order confirmation"),
    "receipts": ("receipt", "receipts", "payment", "payments", "transaction", "transactions"),
    "newsletters": ("newsletter", "newsletters", "digest", "digests", "substack", "mailing list"),
    "notifications": (
        "notification",
        "notifications",
        "alerts",
        "alert",
        "security alert",
        "sign-in",
        "verification",
    ),
    "jobs": (
        "job",
        "jobs",
        "application",
        "applications",
        "interview",
        "interviews",
        "recruiter",
        "recruiters",
        "job offer",
    ),
    "travel": (
        "travel",
        "itinerary",
        "booking",
        "bookings",
        "boarding pass",
        "flight",
        "flights",
        "reservation",
        "hotel",
    ),
    "promotions": (
        "promotion",
        "promotions",
        "sale",
        "sales",
        "discount",
        "coupons",
        "coupon",
        "deal",
        "deals",
        "offer",
        "offers",
    ),
}

_KEYWORDS = {
    "invoices": (r"\binvoice\b", r"\btax invoice\b", r"\bbill\b", r"\bstatement\b", r"order confirmation"),
    "receipts": (r"\breceipt\b", r"\bpayment\b", r"\btransaction\b", r"\bpaid\b", r"order confirmation"),
    "newsletters": (r"\bnewsletter\b", r"\bunsubscribe\b", r"\bdigest\b", r"view in browser", r"\bweekly\b"),
    "notifications": (
        r"\bnotification\b",
        r"\balert\b",
        r"security alert",
        r"verify your",
        r"\bsign-in\b",
        r"\b2fa\b",
    ),
    "jobs": (r"\bapplication\b", r"\binterview\b", r"\brecruiter\b", r"job offer", r"\bcv\b", r"\bresume\b"),
    "travel": (r"\bitinerary\b", r"\bbooking\b", r"boarding pass", r"\bflight\b", r"\breservation\b", r"\bhotel\b"),
    "promotions": (r"\bsale\b", r"\bdiscount\b", r"\bcoupon\b", r"\bpromo\b", r"limited time", r"\bdeal\b"),
}

_NOISY_DOMAINS = (
    "substack.com",
    "mailchimp",
    "beehiiv",
    "convertkit",
    "medium.com",
    "mailerlite",
    "hubspot",
    "sendgrid",
    "mailgun",
)

_SYSTEM_NOISE_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}


def category_query(categories: list[str]) -> str:
    clauses = [CATEGORY_QUERIES[c] for c in categories if c in CATEGORY_QUERIES]
    return " ".join(clauses)


def detect_categories(text: str) -> list[str]:
    """Return categories named in free text, in the order they appear."""
    lowered = text.lower()
    found: list[tuple[int, str]] = []
    for category, words in _SYNONYMS.items():
        positions = [lowered.find(w) for w in words if w in lowered]
        if positions:
            found.append((min(positions), category))
    found.sort()
    return [category for _, category in found]


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, f"Mailkeep/{category.title()}")


def _matches_any(patterns: tuple[str, ...], haystack: str) -> bool:
    return any(re.search(pattern, haystack, re.IGNORECASE) for pattern in patterns)


def classify_email(email: Email) -> str:
    """Best-effort type for one message; used for trash analysis and previews."""
    labels = set(email.labels)
    haystack = " ".join(
        (email.subject, email.snippet, email.sender, email.sender_email)
    )

    for category in ("invoices", "receipts", "jobs", "travel"):
        if _matches_any(_KEYWORDS[category], haystack):
            return category

    if email.unsubscribe or "CATEGORY_PROMOTIONS" in labels:
        return "promotions" if "CATEGORY_PROMOTIONS" in labels else "newsletters"

    if any(domain in email.sender_email.lower() for domain in _NOISY_DOMAINS):
        return "newsletters"

    if _matches_any(_KEYWORDS["newsletters"], haystack):
        return "newsletters"

    if labels & _SYSTEM_NOISE_LABELS:
        return "promotions" if "CATEGORY_PROMOTIONS" in labels else "notifications"

    if _matches_any(_KEYWORDS["notifications"], haystack):
        return "notifications"
    if _matches_any(_KEYWORDS["promotions"], haystack):
        return "promotions"

    if "INBOX" in labels and not email.snippet.lower().startswith("unsubscribe"):
        return "personal"
    return "other"


def is_restorable(category: str) -> bool:
    """Types a user is likely to miss after deleting by mistake."""
    return category in {"invoices", "receipts", "jobs", "travel", "personal"}


def is_disposable(category: str) -> bool:
    return category in {"newsletters", "promotions", "notifications"}
