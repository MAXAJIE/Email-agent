"""OpenMinion agent layer for Mailkeep.

The model never touches Gmail directly. It gets one decorated tool,
``mailkeep_command``, which runs the deterministic Mailkeep command grammar
through ``Session``. Reads return text; writes come back as AWAITING_APPROVAL
and run only when the user presses Approve in the UI (no model involved).

Without OpenMinion, or with the ``echo`` provider (no model, never calls
tools), the rule-based parser answers instead.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from .assistant import HELP_TEXT
from .config import ROOT, setting
from .models import Response
from .session import Outcome, Session

OPENMINION_DIR = ROOT / ".openminion"

INSTRUCTIONS = f"""You are Mailkeep, a careful Gmail assistant running on the user's own machine.
You act ONLY through the `mailkeep_command` tool, which accepts exactly the
command grammar below. Translate the user's request into one or more commands.

{HELP_TEXT}

Exact command shapes (copy them; do not invent other syntax):
  find invoices            find emails from amazon      open 2
  label 1-2 as Finance     unlabel 3 from Finance       archive 1,3
  mark 2 read              delete 4                     restore 1-2
  draft a reply to 1 saying Thanks, received.
Rows are referenced ONLY by the "#" number of the most recent list, never by
message ID or subject. If the user refers to rows that are not in the most
recent list, run the matching `find` first, then use its numbers.

Rules:
- Use as few tool calls as possible (usually one or two).
- Reads (find, open, labels, analyze trash) run immediately.
- Writes (label, unlabel, archive, restore, delete, mark read, draft) return
  AWAITING_APPROVAL. Then STOP calling tools and tell the user in one sentence
  to review the card and press Approve. Never claim a write happened.
- Mailkeep cannot send email. Drafts are the furthest it goes.
- Be brief. The UI already shows the tool's tables; do not repeat them.
"""

# OpenMinion @tool handlers get no per-call context (see report), so the
# session a call belongs to is published here for the duration of one turn.
# The SDK already serialises decorated-tool turns on a global registry lock;
# this lock makes that explicit so the global is safe.
_LEAK_MARKERS = ("[CONSTRAINTS & POLICY]", "[BUDGET TELEMETRY]", "budget_envelope_status")
_TURN_LOCK = threading.Lock()


def trace_enabled() -> bool:
    return setting("MAILKEEP_TRACE").strip().lower() not in {"0", "off", "false", "no"}


def trace(kind: str, text: object) -> None:
    """Print the agent's thinking process to the console (MAILKEEP_TRACE=0 hides it)."""
    if not trace_enabled():
        return
    body = str(text).strip().replace("\r", "")
    if len(body) > 1200:
        body = body[:1200] + " ..."
    pad = "\n" + " " * 21
    print(f"[mailkeep] {kind:<9}  " + pad.join(body.splitlines() or [""]), file=sys.stderr, flush=True)


def _event_text(event: object) -> tuple[str, str]:
    """Best-effort (kind, text) for an OpenMinion progress event (shape is undocumented)."""
    data = event
    if not isinstance(data, dict):
        data = getattr(event, "__dict__", None) or {"text": event}
    kind = str(data.get("type") or data.get("kind") or data.get("event") or "think")
    for field in ("reasoning", "thinking", "delta", "text", "content", "message", "status"):
        value = data.get(field)
        if value:
            return kind, value if isinstance(value, str) else json.dumps(value, default=str)
    return kind, json.dumps(data, default=str)[:400]
_ACTIVE: dict[str, object] = {"session": None, "outcomes": None}


def _build_tool():
    from openminion import tool

    @tool(name="mailkeep_command", idempotent=False)
    def mailkeep_command(command: str) -> str:
        """Run one Mailkeep command (grammar in your instructions) and return its result."""
        session = _ACTIVE["session"]
        outcomes = _ACTIVE["outcomes"]
        if not isinstance(session, Session) or not isinstance(outcomes, list):
            return "ERROR: no active mailbox session."
        calls = _ACTIVE.setdefault("calls", 0) + 1  # type: ignore[operator]
        _ACTIVE["calls"] = calls
        if calls > 4:
            return "STOP: too many commands this turn. Answer the user now."
        if session.approval is not None:
            return "AWAITING_APPROVAL: a previous write is still waiting for the user."
        trace("tool call", f"mailkeep_command({command!r})")
        try:
            outcome = session.run_command(command)
        except Exception as exc:  # user-facing errors become tool text
            trace("tool err", exc)
            return f"ERROR: {exc}"
        outcomes.append(outcome)
        lines = []
        for response in outcome.responses:
            if response.text:
                lines.append(response.text)
            if response.table:
                lines.append(f"[table shown to user, {len(response.table.rows)} row(s)]")
                lines += [" | ".join(row) for row in response.table.rows[:25]]
        if outcome.approval:
            lines.append(f"AWAITING_APPROVAL: {outcome.approval.question}")
        result = "\n".join(lines) or "(no output)"
        trace("tool out", result)
        return result

    return mailkeep_command


