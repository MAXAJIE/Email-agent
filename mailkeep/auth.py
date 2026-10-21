"""Google OAuth for Mailkeep, with the token kept inside the project.

Two entry points share one token file:

* ``load_credentials`` - console login (``python -m mailkeep login``).
* ``WebLogin`` - consent started from the local UI; Google redirects back to
  the UI server's ``/oauth/callback``.

`Error 400: redirect_uri_mismatch` means the redirect URI sent to Google is
not accepted by the OAuth client: Desktop ("installed") clients accept any
loopback URI, Web clients only registered ones. Both paths check this up
front and name the exact string to register.
"""

from __future__ import annotations

import json
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import (
    REQUIRED_SCOPES,
    SCOPES,
    TOKEN_DIR,
    TOKEN_FILE,
    AuthRequired,
    ConfigError,
    client_config,
    client_type,
    has_client_credentials,
    has_token,
    oauth_port,
    redirect_uri,
    registered_redirect_uris,
    setting,
)


def _write_private(path: Path, text: str) -> None:
    from .config import write_private

    write_private(path, text)


def mcp_token_path(email: str, base: Path = TOKEN_DIR) -> Path:
    """File name google_workspace_mcp's LocalDirectoryCredentialStore looks up."""
    return base / f"{urllib.parse.quote(email, safe='@._-')}.json"


def mcp_token_payload(credentials) -> dict:
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes or SCOPES),
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
    }


def _account_email(credentials) -> str:
    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        return str(service.users().getProfile(userId="me").execute().get("emailAddress", ""))
    except Exception:
        return ""


def save_credentials(credentials, email: str | None = None) -> None:
    """Save the token for Mailkeep and, in workspace-mcp's format, for the MCP
    server (same directory: WORKSPACE_MCP_CREDENTIALS_DIR = TOKEN_DIR), so
    scripts/start_gmail_mcp.py never asks for a second consent."""
    _write_private(TOKEN_FILE, credentials.to_json())
    if email is None:
        email = _account_email(credentials)
    if email:
        _write_private(mcp_token_path(email), json.dumps(mcp_token_payload(credentials), indent=2))


def missing_scopes(granted) -> list[str]:
    granted = set(granted or [])
    return [s for s in REQUIRED_SCOPES if s not in granted]


# Google `error=` values on the callback -> what to do about them.
#
# `Error 403: access_denied` with flowName=GeneralOAuthFlow is NOT a code bug:
# Google refused before Mailkeep ever saw a code. With gmail.modify/compose
# (restricted scopes) on an unverified app, Google only lets through accounts
# listed as Test users of the SAME Cloud project the client ID belongs to.
# Picking another account in a multi-account browser, or adding the test user
# in a different project, produces exactly this error.
CONSENT_ERRORS = {
    "access_denied": (
        "Google returned Error 403: access_denied - it blocked the sign-in before "
        "Mailkeep got anything (or you pressed Cancel). Fix, in this order: "
        "(1) Google Cloud Console -> Google Auth Platform -> Audience, in the SAME "
        "project as your OAuth client (project number {project}): user type "
        "External, Publishing status Testing, and add the exact Gmail address you "
        "sign in with under Test users -> Save. (2) On Google's page choose that "
        "same account (set MAILKEEP_GMAIL_ACCOUNT so Mailkeep pre-selects it). "
        "(3) Google Auth Platform -> Data access: add gmail.modify and "
        "gmail.compose. (4) Workspace (company/school) accounts: the admin must "
        "allow the app. Then sign in again and tick every Gmail checkbox."
    ),
    "admin_policy_enforced": "Your Google Workspace admin blocks this app.",
    "org_internal": (
        "The OAuth app's Audience is 'Internal', so only accounts of that Workspace "
        "organisation can sign in. Switch to External + Testing and add yourself "
        "as a test user."
    ),
}


def client_project_number() -> str:
    """Cloud project number = digits before '-' in the client ID (not secret)."""
    try:
        client_id = next(iter(client_config().values()))["client_id"]
    except Exception:
        return "unknown"
    head = str(client_id).split("-", 1)[0]
    return head if head.isdigit() else "unknown"


