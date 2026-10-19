"""OAuth for a Desktop Google client, with the token kept inside the project.

The first call opens a browser (or prints the URL when `open_browser=False`).
After consent, refreshes are silent until the refresh token is revoked.
"""

from __future__ import annotations

from pathlib import Path

from .config import (
    TOKEN_DIR,
    TOKEN_FILE,
    AuthRequired,
    client_config,
    has_client_credentials,
    has_token,
    oauth_port,
    redirect_uri,
)


def save_credentials(credentials) -> None:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")


def load_credentials(interactive: bool = True, open_browser: bool = True):
    """Return valid credentials, refreshing or running the consent flow."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    from .config import SCOPES

    credentials = None
    if has_token():
        try:
            credentials = Credentials.from_authorized_user_file(
                str(TOKEN_FILE), list(SCOPES)
            )
        except Exception:
            credentials = None

        if credentials is not None and credentials.expired:
            if credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                except Exception:
                    credentials = None
            else:
                credentials = None

        if credentials is not None and credentials.valid:
            return credentials

    if not interactive:
        raise AuthRequired(
            "No usable Gmail token. Run `python -m mailkeep login` first."
        )

    if not has_client_credentials():
        raise AuthRequired(
            "OAuth client credentials are missing from .openminion/gmail.env "
            "or scripts/gmail.env."
        )

    flow = InstalledAppFlow.from_client_config(client_config(), list(SCOPES))
    port = oauth_port()
    if open_browser:
        print("Opening the Google consent page in your browser...")
    print(f"OAuth redirect URI: {redirect_uri(port)}")
    print(
        "If Google shows 'Error 400: redirect_uri_mismatch', add exactly that "
        "URI to the OAuth client's Authorized redirect URIs."
    )
    try:
        credentials = flow.run_local_server(
            port=port,
            open_browser=open_browser,
            authorization_prompt_message=(
                "Open this URL to authorize Mailkeep:\n{url}\n"
            ),
            success_message="Mailkeep is authorized. You can close this tab.",
        )
    except OSError as exc:
        raise AuthRequired(
            f"Could not bind the OAuth callback port {port} ({exc}). "
            f"Set MAILKEEP_OAUTH_PORT to a free port and register "
            f"http://localhost:<port>/ as an authorized redirect URI."
        ) from exc
    save_credentials(credentials)
    print(f"Token saved to {TOKEN_FILE}")
    return credentials


def token_state() -> dict[str, object]:
    return {
        "client_credentials": has_client_credentials(),
        "token_file": str(TOKEN_FILE),
        "token_present": has_token(),
        "token_bytes": TOKEN_FILE.stat().st_size if has_token() else 0,
    }


def forget_token(path: Path = TOKEN_FILE) -> bool:
    if path.is_file():
        path.unlink()
        return True
    return False
