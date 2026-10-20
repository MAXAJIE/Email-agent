import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from mailkeep.web import App, make_handler


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("MAILKEEP_AGENT", "rules")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), None)
    app = App(demo=True, limit=20, port=srv.server_address[1])
    srv.RequestHandlerClass = make_handler(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, f"http://localhost:{app.port}"
    srv.shutdown()


def call(base, path, body=None, token=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Mailkeep-Token": token or "", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_api_requires_token(server):
    app, base = server
    assert call(base, "/api/chat", {"text": "find invoices"})[0] == 403


def submit(base, app, text, request_id):
    return call(base, "/api/chat", {"text": text, "request_id": request_id}, app.token)


def poll(base, app, job_id, timeout=30.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status, out = call(base, f"/api/jobs/{job_id}", token=app.token)
        if status == 200 and out.get("state") != "running":
            return out
        time.sleep(0.05)
    raise AssertionError("task did not finish in time")


def test_chat_requires_a_request_id(server):
    app, base = server
    assert call(base, "/api/chat", {"text": "find invoices"}, app.token)[0] == 400


def test_chat_and_approval_round_trip(server):
    app, base = server
    status, out = submit(base, app, "find invoices", "req-1")
    assert status == 202 and out["job_id"] == "req-1"
    # Same request_id + same text is idempotent: it is the same task, not a
    # second run. Same id with different text is refused.
    assert submit(base, app, "find invoices", "req-1") == (202, {"job_id": "req-1"})
    assert submit(base, app, "other text", "req-1")[0] == 400
    done = poll(base, app, "req-1")
    assert done["state"] == "completed"
    assert done["outcome"]["messages"][-1]["table"]["rows"]
    assert call(base, "/api/jobs/no-such-job", token=app.token)[0] == 404
    _, out = submit(base, app, "archive 1", "req-2")
    done = poll(base, app, "req-2")
    approval = done["outcome"]["approval"]
    _, ack = call(base, "/api/approve", {"id": approval["id"], "approve": True}, app.token)
    assert ack["messages"][0]["text"].startswith("Archived")