def consent_error_message(error: str, description: str = "") -> str:
    template = CONSENT_ERRORS.get(error)
    if template is None:
        text = f"Google did not grant access ({error}). Nothing was connected."
    else:
        text = template.format(project=client_project_number())
    if description:
        text += f" Google said: {description}"
    return text


def login_hint() -> str:
    return setting("MAILKEEP_GMAIL_ACCOUNT").strip()


def oauth_checklist() -> dict[str, object]:
    """Everything needed to diagnose a Google refusal, without any secret."""
    project = client_project_number()
    url = "https://console.cloud.google.com/auth/audience"
    if project != "unknown":
        url += f"?project={project}"
    return {
        "project_number": project,
        "client_type": client_type() if has_client_credentials() else "",
        "login_hint": login_hint(),
        "scopes": list(SCOPES),
        "audience_url": url,
        "steps": [
            "Audience: user type 'External', Publishing status 'Testing'.",
            "Audience -> Test users: add the exact Gmail address you will pick.",
            "Data access: add gmail.modify and gmail.compose (and readonly/labels).",
            "APIs & Services -> Library: Gmail API is Enabled in this project.",
            "On Google's page pick that same account and tick every Gmail box.",
        ],
    }


def check_redirect(uri: str) -> None:
    """Fail here, not on Google's error page, for unregistered Web-client URIs."""
    kind = client_type()
    if kind == "unknown":
        raise ConfigError(
            "Mailkeep cannot tell from the client ID whether your Google OAuth client "
            "is a 'Desktop app' or a 'Web application', and guessing wrong is what "
            "produces 'Error 400: redirect_uri_mismatch'. Pick the type in the setup "
            "screen, paste the downloaded client JSON, or set "
            "GOOGLE_OAUTH_CLIENT_TYPE=desktop|web in .openminion/gmail.env. "
            f"For a Web client, also register exactly: {uri}"
        )
    if kind != "web":
        return
    registered = registered_redirect_uris()
    if not registered:
        # Declared Web via env/UI: Google's list is not visible locally. The UI
        # shows the exact URI to register before sending the user to Google.
        return
    if uri not in registered:
        raise ConfigError(
            "Your OAuth client is a 'Web application' and does not list the redirect "
            f"URI {uri!r} (registered: {', '.join(registered) or 'none'}). Add it under "
            "Google Cloud Console -> Credentials -> your client -> Authorized redirect "
            "URIs, or create a 'Desktop app' client instead (no registration needed)."
        )


def cached_credentials():
    """Valid cached credentials (refreshed if needed), or None."""
    if not has_token():
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        # No scopes argument: keep the scopes actually recorded in the token.
        # Forcing today's SCOPES makes refresh of an older, narrower token
        # fail with invalid_scope (refresh may only request a subset).
        credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE))
    except Exception as exc:
        print(f"mailkeep: cached token unreadable: {exc}", file=sys.stderr)
        return None
    if credentials.expired:
        if not credentials.refresh_token:
            return None
        try:
            credentials.refresh(Request())
        except Exception as exc:
            print(f"mailkeep: token refresh failed, sign in again: {exc}", file=sys.stderr)
            return None
        save_credentials(credentials, "")  # persist refresh; MCP refreshes its own copy
    return credentials if credentials.valid else None


