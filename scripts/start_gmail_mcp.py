#!/usr/bin/env python3
"""Run the Google Workspace MCP server (Gmail) for OpenMinion.

OpenMinion's stdio MCP transport cannot poll child pipes on Windows
(``selectors.SelectSelector`` raises WinError 10038), so this launcher always
starts the server in streamable-HTTP mode and OpenMinion connects over ``url``.

Usage:
    python scripts/start_gmail_mcp.py            # background-friendly
    python scripts/start_gmail_mcp.py --read-only

Credentials for the Google OAuth client are read from `.openminion/gmail.env`
(see `scripts/gmail.env.example`). The OAuth token cache is kept inside the
project so nothing is written outside the workspace.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".openminion" / "gmail.env"
CREDENTIALS_DIR = ROOT / ".openminion" / "gmail" / "credentials"
DEFAULT_PORT = "8001"


def _load_env_values() -> dict[str, str]:
    """Same files and parser as Mailkeep itself (BOM/UTF-16/comments, every
    location in mailkeep.config.env_files()), so both always agree."""
    sys.path.insert(0, str(ROOT))
    from mailkeep import config

    return config.env_values()


def main(argv: list[str]) -> int:
    uvx = shutil.which("uvx")
    if not uvx:
        print(
            "uvx was not found on PATH. Install uv first: https://docs.astral.sh/uv/",
            file=sys.stderr,
        )
        return 1

    env = dict(os.environ)
    env.update(_load_env_values())
    env.setdefault("WORKSPACE_MCP_PORT", DEFAULT_PORT)
    env.setdefault("WORKSPACE_MCP_CREDENTIALS_DIR", str(CREDENTIALS_DIR))
    # Local http:// development endpoint for the OAuth callback.
    env.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)

    if not env.get("GOOGLE_OAUTH_CLIENT_ID") or not env.get(
        "GOOGLE_OAUTH_CLIENT_SECRET"
    ):
        print(
            f"warning: GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET are unset "
            f"in {ENV_FILE}; the server will start but Gmail calls will fail.",
            file=sys.stderr,
        )

    command = [
        uvx,
        "workspace-mcp",
        "--transport",
        "streamable-http",
        "--single-user",
        "--permissions",
        # Must equal mailkeep.config.MCP_GMAIL_PERMISSION. The old "full"
        # level added gmail.send + settings.basic, which the Mailkeep token
        # never holds, so workspace-mcp asked for a second consent.
        "gmail:drafts",
        # Mailkeep never sends mail: drafts are the product boundary. Removing
        # the tool server-side means the model cannot send even by mistake.
        "--disabled-tools",
        "send_gmail_message",
        *argv,
    ]
    print(
        f"workspace-mcp on http://127.0.0.1:{env['WORKSPACE_MCP_PORT']}/mcp "
        f"(credentials: {env['WORKSPACE_MCP_CREDENTIALS_DIR']})",
        file=sys.stderr,
    )
    try:
        return subprocess.call(command, env=env, cwd=str(ROOT))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
