"""Timeout recheck (wait > 20 s), lenient router JSON, late-timeout keeps tool results."""
import threading
import time

from mailkeep import fastllm, router


def test_router_repairs_unescaped_quotes():
    raw = '{"action":"agent","agent":"mailbox","args":{"command":"/search "shopback""},"say":"ok"}'
    decision = router.parse(raw)
    assert decision.agent == "mailbox"
    assert decision.args["command"] == '/search "shopback"'


def test_router_repairs_truncated_json():
    decision = router.parse('{"action":"reply","reply":"Hi there"')
    assert decision.reply == "Hi there"


def test_watchdog_stops_when_provider_dead():
    dog = fastllm.Watchdog(lambda: fastllm.Health("offline", detail="down"), "t", every=0.05)
    end = time.monotonic() + 2
    reason = ""
    while not reason and time.monotonic() < end:
        reason = dog.dead()
        time.sleep(0.01)
    assert "stopped responding" in reason


def test_watchdog_keeps_waiting_when_online():
    notes = []
    dog = fastllm.Watchdog(lambda: fastllm.Health("online"), "t", notes.append, every=0.05)
    end = time.monotonic() + 0.4
    while time.monotonic() < end:
        assert dog.dead() == ""
        time.sleep(0.01)
    assert notes


def test_default_recheck_is_20_seconds(monkeypatch):
    monkeypatch.delenv("MAILKEEP_LLM_CHECK_AFTER", raising=False)
    monkeypatch.setattr(fastllm, "setting", lambda _n: "")
    assert fastllm.check_after() == 20
