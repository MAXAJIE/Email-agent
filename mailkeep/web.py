"""Local pop-up chat UI for Mailkeep (stdlib HTTP server, no Node build).

    python -m mailkeep ui            # app-style window on localhost
    python -m mailkeep ui --demo     # in-memory mailbox, no Google account

Hardening for a localhost server:
* binds 127.0.0.1 only;
* rejects any Host header other than localhost/127.0.0.1:<port> (DNS rebinding);
* every /api call carries a per-launch token embedded in the page (CSRF);
* never logs request lines (the OAuth callback query holds the auth code).
"""

from __future__ import annotations

import html
import json
import secrets
import shutil
import subprocess
import threading
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .agent import MailkeepAgent
from .auth import WebLogin, cached_credentials, forget_token
from .config import (
    ConfigError,
    client_type,
    has_client_credentials,
    save_client_file,
    save_client_type,
    ui_redirect_uri,
)
from .demo import DemoGmailClient
from .session import Outcome, Session
from .tasks import ChatJobs

STATIC = Path(__file__).with_name("static")


class App:
    def __init__(self, *, demo: bool, limit: int, port: int):
        self.demo = demo
        self.limit = limit
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.jobs = ChatJobs()
        self.login = WebLogin(ui_redirect_uri(port))
        self.lock = threading.Lock()
        self.agent: MailkeepAgent | None = None
        self.account = ""
        self.auth_error = ""
        if demo:
            self.attach(DemoGmailClient())

    def attach(self, backend) -> None:
        if self.agent:
            self.agent.close()
        self.agent = MailkeepAgent(Session(backend, limit=self.limit))
        try:
            self.account = backend.profile_email()
        except Exception:
            self.account = "(unknown)"

    def ensure_connected(self) -> bool:
        with self.lock:
            if self.agent:
                return True
            credentials = cached_credentials()
            if credentials is None:
                return False
            from .gmail import build_client

            self.attach(build_client(credentials))
            return True

    def status(self) -> dict:
        connected = self.ensure_connected()
        try:
            kind = client_type() if has_client_credentials() else ""
        except ConfigError:
            kind = ""
        return {
            "demo": self.demo,
            "connected": connected,
            "account": self.account if connected else "",
            "client_configured": has_client_credentials(),
            "client_type": kind,
            "redirect_uri": self.login.redirect,
            "mode": self.agent.mode if self.agent else "",
            "mode_reason": self.agent.reason if self.agent else "",
            "auth_error": "" if connected else self.auth_error,
            "llm": self.llm_health(),
        }

    def llm_health(self, refresh: bool = False) -> dict:
        agent = self.agent
        if agent is None or getattr(agent, "health", None) is None or agent.mode != "openminion":
            return {"state": "off", "detail": "rule parser only"}
        health = agent.health.check_now() if refresh else agent.health.snapshot()
        return health.public()

    def disconnect(self) -> None:
        with self.lock:
            if self.agent:
                self.agent.close()
            self.agent, self.account = None, ""
            forget_token()


def outcome_json(outcome: Outcome) -> dict:
    return {
        "messages": [asdict(r) for r in outcome.responses if r.text or r.table or r.hint],
        "approval": outcome.approval.public() if outcome.approval else None,
    }


