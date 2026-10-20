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
# Plain `.env` files are read too (project root first): many users keep their
# provider key there, and before this it was silently ignored.
DOTENV_FILES = (ROOT / ".env", ROOT / ".openminion" / ".env", ROOT / "scripts" / ".env")
# Where people actually drop gmail.env when following the example: the project
# root, or with a hidden ".txt" from Windows Notepad. All are read, in order.
EXTRA_ENV_FILES = (
    ROOT / "gmail.env",
    ROOT / ".openminion" / "gmail.env.txt",
    ROOT / "scripts" / "gmail.env.txt",
    ROOT / "gmail.env.txt",
)
TOKEN_DIR = ROOT / ".openminion" / "gmail" / "credentials"
TOKEN_FILE = TOKEN_DIR / "mailkeep-token.json"
# Client JSON downloaded from Google Cloud Console. Its top-level key
# ("installed" = Desktop app, "web") decides whether the redirect URI
# must be registered.
CLIENT_SECRET_FILE = ROOT / ".openminion" / "client_secret.json"
# Client type chosen in the UI when only ID/secret are in gmail.env.
CLIENT_TYPE_FILE = ROOT / ".openminion" / "client_type"

# `gmail.modify` covers reading, labels, trash and read state.
# `gmail.compose` covers drafts. Mailkeep never calls messages.send().
#
# One consent for Mailkeep AND google_workspace_mcp: this is exactly what
# workspace-mcp requests for `--permissions gmail:drafts` plus the identity
# scopes it uses to find the account. With the old two-scope token
# workspace-mcp found no matching grant and started a second consent on its
# own redirect URI (localhost:8001/oauth2callback). gmail.send is absent.
SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
)
# What Mailkeep itself cannot work without (older two-scope tokens qualify).
REQUIRED_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
)
MCP_GMAIL_PERMISSION = "gmail:drafts"

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
    """Read a simple KEY=VALUE file without touching the process environment.

    Tolerates what real-world Windows/.env files contain: a UTF-8 BOM (Notepad),
    UTF-16, `export KEY=...`, spaces around `=`, quotes, and trailing
    `# comments` on unquoted values.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith("#"):
            # `KEY=   # comment` means empty, not the comment text. Before, the
            # example's `MAILKEEP_LLM_API_KEY=  # or OPENROUTER_API_KEY` line
            # became a bogus key that hid the real one.
            value = ""
        elif value[:1] in {'"', "'"} and value[1:].find(value[0]) != -1:
            value = value[1 : 1 + value[1:].find(value[0])]
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
        if key:
            values[key] = value
    return values


def env_files() -> tuple[Path, ...]:
    """Every file `setting()` reads, in priority order.

    MAILKEEP_ENV_FILE (process environment only) points at any other file and
    is read first, so a launcher or another machine can keep it elsewhere.
    """
    files: list[Path] = []
    custom = os.environ.get("MAILKEEP_ENV_FILE", "").strip()
    if custom:
        files.append(Path(custom).expanduser())
    files += [ENV_FILE, FALLBACK_ENV_FILE, *EXTRA_ENV_FILES, *DOTENV_FILES]
    seen: set[Path] = set()
    unique = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return tuple(unique)


def env_values() -> dict[str, str]:
    """Merged view of all env files (earlier files win). Values are secrets."""
    merged: dict[str, str] = {}
    for path in env_files():
        for key, value in load_env_file(path).items():
            if value and key not in merged:
                merged[key] = value
    return merged


# Values entered in the UI settings gear. They sit on top of the environment
# and .env files (which stay the preset). "Session" values live in memory only;
# "saved" values are written to SETTINGS_FILE (inside the gitignored
# .openminion/ folder, so keys never leave the machine).
SETTINGS_FILE = ROOT / ".openminion" / "mailkeep-settings.json"
_session_overrides: dict[str, str] = {}
_saved_cache: tuple[float, dict[str, str]] = (-1.0, {})


def _saved_overrides() -> dict[str, str]:
    global _saved_cache
    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except OSError:
        return {}
    if mtime != _saved_cache[0]:
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            values = {str(k): str(v) for k, v in (data.get("values") or {}).items() if v}
        except (OSError, ValueError, AttributeError):
            values = {}
        _saved_cache = (mtime, values)
    return _saved_cache[1]


def ui_override(name: str) -> tuple[str, str]:
    """(value, "session"|"saved"|"") set through the UI settings panel."""
    if _session_overrides.get(name):
        return _session_overrides[name], "session"
    value = _saved_overrides().get(name, "")
    return (value, "saved") if value else ("", "")


def set_ui_override(name: str, value: str, *, persist: bool) -> None:
    """Set (or clear, with value="") a UI setting. persist=True writes the file."""
    global _saved_cache
    value = value.strip()
    _session_overrides.pop(name, None)
    saved = dict(_saved_overrides())
    if persist or name in saved:
        if value and persist:
            saved[name] = value
        else:
            saved.pop(name, None)
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps({"values": saved}, indent=2), encoding="utf-8")
        try:
            os.chmod(SETTINGS_FILE, 0o600)
        except OSError:
            pass
        _saved_cache = (-1.0, {})
    if value and not persist:
        _session_overrides[name] = value


def setting(name: str) -> str:
    """UI settings win, then the process environment, then the env files."""
    value, _ = ui_override(name)
    if value:
        return value
    value = os.environ.get(name, "").strip()
    if value:
        return value
    for path in env_files():
        value = load_env_file(path).get(name, "").strip()
        if value:
            return value
    return ""


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
    # A client ID alone does not say whether it is Desktop or Web. The old
    # default ("installed") skipped the redirect check, so a Web client sent
    # an unregistered URI and Google answered `Error 400: redirect_uri_mismatch`.
    # Never guess: stay "unknown" until the user tells us.
    declared = setting("GOOGLE_OAUTH_CLIENT_TYPE").strip().lower()
    if not declared and CLIENT_TYPE_FILE.is_file():
        declared = CLIENT_TYPE_FILE.read_text(encoding="utf-8").strip().lower()
    if declared in {"web", "web application"}:
        return "web"
    if declared in {"installed", "desktop", "desktop app"}:
        return "installed"
    return "unknown"


def save_client_type(kind: str) -> str:
    """Remember 'installed' (Desktop app) or 'web' for env-file clients."""
    kind = {"desktop": "installed"}.get(kind.strip().lower(), kind.strip().lower())
    if kind not in {"installed", "web"}:
        raise ConfigError("Client type must be 'desktop' or 'web'.")
    CLIENT_TYPE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLIENT_TYPE_FILE.write_text(kind, encoding="utf-8")
    return kind


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
        # google-auth accepts either key; "unknown" is stopped in auth.check_redirect.
        kind = "web" if client_type() == "web" else "installed"
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