def load_credentials(interactive: bool = True, open_browser: bool = True):
    """Return valid credentials, refreshing or running the console consent flow."""
    credentials = cached_credentials()
    if credentials is not None:
        return credentials
    if not interactive:
        raise AuthRequired("No usable Gmail token. Run `python -m mailkeep ui` or `login`.")
    if not has_client_credentials():
        raise AuthRequired(
            "OAuth client is missing (.openminion/client_secret.json or .openminion/gmail.env)."
        )

    from google_auth_oauthlib.flow import InstalledAppFlow

    # Desktop clients take any loopback port, so let the OS pick a free one
    # (the old pinned 8080 collides with dev servers). Web clients need the
    # pinned, registered port.
    pinned = client_type() == "web" or bool(setting("MAILKEEP_OAUTH_PORT"))
    port = oauth_port() if pinned else 0
    if pinned:
        check_redirect(redirect_uri(port))
    flow = InstalledAppFlow.from_client_config(client_config(), list(SCOPES))
    if open_browser:
        print("Opening the Google consent page in your browser...")
    try:
        credentials = flow.run_local_server(
            port=port,
            open_browser=open_browser,
            authorization_prompt_message="Open this URL to authorize Mailkeep:\n{url}\n",
            success_message="Mailkeep is authorized. You can close this tab.",
            prompt="select_account consent",
            **({"login_hint": login_hint()} if login_hint() else {}),
        )
    except OSError as exc:
        raise AuthRequired(
            f"Could not bind OAuth callback port {port} ({exc}). Set MAILKEEP_OAUTH_PORT."
        ) from exc
    except Exception as exc:
        # oauthlib turns ?error=access_denied into an opaque exception.
        if "access_denied" in str(exc) or "AccessDenied" in type(exc).__name__:
            raise AuthRequired(consent_error_message("access_denied")) from exc
        raise
    save_credentials(credentials)
    print(f"Token saved to {TOKEN_FILE}")
    return credentials


class WebLogin:
    """Consent flow driven by the local UI, keyed by an unguessable state."""

    TTL_SECONDS = 600

    def __init__(self, redirect: str):
        self.redirect = redirect
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[object, float]] = {}

    def start(self) -> str:
        from google_auth_oauthlib.flow import Flow

        check_redirect(self.redirect)
        flow = Flow.from_client_config(
            client_config(self.redirect), scopes=list(SCOPES), redirect_uri=self.redirect
        )
        state = secrets.token_urlsafe(24)
        extra = {"login_hint": login_hint()} if login_hint() else {}
        # select_account forces the chooser, so a multi-account browser cannot
        # silently pick a non-test-user account (usual cause of 403 access_denied).
        url, _ = flow.authorization_url(
            access_type="offline",
            prompt="select_account consent",
            include_granted_scopes="true",
            state=state,
            **extra,
        )
        with self._lock:
            now = time.monotonic()
            self._pending = {
                k: v for k, v in self._pending.items() if now - v[1] < self.TTL_SECONDS
            }
            self._pending[state] = (flow, now)
        return url

    def finish(self, state: str, code: str):
        with self._lock:
            entry = self._pending.pop(state, None)
        if entry is None or time.monotonic() - entry[1] > self.TTL_SECONDS:
            raise AuthRequired("This sign-in link expired or was not started here. Try again.")
        flow = entry[0]
        # code= (not the callback URL) avoids oauthlib's https-only check on
        # the http://localhost callback; PKCE verifier stays on the flow.
        flow.fetch_token(code=code)
        lacking = missing_scopes(flow.credentials.scopes)
        if lacking:
            # Gmail boxes unticked on Google's page: signed in, but no access.
            raise AuthRequired(
                "Signed in, but Gmail access was not granted ("
                + ", ".join(s.rsplit("/", 1)[-1] for s in lacking)
                + "). Sign in again and tick every checkbox on Google's page."
            )
        save_credentials(flow.credentials)
        return flow.credentials


def token_state() -> dict[str, object]:
    return {
        "client_credentials": has_client_credentials(),
        "token_file": str(TOKEN_FILE),
        "token_present": has_token(),
        "token_bytes": TOKEN_FILE.stat().st_size if has_token() else 0,
    }


def forget_token(path: Path = TOKEN_FILE) -> bool:
    """Revoke the grant at Google (best effort) and delete the local token."""
    if not path.is_file():
        return False
    try:
        token = json.loads(path.read_text(encoding="utf-8")).get("refresh_token")
        if token:
            data = urllib.parse.urlencode({"token": token}).encode()
            urllib.request.urlopen("https://oauth2.googleapis.com/revoke", data=data, timeout=10).close()
    except Exception:
        pass
    path.unlink()
    if path == TOKEN_FILE:
        for extra in TOKEN_DIR.glob("*@*.json"):  # workspace-mcp copies
            extra.unlink()
    return True