def config_path() -> Path:
    """MAILKEEP_LLM_PROVIDER (portal/gemini/openrouter) wins: it generates a
    known-good OpenMinion config. Otherwise use the user's own file."""
    from . import llm

    try:
        preset = llm.selected()
    except ValueError:
        preset = None
    if preset is not None:
        llm.export_key(preset)
        return llm.write_config(preset)
    return Path(setting("MAILKEEP_OPENMINION_CONFIG") or OPENMINION_DIR / "agents.json")


def configured_provider() -> str:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    agents = data.get("agents") or {}
    profile = agents.get("openminion") or next(iter(agents.values()), {})
    return str(profile.get("provider") or "")


def resolve_mode() -> tuple[str, str]:
    """('openminion' | 'rules', human-readable reason)."""
    if setting("MAILKEEP_AGENT").lower() == "rules":
        return "rules", "MAILKEEP_AGENT=rules"
    try:
        import openminion  # noqa: F401
    except Exception as exc:
        return "rules", f"openminion cannot be imported ({type(exc).__name__}: {exc}); run pip install -r requirements.txt"
    from . import llm

    try:
        preset = llm.selected()
    except ValueError as exc:
        return "rules", str(exc)
    if preset is not None:
        if not llm.export_key(preset):
            return "rules", f"{preset.label} selected but no API key (MAILKEEP_LLM_API_KEY or {preset.key_env})"
        return "openminion", f"{preset.label} ({preset.model}) via OpenMinion"
    provider = configured_provider()
    if not provider:
        return "rules", f"no OpenMinion config at {config_path()}"
    if provider == "echo":
        return "rules", "OpenMinion provider is 'echo' (no model, no tool calls)"
    # The user's own agents.json names a provider (e.g. openrouter). OpenMinion
    # only reads its key from the process environment, so copy it there from
    # the env files too; before, a key in .env never reached OpenMinion.
    known = llm.by_name(provider)
    if known is not None and not llm.export_key(known):
        return "rules", (
            f"OpenMinion provider '{provider}' needs {known.key_env}, which is not set in the "
            "environment or in .env / .openminion/gmail.env / scripts/gmail.env"
        )
    return "openminion", f"OpenMinion provider '{provider}'"