def make_handler(app: App):
    allowed_hosts = {f"localhost:{app.port}", f"127.0.0.1:{app.port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "Mailkeep"

        def log_message(self, fmt, *args):
            return

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: dict) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _authorized(self) -> bool:
            return self.headers.get("X-Mailkeep-Token") == app.token

        def do_GET(self):
            if self.headers.get("Host", "") not in allowed_hosts:
                return self._send(403, b"bad host", "text/plain")
            url = urlparse(self.path)
            if url.path == "/":
                page = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            if url.path == "/oauth/callback":
                return self._callback(parse_qs(url.query))
            if url.path.startswith("/api/jobs/"):
                if not self._authorized():
                    return self._json(403, {"error": "forbidden"})
                try:
                    return self._json(200, app.jobs.status(url.path.rsplit("/", 1)[-1]))
                except KeyError:
                    return self._json(404, {"error": "Task not found"})
            if url.path == "/api/status":
                if not self._authorized():
                    return self._json(403, {"error": "forbidden"})
                try:
                    return self._json(200, app.status())
                except Exception as exc:
                    return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return self._send(404, b"not found", "text/plain")

        def _callback(self, query: dict) -> None:
            error = (query.get("error") or [""])[0]
            message, ok = "Gmail connected. You can close this window.", True
            if error:
                from .auth import consent_error_message

                ok = False
                message = consent_error_message(
                    error, (query.get("error_description") or [""])[0][:300]
                )
            else:
                try:
                    credentials = app.login.finish(
                        (query.get("state") or [""])[0], (query.get("code") or [""])[0]
                    )
                    from .gmail import build_client

                    with app.lock:
                        app.attach(build_client(credentials))
                except Exception as exc:
                    ok, message = False, f"Sign-in failed: {exc}"
            app.auth_error = "" if ok else message
            # BUG FIX: errors used to auto-close after 1.5 s, so the fix was
            # unreadable. Close only on success; the main window also shows
            # the error via /api/status.
            closer = "setTimeout(()=>close(),1500)" if ok else ""
            colour = "#5fbf8f" if ok else "#e5675f"
            title = "Connected" if ok else "Google refused the sign-in"
            page = (
                "<!doctype html><meta charset=utf-8><title>Mailkeep</title>"
                "<body style='font:16px system-ui;padding:40px;background:#0f1115;color:#e7e9ee;max-width:640px'>"
                f"<h2 style='color:{colour}'>{title}</h2>"
                f"<p>{html.escape(message)}</p><script>"
                "try{opener&&opener.postMessage('mailkeep-auth',location.origin)}catch(e){}"
                f"{closer}</script>"
            )
            self._send(200 if ok else 403, page.encode(), "text/html; charset=utf-8")

        def do_POST(self):
            if self.headers.get("Host", "") not in allowed_hosts or not self._authorized():
                return self._json(403, {"error": "forbidden"})
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json(400, {"error": "invalid JSON"})
            route = urlparse(self.path).path
            try:
                if route == "/api/client":
                    return self._json(200, {"client_type": save_client_file(str(body.get("json", "")))})
                if route == "/api/client-type":
                    return self._json(200, {"client_type": save_client_type(str(body.get("type", "")))})
                if route == "/api/oauth-check":
                    from .auth import oauth_checklist

                    return self._json(200, oauth_checklist())
                if route == "/api/login":
                    app.auth_error = ""
                    return self._json(200, {"url": app.login.start()})
                if route == "/api/llm-check":
                    return self._json(200, app.llm_health(refresh=True))
                if route == "/api/logout":
                    app.disconnect()
                    return self._json(200, {"ok": True})
                if route == "/api/cancel":
                    return self._json(200, {"cancelled": app.jobs.cancel(str(body.get("job_id", "")))})
                if route in {"/api/chat", "/api/approve"}:
                    if not app.ensure_connected() or app.agent is None:
                        return self._json(409, {"error": "Connect Gmail first."})
                    if route == "/api/chat":
                        return self._json(202, app.jobs.submit(app.agent,
                            str(body.get("text", ""))[:4000], str(body.get("request_id", ""))))
                    else:
                        if app.agent._busy.is_set():
                            return self._json(409, {"error": "Wait for the current turn to finish first."})
                        outcome = app.agent.session.resolve(str(body.get("id", "")), bool(body.get("approve")))
                    return self._json(200, outcome_json(outcome))
            except (ValueError, KeyError) as exc:
                return self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                return self._json(409, {"error": str(exc)})
            except ConfigError as exc:
                return self._json(400, {"error": str(exc)})
            except Exception as exc:
                return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return self._json(404, {"error": "not found"})

    return Handler


def open_window(url: str) -> None:
    """App-style window via Chrome/Edge --app, else a normal browser tab."""
    for name in ("google-chrome", "chrome", "chromium", "chromium-browser", "msedge", "microsoft-edge"):
        exe = shutil.which(name)
        if exe:
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1100,820"])
                return
            except OSError:
                continue
    webbrowser.open(url)


def serve(*, demo: bool, limit: int, port: int, open_browser: bool = True) -> int:
    app = App(demo=demo, limit=limit, port=port)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    except OSError as exc:
        print(f"Port {port} is busy ({exc}). Use --port or MAILKEEP_UI_PORT.")
        return 2
    url = f"http://localhost:{port}/"
    print(f"Mailkeep UI: {url}   (Ctrl+C to stop)")
    print(f"OAuth redirect URI (register only for Web clients): {ui_redirect_uri(port)}")
    if open_browser:
        threading.Timer(0.4, open_window, args=(url,)).start()
    runner = threading.Thread(target=server.serve_forever, name="mailkeep-http", daemon=True)
    runner.start()
    try:
        while runner.is_alive():
            try:
                runner.join(0.5)
            except KeyboardInterrupt:
                # Ctrl+C while the agent is thinking stops that turn only;
                # Ctrl+C while idle quits the app.
                if app.agent is not None and app.agent.cancel():
                    print("\n[mailkeep] Ctrl+C: stopped the agent; conversation aborted. "
                          "Press Ctrl+C again (while idle) to quit.", flush=True)
                    continue
                break
    finally:
        server.shutdown()
        server.server_close()
        if app.agent:
            app.agent.close()
    return 0
