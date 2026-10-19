"""Project-local configuration for Mailkeep.

Secrets live in `.openminion/gmail.env` (gitignored). Nothing in this module
prints a secret value, and the token cache stays inside the project.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".openminion" / "gmail.env"
# Accepted as a fallback so `scripts/gmail.env` works too (the documented copy
# target). The canonical location stays `.openminion/gmail.env`.
FALLBACK_ENV_FILE = ROOT / "scripts" / "gmail.env"
TOKEN_DIR = ROOT / ".openminion" / "gmail" / "credentials"
TOKEN_FILE = TOKEN_DIR / "mailkeep-token.json"

# `gmail.modify` covers reading, labels, trash and read state.
# `gmail.compose` covers drafts. Mailkeep never calls messages.send().
SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
)

DEFAULT_LABEL_PREFIX = "Mailkeep"

# Fixed loopback port for the OAuth consent callback.
#
# `run_local_server(port=0)` picks a random port, which makes the redirect URI
# unpredictable. A Desktop-app client tolerates any loopback port, but a Web
# client must have the exact URI registered, and a random port can never be
# registered. Pinning the port makes the redirect URI deterministic and
# therefore registerable. Override with MAILKEEP_OAUTH_PORT if 8080 is taken.
DEFAULT_OAUTH_PORT = 8080


def oauth_port() -> int:
    """Loopback port used for the consent callback."""
    raw = setting("MAILKEEP_OAUTH_PORT").strip()
    if not raw:
        return DEFAULT_OAUTH_PORT
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_OAUTH_PORT
    return port if 1 <= port <= 65535 else DEFAULT_OAUTH_PORT


def redirect_uri(port: int | None = None) -> str:
    """The exact redirect URI sent to Google, including the trailing slash."""
    return f"http://localhost:{port or oauth_port()}/"


class ConfigError(RuntimeError):
    """Configuration is missing or unusable."""


class AuthRequired(RuntimeError):
    """No usable OAuth token; an interactive login is required."""


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Read a simple KEY=VALUE file without touching the process environment."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def setting(name: str) -> str:
    """Process environment wins, then the project env files."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    value = load_env_file().get(name, "").strip()
    if value:
        return value
    return load_env_file(FALLBACK_ENV_FILE).get(name, "").strip()


def client_config() -> dict:
    """Return a google-auth client config dict for the OAuth client.

    The declared redirect URI must match what `run_local_server` actually
    sends, otherwise Google rejects the request with
    `Error 400: redirect_uri_mismatch`.
    """
    client_id = setting("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = setting("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ConfigError(
            "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET are missing. "
            f"Add them to {ENV_FILE} or {FALLBACK_ENV_FILE} "
            "(template: scripts/gmail.env.example)."
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [redirect_uri()],
        }
    }


def has_client_credentials() -> bool:
    return bool(
        setting("GOOGLE_OAUTH_CLIENT_ID") and setting("GOOGLE_OAUTH_CLIENT_SECRET")
    )


def has_token() -> bool:
    return TOKEN_FILE.is_file()