class MailkeepAgent:
    """Routes chat text to the OpenMinion agent or the rule-based parser."""

    def __init__(self, session: Session, *, session_id: str | None = None):
        self.session = session
        # Unique per launch: replayed replies are keyed by session_id and
        # persist on disk across restarts (OM-01).
        self.session_id = session_id or f"mailkeep-{uuid.uuid4().hex[:12]}"
        self.mode, self.reason = resolve_mode()
        from . import llm

        for line in llm.describe():
            print(f"[mailkeep] {line}", file=sys.stderr, flush=True)
        print(f"[mailkeep] chat mode: {self.mode} - {self.reason}", file=sys.stderr, flush=True)
        self._agent = None
        self._cancel = threading.Event()
        self._busy = threading.Event()
        self._turn = 0
        self._history: list[tuple[str, str]] = []

    def _ensure_agent(self):
        if self._agent is None:
            os.environ.setdefault("OPENMINION_HOME", str(OPENMINION_DIR))
            os.environ.setdefault("OPENMINION_DATA_ROOT", str(OPENMINION_DIR / "data"))
            from openminion import Agent

            self._agent = Agent(
                name="mailkeep",
                instructions=INSTRUCTIONS,
                tools=[_build_tool()],
                config_path=str(config_path()),
                session_id=self.session_id,
            )
        return self._agent

    def chat(self, text: str) -> Outcome:
        # Slash commands and answers to a pending question ("what should the
        # draft say?") go straight to the deterministic parser.
        direct = text.strip().startswith("/") or self.session.assistant.pending is not None
        if self.mode != "openminion" or direct:
            return self.session.run_command(text)
        return self._agent_turn(text)

    def _agent_turn(self, text: str) -> Outcome:
        from openminion import ProviderError, TurnTimeoutError

        outcomes: list[Outcome] = []
        reply = ""
        with _TURN_LOCK:
            _ACTIVE["session"], _ACTIVE["outcomes"], _ACTIVE["calls"] = self.session, outcomes, 0
            try:
                # WORKAROUND (see report, OM-01): openminion 0.1.7 replays the
                # first reply of a session for every later turn with the same
                # session_id. Use a fresh session per turn and carry a short
                # transcript ourselves; numbered refs live in Session anyway.
                self._turn += 1
                started = time.monotonic()
                trace("user", text)

                def on_delta(event: object) -> None:
                    try:
                        kind, body = _event_text(event)
                        trace(kind[:9], body)
                    except Exception:  # tracing must never break a turn
                        pass

                agent = self._ensure_agent()
                box: dict[str, object] = {}

                def work() -> None:
                    try:
                        box["result"] = agent.run_stream(
                            self._with_history(text),
                            on_delta=on_delta,
                            session_id=f"{self.session_id}-{self._turn}",
                            timeout_seconds=float(setting("MAILKEEP_AGENT_TIMEOUT") or 180),
                            # Only our own tool may run; it gates writes itself.
                            approval_callback=lambda name, _args, _id: (
                                name == "mailkeep_command" and not self._cancel.is_set()
                            ),
                        )
                    except BaseException as exc:  # re-raised on the caller's thread
                        box["error"] = exc

                self._cancel.clear()
                self._busy.set()
                worker = threading.Thread(target=work, name="mailkeep-turn", daemon=True)
                worker.start()
                try:
                    # Short waits keep Ctrl+C / cancel() responsive (also on Windows).
                    while worker.is_alive() and not self._cancel.is_set():
                        worker.join(0.2)
                except KeyboardInterrupt:
                    self._cancel.set()
                finally:
                    self._busy.clear()
                if self._cancel.is_set():
                    trace("abort", "stopped by user (Ctrl+C); conversation reset")
                    self._abandon(agent)
                    self._history = []
                    return Outcome([Response("Stopped. That conversation was aborted.")],
                                   self.session.approval)
                if "error" in box:
                    raise box["error"]  # type: ignore[misc]
                result = box["result"]
                reply = result.text.strip()
                trace("reply", f"{reply}\n({time.monotonic() - started:.1f}s, stats={result.stats})")
                # The runtime prefixes replies with the agent profile name.
                if reply.lower().startswith("openminion:"):
                    reply = reply.split(":", 1)[1].strip()
                # OM-05: the runtime sometimes returns its own prompt
                # scaffolding as the reply body. Never show that to the user.
                if any(tag in reply for tag in _LEAK_MARKERS):
                    reply = (
                        "Please review the request above and press Approve or Cancel."
                        if self.session.approval else ""
                    )
            except (ProviderError, TurnTimeoutError) as exc:
                why = (f"Model provider failed ({exc.code}): {exc.message}"
                       if isinstance(exc, ProviderError) else f"Agent turn timed out ({exc})")
                trace("error", why)
                outcomes.append(Outcome([Response(
                    f"{why}. Answered with the rule-based parser instead."
                )]))
                if self.session.approval is None:
                    outcomes.append(self.session.run_command(text))
            finally:
                _ACTIVE["session"], _ACTIVE["outcomes"] = None, None
        responses = [r for o in outcomes for r in o.responses]
        self._history = (self._history + [(text, reply[:400])])[-3:]
        if reply:
            responses.append(Response(reply))
        return Outcome(responses, self.session.approval)

    def cancel(self) -> bool:
        """Abort the turn in progress. True if one was running."""
        if not self._busy.is_set():
            return False
        self._cancel.set()
        return True

    def _abandon(self, agent) -> None:
        """Drop the agent whose turn was aborted; a fresh one serves the next turn.
        The orphaned run can no longer touch the mailbox: _ACTIVE is cleared and
        the approval callback refuses tools once cancelled."""
        if self._agent is agent:
            self._agent = None

        def close() -> None:
            try:
                agent.close()
            except Exception:
                pass

        threading.Thread(target=close, daemon=True).start()

    def _with_history(self, text: str) -> str:
        if not self._history:
            return text
        lines = ["Earlier in this conversation:"]
        for user, bot in self._history:
            lines.append(f"User: {user}")
            if bot:
                lines.append(f"You: {bot}")
        return "\n".join(lines) + f"\n\nCurrent request: {text}"

    def close(self) -> None:
        if self._agent is not None:
            self._agent.close()
            self._agent = None
