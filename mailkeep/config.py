"""Project-local configuration for Mailkeep.

Secrets live in `.openminion/gmail.env` (gitignored). Nothing in this module
prints a secret value, and the token cache stays inside the project.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".openminion" / "gmail.env"
# Accepted as a fallback so `scripts/gmail.env` works too (the documented copy
# target). The canonical location stays `.openminion/gmail.env`.
FALLBACK_ENV_FILE = ROOT / "scripts" / "gmail.env"
TOKEN_DIR = ROOT / ".openminion" / "gmail" / "credentials"
TOKEN_FILE = TOKEN_DIR / "mailkeep-token.json"
# Client JSON downloaded from Google Cloud Console. Its top-level key
# ("installed" = Desktop app, "web") decides whether the redirect URI
# must be registered.
CLIENT_SECRET_FILE = ROOT / ".openminion" / "client_secret.json"

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


def _client_file_section() -> tuple[str, dict] | None:
    """Return ("installed"|"web", section) from the downloaded client JSON."""
    path = Path(setting("MAILKEEP_CLIENT_SECRET_FILE") or CLIENT_SECRET_FILE)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{path} is not a valid Google client JSON: {exc}") from exc
    for kind in ("installed", "web"):
        section = data.get(kind)
        if isinstance(section, dict) and section.get("client_id"):
            return kind, section
    raise ConfigError(f"{path} has neither an 'installed' nor a 'web' client.")


def client_type() -> str:
    """'installed' (Desktop app) or 'web'. Env-file clients default to Desktop.

    Google accepts an unregistered loopback redirect only for Desktop clients.
    A Web client must list the exact redirect URI; sending any other URI is
    what produces `Error 400: redirect_uri_mismatch`.
    """
    found = _client_file_section()
    if found:
        return found[0]
    return "web" if setting("GOOGLE_OAUTH_CLIENT_TYPE").lower() == "web" else "installed"


def registered_redirect_uris() -> list[str]:
    found = _client_file_section()
    return [str(u) for u in (found[1].get("redirect_uris") or [])] if found else []


def client_config(redirect: str | None = None) -> dict:
    """google-auth client config whose declared redirect equals the one sent."""
    redirect = redirect or redirect_uri()
    found = _client_file_section()
    if found:
        kind, section = found
        client_id, client_secret = section["client_id"], section.get("client_secret", "")
    else:
        kind = client_type()
        client_id = setting("GOOGLE_OAUTH_CLIENT_ID")
        client_secret = setting("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ConfigError(
            "No Google OAuth client configured. Put the downloaded client JSON at "
            f"{CLIENT_SECRET_FILE} (or paste it in the UI), or set "
            f"GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET in {ENV_FILE}."
        )
    return {
        kind: {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [redirect],
        }
    }


def save_client_file(raw: str) -> str:
    """Validate and store a client JSON pasted into the UI. Returns its type."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ConfigError(f"That is not JSON: {exc}") from exc
    kind = next((k for k in ("installed", "web") if isinstance(data.get(k), dict)), "")
    if not kind or not data[kind].get("client_id") or not data[kind].get("client_secret"):
        raise ConfigError(
            "Expected the JSON downloaded from Google Cloud Console (an 'installed' "
            "or 'web' object with client_id and client_secret)."
        )
    CLIENT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLIENT_SECRET_FILE.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(CLIENT_SECRET_FILE, 0o600)
    except OSError:
        pass
    return kind


def has_client_credentials() -> bool:
    try:
        if _client_file_section():
            return True
    except ConfigError:
        return False
    return bool(
        setting("GOOGLE_OAUTH_CLIENT_ID") and setting("GOOGLE_OAUTH_CLIENT_SECRET")
    )


# Local web UI. Google redirects back to the UI server itself. Desktop
# clients accept this unregistered; Web clients must register it exactly.
DEFAULT_UI_PORT = 8765


def ui_port() -> int:
    raw = setting("MAILKEEP_UI_PORT")
    try:
        port = int(raw) if raw else DEFAULT_UI_PORT
    except ValueError:
        return DEFAULT_UI_PORT
    return port if 1 <= port <= 65535 else DEFAULT_UI_PORT


def ui_redirect_uri(port: int | None = None) -> str:
    return f"http://localhost:{port or ui_port()}/oauth/callback"


def has_token() -> bool:
    return TOKEN_FILE.is_file()
