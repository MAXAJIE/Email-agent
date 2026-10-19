from mailkeep.demo import DemoGmailClient
from mailkeep.trash import analyze_trash, human_bytes


def _trash():
    client = DemoGmailClient()
    return client, client.search("in:trash", 200)


def test_trash_report_counts_and_bytes():
    _, emails = _trash()
    report = analyze_trash(emails)
    assert report.total == len(emails) == 12
    assert report.total_bytes == sum(email.size for email in emails)
    assert report.total_bytes > 0
    assert report.oldest is not None and report.newest is not None


def test_restore_candidates_and_disposable_split():
    _, emails = _trash()
    report = analyze_trash(emails)
    assert len(report.restore_candidates) == 5
    assert all(
        email.id not in {candidate.id for candidate in report.restore_candidates}
        for email in report.disposable
    )
    assert {"invoices", "receipts", "travel"} <= set(report.categories.values())


def test_duplicate_subject_groups_are_detected():
    _, emails = _trash()
    report = analyze_trash(emails)
    assert len(report.duplicates) == 1
    _key, group = report.duplicates[0]
    assert all(email.subject == "Medium daily digest" for email in group)


def test_human_bytes():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1024) == "1.0 KB"
    assert human_bytes(1024 * 1024) == "1.0 MB"
