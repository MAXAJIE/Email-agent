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


def test_chat_and_approval_round_trip(server):
    app, base = server
    status, out = call(base, "/api/chat", {"text": "find invoices"}, app.token)
    assert status == 200 and out["messages"][-1]["table"]["rows"]
    _, out = call(base, "/api/chat", {"text": "archive 1"}, app.token)
    assert out["approval"]
    _, done = call(base, "/api/approve", {"id": out["approval"]["id"], "approve": True}, app.token)
    assert done["messages"][0]["text"].startswith("Archived")
