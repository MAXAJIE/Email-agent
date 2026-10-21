from types import SimpleNamespace as E

from mailkeep import understand

ROWS = [E(sender=s, subject="x") for s in [
    "Cursor Team", "ChatGPT", "ChatGPT", "ChatGPT", "Binance", "THE FINALS",
    "Google", "Shopee", "GitHub", "Google"]]


def test_keep_named_senders_trash_rest():
    cmd, kept = understand.list_selection(
        "remove github and google from here, it is useful. Put other into trash", ROWS)
    assert kept == [7, 9, 10]
    assert cmd == "delete 1-6,8"


def test_except_numbers():
    cmd, kept = understand.list_selection("trash all except 9 and 10", ROWS)
    assert kept == [9, 10] and cmd == "delete 1-8"


def test_archive_rest():
    cmd, _ = understand.list_selection("keep shopee, archive the rest", ROWS)
    assert cmd == "archive 1-7,9-10"


def test_no_selection_without_list_or_verb():
    assert understand.list_selection("keep github", ROWS) is None
    assert understand.list_selection("trash the others except github", []) is None


def test_default_budget_is_500k(monkeypatch):
    monkeypatch.delenv("MAILKEEP_CONTEXT_TOKENS", raising=False)
    assert understand.context_budget() == 500_000


def test_history_kept_far_beyond_three_turns():
    history = [(f"u{i}", "reply " * 50) for i in range(200)]
    assert len(understand.trim_history(history)) == 200


def test_history_trims_oldest_over_budget():
    history = [(f"u{i}", "a" * 4000) for i in range(10)]  # ~1000 tokens each
    kept = understand.trim_history(history, budget=3_100)
    assert [u for u, _ in kept] == ["u7", "u8", "u9"]


def test_cjk_tokens_counted_per_char():
    assert understand.estimate_tokens("记住上下文") == 5


def test_refine_keeps_earlier_kept_rows():
    cmd, kept = understand.list_selection("google and github is not trash, move other into trash", ROWS)
    assert kept == [7, 9, 10] and cmd == "delete 1-6,8"
    cmd, kept = understand.refine_selection("shopee is not trash", ROWS, kept, "delete")
    assert kept == [7, 8, 9, 10] and cmd == "delete 1-6"


def test_refine_trash_one_more():
    cmd, kept = understand.refine_selection("trash github too", ROWS, [7, 9, 10], "delete")
    assert kept == [7, 10] and cmd == "delete 1-6,8-9"


def test_refine_by_number_and_unrelated_text():
    cmd, kept = understand.refine_selection("keep 5 as well", ROWS, [9, 10], "delete")
    assert kept == [5, 9, 10] and cmd == "delete 1-4,6-8"
    assert understand.refine_selection("looks good", ROWS, [9, 10], "delete") is None


def test_agent_followup_refines_pending_card(monkeypatch):
    from mailkeep import agent as agent_mod, llm
    from mailkeep.demo import DemoGmailClient
    from mailkeep.session import Session
    monkeypatch.setattr(agent_mod, "resolve_mode", lambda: ("rules", "test"))
    monkeypatch.setattr(llm, "describe", lambda: [])
    monkeypatch.setattr(agent_mod, "trace", lambda kind, text: None)
    agent = agent_mod.MailkeepAgent(Session(DemoGmailClient()))
    agent.session.run_command("find in:inbox limit 6")
    rows = agent.session.assistant.last_results
    assert len(rows) >= 4
    first = str(rows[0].sender).split()[-1]
    second = str(rows[1].sender).split()[-1]
    agent.chat(f"keep {first}, trash the others")
    kept1 = list(agent._selection["kept"])
    assert 1 in kept1
    if second.lower() in first.lower():
        return
    agent.chat(f"{second} is not trash")
    kept2 = agent._selection["kept"]
    assert set(kept1) < set(kept2) and 2 in kept2
    assert agent.session.approval is agent._selection["approval"]
