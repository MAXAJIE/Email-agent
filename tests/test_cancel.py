"""Ctrl+C / cancel() stops a thinking turn and resets the conversation."""
import threading, time

from mailkeep import agent as agent_mod
from mailkeep.demo import DemoGmailClient
from mailkeep.session import Session


def test_cancel_aborts_turn(monkeypatch):
    openminion = __import__("pytest").importorskip("openminion")
    a = agent_mod.MailkeepAgent(Session(DemoGmailClient()))
    a.mode = "openminion"

    class Slow:
        def run_stream(self, *args, **kw):
            time.sleep(5)
        def close(self):
            pass

    a._agent = Slow()
    a._history = [("old", "reply")]
    threading.Timer(0.3, a.cancel).start()
    started = time.monotonic()
    out = a.chat("how are u")
    assert time.monotonic() - started < 2
    assert "Stopped" in out.responses[-1].text
    assert a._history == [] and a._agent is None
