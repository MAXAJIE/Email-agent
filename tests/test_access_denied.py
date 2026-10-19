"""Error 403 access_denied: message must survive and name the fix."""
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from mailkeep import auth
from mailkeep.web import App, make_handler


def test_access_denied_message_names_test_users(monkeypatch):
    monkeypatch.setattr(auth, "client_project_number", lambda: "123456")
    text = auth.consent_error_message("access_denied")
    assert "Test users" in text and "123456" in text


def test_unknown_error_is_reported_verbatim():
    assert "weird_error" in auth.consent_error_message("weird_error")


def test_login_hint_from_setting(monkeypatch):
    monkeypatch.setattr(auth, "setting", lambda n: "me@gmail.com" if n == "MAILKEEP_GMAIL_ACCOUNT" else "")
    assert auth.login_hint() == "me@gmail.com"


def test_denied_callback_page_stays_open_and_status_keeps_error():
    app = App(demo=False, limit=5, port=8799)
    server = ThreadingHTTPServer(("127.0.0.1", 8799), make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        try:
            urllib.request.urlopen("http://localhost:8799/oauth/callback?error=access_denied")
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            assert exc.code == 403
            assert "close()" not in body and "Test users" in body
        assert "access_denied" in app.auth_error
    finally:
        server.shutdown()
