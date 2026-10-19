# Gmail integration (Google Workspace MCP)

How this workspace connects OpenMinion to Gmail through
[taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp),
and how the morning digest is delivered to a phone.

## Why HTTP and not stdio

OpenMinion's stdio MCP transport is unusable on Windows today:

- `src/openminion/tools/mcp/transport.py` registers the child process's **pipes**
  with `selectors.DefaultSelector()` (`transport.py:54,110,496`).
- Windows `select` only accepts sockets, so the first read fails with
  `OSError: [WinError 10038] An operation was attempted on something that is not a socket`.
- Measured on this machine: `pytest tests/mcp` → **32 failed, 3 errors, 211 passed**
  (67 occurrences of WinError 10038).

Streamable HTTP avoids the pipe path entirely, so the server runs separately and
OpenMinion connects by URL.

## Prerequisites (one-time, needs your accounts)

1. **Google Cloud project**
   - Enable the Gmail API.
   - Create an OAuth client of type **Desktop app**.
   - Copy the client ID and client secret into `.openminion/gmail.env`
     (template: `scripts/gmail.env.example`).
2. **LLM provider** — `agents.json` still uses `provider: "echo"` with
   `demo_mode: true`. Set a real provider and API key, otherwise scheduled
   turns cannot call tools.
3. **Telegram** — gives you phone push (see below).

## Start the server

```bash
python scripts/start_gmail_mcp.py
```

The launcher pins:

- transport `streamable-http` on `http://127.0.0.1:8001/mcp`
- `--single-user --permissions gmail:full` with
  `--disabled-tools send_gmail_message` (**14** Gmail tools; sending is removed
  at the server so Mailkeep's draft-only boundary cannot be talked around)
- `WORKSPACE_MCP_CREDENTIALS_DIR` → `.openminion/gmail/credentials`
  (keeps OAuth tokens inside this project)
- OAuth client credentials from `.openminion/gmail.env`

First Gmail call opens a browser consent window; tokens are cached in the
credentials directory, so later calls are silent.

## Register with OpenMinion

Already done in `.openminion/agents.json`:

```bash
openminion mcp add workspace --url http://127.0.0.1:8001/mcp
openminion mcp test          # discovery: 14 tools, protocol 2026-07-28
```

Runtime tool names are namespaced: `mcp.workspace.<tool>`.

## Gmail tool surface

| Tier | Tools |
| --- | --- |
| core | `search_gmail_messages`, `get_gmail_message_content`, `get_gmail_messages_content_batch`, `start_google_auth` (`send_gmail_message` is disabled here) |
| extended | `get_gmail_thread_content`, `get_gmail_attachment_content`, `modify_gmail_message_labels`, `list_gmail_labels`, `list_gmail_filters`, `manage_gmail_label`, `manage_gmail_filter`, `draft_gmail_message` |
| complete | `get_gmail_threads_content_batch`, `batch_modify_gmail_message_labels` |

Every tool requires `user_google_email` in its arguments, even in single-user
mode. Include it in prompts or the call fails schema validation.

## Risk posture

OpenMinion does not trust MCP annotations unless the server is `trusted`, and
even then `openWorldHint: true` downgrades every read tool to `WRITE_SAFE`
(`tools/mcp/risk.py`). Every Gmail tool reports `WRITE_SAFE` out of the box
(15/15 before `send_gmail_message` was disabled), which would force a
confirmation on every search — fatal for an unattended digest.

`.openminion/agents.json` therefore sets explicit `tool_risk_overrides`:

| Tools | min_scope | dangerous |
| --- | --- | --- |
| search / get / list | `READ_ONLY` | false |
| send / draft / auth | `WRITE_SAFE` | false |
| manage label / filter / modify labels | `POWER_USER` | true |

Verify with `openminion mcp test` and inspect each tool's `posture`.

## Morning digest to phone

Task `gmail-morning-digest` (id `126e1a712530496caa6c2ca0d320d3e9`) runs at
08:00 `Asia/Kuala_Lumpur`, read-only, and asks the agent to search
`is:unread newer_than:1d` then batch-fetch content and reply with a short
digest. It sends nothing and changes nothing.

Isolated scheduled tasks default to `delivery = {mode: announce, channel: last,
to: last}` (`modules/session/storage/cron_store.py:164`), so once Telegram is
paired the digest is announced into that chat.

Telegram setup (token comes from @BotFather; keep it in an env var, not in git):

```bash
openminion channel telegram setup --bot-token-ref env:TELEGRAM_BOT_TOKEN
openminion channel telegram identify      # message the bot, then pair
openminion channel telegram pair
openminion channel telegram status
```

Note: the Telegram control-plane state lives in `~/.controlplane/` (outside this
workspace), so run these commands yourself.

One-off push from a script:

```bash
openminion message send --channel telegram --target <chat_id> --message "..."
```

## Verified today

- `openminion mcp test`: `ok: true`, 14 tools (send disabled), server
  `google_workspace 4.1.1`.
- `openminion tools run mcp.workspace.search_gmail_messages`: reaches the MCP
  handler, reports `tool_min_scope: READ_ONLY`, and fails only at
  `OAuth client credentials not found` — i.e. wiring is correct, credentials
  are the remaining step.
- `openminion schedule create` now works on Windows after adding the `tzdata`
  dependency (previously `unknown timezone: UTC`).

## Known gaps in OpenMinion

- `openminion mcp add` has no `--env`, so server env vars cannot be set from the
  CLI; they live in `.openminion/agents.json` or the launcher.
- The risk resolver treats `openWorldHint` as a write signal and ignores
  `readOnlyHint` for untrusted servers, so read-only annotations from reputable
  MCP servers are lost.
- stdio MCP is broken on Windows (see above) and stdio child processes inherit
  an empty environment unless `stdio_sandbox.inherit_env_allowlist` is set
  (`transport.py:150`).
