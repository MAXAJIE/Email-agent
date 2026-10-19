"""Console entry point for Mailkeep."""

from __future__ import annotations

import argparse
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text

from .assistant import HELP_TEXT, Assistant, AssistantError
from .auth import load_credentials, token_state
from .config import AuthRequired, ConfigError, has_client_credentials, redirect_uri
from .demo import DemoGmailClient
from .gmail import GmailClient, build_client
from .intents import parse_intent
from .models import GmailError, Response

BANNER = """Mailkeep - Gmail console assistant
Find mail, label it, audit Trash and write drafts. Nothing is ever sent:
draft creation is the only write that leaves the mailbox unchanged."""


def render(console: Console, response: Response) -> None:
    if response.text:
        console.print(Text(response.text))
    if response.table is not None:
        table = RichTable(
            box=box.SIMPLE_HEAD,
            show_edge=False,
            pad_edge=False,
            header_style="bold cyan",
        )
        for column in response.table.columns:
            table.add_column(column, overflow="fold")
        for row in response.table.rows:
            table.add_row(*row)
        console.print(table)
    if response.hint:
        console.print(Text(response.hint, style="dim"))


def _confirm(question: str) -> bool:
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in {"y", "yes"}


def _chat(console: Console, backend, limit: int, *, yes: bool) -> int:
    assistant = Assistant(
        backend,
        confirm=(lambda _question: True) if yes else _confirm,
        notify=lambda response: render(console, response),
        limit=limit,
    )
    try:
        account = backend.profile_email()
    except Exception:
        account = "(unknown)"
    mode = "demo data" if isinstance(backend, DemoGmailClient) else "Gmail"
    console.print(Panel(Text(BANNER), title="Mailkeep", subtitle=f"{mode} | {account}"))
    console.print(Text("Type 'help' for commands. Ctrl+C clears the line, Ctrl+D exits.", style="dim"))

    while True:
        try:
            text = input("you> ")
        except EOFError:
            console.print("")
            break
        except KeyboardInterrupt:
            console.print("")
            continue
        if not text.strip():
            continue
        if parse_intent(text).kind == "quit":
            break
        try:
            response = assistant.handle(text)
        except (AssistantError, GmailError, ConfigError, AuthRequired) as exc:
            console.print(Text(f"Error: {exc}", style="bold red"))
            continue
        render(console, response)
    return 0


def _once(console: Console, backend, limit: int, text: str, *, yes: bool) -> int:
    assistant = Assistant(
        backend,
        confirm=(lambda _question: True) if yes else (lambda _question: False),
        notify=lambda response: render(console, response),
        limit=limit,
    )
    try:
        render(console, assistant.handle(text))
    except (AssistantError, GmailError, ConfigError, AuthRequired) as exc:
        console.print(Text(f"Error: {exc}", style="bold red"))
        return 2
    return 0


def _status(console: Console) -> int:
    state = token_state()
    console.print(Text(f"OAuth client configured: {state['client_credentials']}"))
    console.print(Text(f"OAuth redirect URI: {redirect_uri()}"))
    console.print(Text(f"Token file: {state['token_file']}"))
    console.print(Text(f"Token present: {state['token_present']}"))
    if not state["client_credentials"]:
        console.print(Text("Add credentials to .openminion/gmail.env.", style="yellow"))
        return 2
    if not state["token_present"]:
        console.print(Text("No token yet. Run: python -m mailkeep login", style="yellow"))
        return 2
    try:
        credentials = load_credentials(interactive=False)
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        client = GmailClient(service)
        console.print(Text(f"Account: {client.profile_email()}"))
        console.print(Text(f"Labels visible: {len(client.labels())}"))
    except Exception as exc:
        console.print(Text(f"Token check failed: {exc}", style="red"))
        return 2
    return 0


def _login(console: Console, *, open_browser: bool) -> int:
    try:
        credentials = load_credentials(interactive=True, open_browser=open_browser)
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        console.print(Text(f"Authorized: {GmailClient(service).profile_email()}"))
    except (ConfigError, AuthRequired) as exc:
        console.print(Text(str(exc), style="bold red"))
        return 2
    except Exception as exc:
        console.print(Text(f"Login failed: {exc}", style="bold red"))
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mailkeep",
        description="Console Gmail assistant: search, label, trash audit, drafts.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="chat",
        choices=["chat", "login", "status"],
        help="chat (default), login, or status",
    )
    parser.add_argument("--demo", action="store_true", help="use in-memory demo mail")
    parser.add_argument("--limit", type=int, default=20, help="max search results")
    parser.add_argument(
        "--no-browser", action="store_true", help="print the consent URL instead of opening a browser"
    )
    parser.add_argument("--once", default="", help="run one command and exit")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="auto-confirm writes for --once (no interactive prompt)",
    )
    args = parser.parse_args(argv)
    console = Console()
    limit = max(1, min(200, args.limit))

    if args.command == "status":
        return _status(console)
    if args.command == "login":
        return _login(console, open_browser=not args.no_browser)

    if args.demo:
        backend = DemoGmailClient()
    else:
        if not has_client_credentials():
            console.print(
                Text(
                    "OAuth client credentials are missing. Add them to "
                    ".openminion/gmail.env (template: scripts/gmail.env.example).",
                    style="bold red",
                )
            )
            return 2
        try:
            backend = build_client()
        except (ConfigError, AuthRequired) as exc:
            console.print(Text(str(exc), style="bold red"))
            console.print(Text("Run: python -m mailkeep login", style="yellow"))
            return 2
        except Exception as exc:
            console.print(Text(f"Could not start Gmail: {exc}", style="bold red"))
            return 2

    if args.once:
        return _once(console, backend, limit, args.once, yes=args.yes)
    try:
        return _chat(console, backend, limit, yes=args.yes)
    except KeyboardInterrupt:
        console.print("")
        return 130


if __name__ == "__main__":
    sys.exit(main())
