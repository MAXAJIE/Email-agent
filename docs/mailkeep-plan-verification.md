# Mailkeep plan — verified against OpenMinion source

Verified 2026-10-19 on this machine (Windows, Python 3.13, `openminion` from
`openminion/src`). Every claim below was executed or read in the source; file
references are exact.

## Answers to the plan's open questions (§10)

1. **Gmail MCP server** — `taylorwilsdon/google_workspace_mcp` (MIT). Already
   wired; see `docs/gmail-integration.md`. Running here over streamable HTTP on
   `127.0.0.1:8001` with **14 Gmail tools** (`send_gmail_message` disabled
   server-side).
2. **Provider** — `openminion config init --provider` accepts
   `{echo,openai,anthropic,claude,openrouter,cerebras,groq,ollama,cortensor}`.
   `cortensor` is a first-class provider, which matters for this hackathon.
3. Project name, Discord, submission repo — not verifiable from this workspace.
4. Team size — not verifiable.

## Day-1 checklist results

### Permissions (`/permissions`, four postures) — CONFIRMED

- `docs/getting-started.md:98-110` documents Read only / Ask / Workspace auto /
  Full access; internal modes are `readonly`, `default`, `auto`, `bypass`
  (`cli/presentation/permission_menu.py`, tests
  `tests/cli/interactive/terminal/test_permissions_menu.py`).
- **Default is Ask** — `tests/tools/test_approval_pending.py:113`
  (`test_default_permission_mode_asks_for_each_write`) asserts one approval per
  write.
- Ask "requests approval for non-read effects"; Workspace auto skips prompts
  only for `file.write`/`file.edit` inside the workspace; Full access still
  honors configured blocks. So **Ask is the correct fixed posture** for
  Mailkeep, exactly as the plan argues.
- The plan's ⚠️ item — "is a two-phase preview/confirm needed?" — is answered:
  **no hand-rolled two-phase protocol is required.** The runtime pauses on
  `REQUIRE_CONFIRMATION`, emits a prompt, and replays the exact command with a
  grant on `yes` / `allow_once` / `session`:
  `modules/brain/runner/delegates.py:239-260`, `modules/brain/runner/tick/context.py:79-132`.
- **Important limitation for Mailkeep's "reviewable list" requirement.** The
  chat prompt is built by `confirmation_required_user_message`
  (`modules/brain/loop/tools/confirmation.py:151-195`). It shows tool name +
  risk class + side effects + reversibility + reason, plus an argument preview
  limited to **3 arguments from a fixed whitelist**
  (`_CONFIRMATION_MESSAGE_ARG_KEYS = ("path", "file_path", "command", "cwd",
  "url")`, `confirmation.py:24`; limit `CONFIRMATION_MESSAGE_ARG_LIMIT = 3`,
  `modules/brain/constants.py:73`). Tool-specific `display_lines` rendering
  exists only for `ops.command.run` (`modules/policy/models.py:81-97`).
  **Consequence:** the built-in prompt for `mcp.workspace.modify_gmail_message_labels`
  will not name the message IDs or label IDs. The agent's own batch list (skill
  step 3) is therefore *the* review surface, not a nicety — the plan's §4
  principle 3 is load-bearing.
- Batch approvals are native: when several commands are queued from one batch,
  the prompt adds "This approval also covers N queued commands from the same
  batch" (`confirmation.py:181-186`). Commands marked
  `requires_individual_confirmation` instead demand an exact `yes`/`no`.

### Skills — CONFIRMED with a corrected spec

- Recognized frontmatter keys (26) are enumerated at
  `modules/skill/runtime/parser.py:25-55`: `name, id, status, risk, tools, tags,
  applies_to, inputs, version, verification, rollback, recipe, references,
  description, metadata, license, compatibility, allowed-tools, objective,
  preflight, stop_conditions, safety_notes, idempotency_notes, teaches,
  requires_tools, safe_for_domains, forbidden_claims, evidence_expectations`.
- `name` + `description` alone will parse, so the plan's draft is not *wrong* —
  but OpenMinion's own example
  (`examples/skills/commerce-order-care/SKILL.md`) also uses `id`, `version`,
  `risk`, `tools`, `verification`, and a structured `recipe` block
  (`objective`, `preflight`, `steps[]` with `step_id`/`tool_id`/`instruction`,
  `verification`, `rollback`, `stop_conditions`, `idempotency_notes`,
  `safety_notes`) plus body sections `# Summary`, `# Procedure`,
  `# Verification`, `# Rollback`. That is what the planner and verifier
  actually consume; the Markdown body alone is prose.
- Unknown top-level keys are only warnings
  (`front_matter_unknown_key_warnings`), so a Claude-style frontmatter will not
  hard-fail — it will just be ignored.
- Tooling for the Day-1 check: `openminion skill test <skill_root>` (filesystem
  and bundle conformance), `openminion skill validate`, `openminion skill
  ingest --file <path>`, `openminion skill list/show/debug`.

### Tasks — ALREADY NATIVE (the plan guessed "maybe not available")

- `openminion schedule create --instruction ... --cron ... --tz ...`, plus
  `status/pause/resume/show/run/tick`; `openminion tasks list/show/pause/
  resume/cancel`.
- Verified live: created `gmail-morning-digest`, cron `0 8 * * *`,
  `tz=Asia/Kuala_Lumpur`, stored payload is an `agentTurn` with
  `delivery={"mode":"announce","channel":"last","to":"last"}` — isolated
  scheduled turns announce into the last active channel (Telegram) by default.
- Also available: `task.watch` (polling watch with `alert_condition`), which is
  the natural primitive for the "new important mail" alert idea.

### Delegation — first-class

`openminion agent delegate / delegate-list / delegate-status / delegate-result /
delegate-cancel`, plus the `task.delegate` tool with a validated
`child_permission_mode` (`tests/tools/test_agent_delegation.py`). The stretch
"polish hand-off" experiment is supported by a real surface.

### Memory — CONFIRMED (sophiagraph)

- Dependency `sophiagraph>=0.0.12` in `openminion/pyproject.toml`; module README
  confirms `memory.backend.provider=sophiagraph|none|external`.
- Operator surfaces: `openminion memory blocks list/pin/update/unpin`, and the
  CLI states the full record/candidate/review surface is `memctl`.
- For the plan's "remember the user's corrections": write them through the
  memory tool/`memctl` candidate flow, not by editing stores directly.

### Diagnostics — CONFIRMED

`openminion doctor [--check-turn] [--json]`, `openminion agent logs`,
`openminion agent inspect`, `openminion tools list/schema/run/exposure`,
`openminion status`, `openminion replay`.

## Corrected Gmail tool contract (replaces plan §5)

Every call requires `user_google_email` (even in single-user mode). `*` =
required.

| Capability | Real tool (`mcp.workspace.<name>`) | Parameters | Posture |
| --- | --- | --- | --- |
| Search mail | `search_gmail_messages` | `query*`, `page_size`, `page_token`, `include_headers`, `user_google_email*` | READ_ONLY |
| Read one message | `get_gmail_message_content` | `message_id*`, `body_format`, `format`, `full`, `user_google_email*` | READ_ONLY |
| Read many messages | `get_gmail_messages_content_batch` | `message_ids*` (max 25), `body_format`, `format`, `user_google_email*` | READ_ONLY |
| Read thread | `get_gmail_thread_content` | `thread_id*`, `user_google_email*` | READ_ONLY |
| List labels | `list_gmail_labels` | `prefix`, `compact`, `include_system`, `user_google_email*` | READ_ONLY |
| Add/remove labels | `modify_gmail_message_labels` | `message_id*`, `add_label_ids`, `remove_label_ids`, `user_google_email*` | POWER_USER |
| Batch label change | `batch_modify_gmail_message_labels` | `message_ids*`, `add_label_ids`, `remove_label_ids`, `verify`, `user_google_email*` | POWER_USER |
| Create draft | `draft_gmail_message` | `to`, `cc`, `subject`, `body`, `thread_id`, `attachments`, ..., `user_google_email*` | WRITE_SAFE |
| Download attachment | `get_gmail_attachment_content` | `message_id*`, `attachment_id*`, `attachment_index`, `return_base64`, `user_google_email*` | READ_ONLY |
| ~~Send mail~~ | **not registered** (`--disabled-tools send_gmail_message`) | — | — |

Naming corrections vs the plan: `list_messages` → `search_gmail_messages`
(returns IDs + links only, so triage is always search → batch get),
`get_message` → `get_gmail_message_content`, `modify_labels` →
`modify_gmail_message_labels`, `create_draft` → `draft_gmail_message`,
`list_labels` → `list_gmail_labels`. There is **no** `list_attachments` tool:
attachment metadata arrives with message content, and
`get_gmail_attachment_content` downloads one attachment at a time.

## Product hardening already applied

- `send_gmail_message` is removed **at the server**, not by prompt instruction:
  `scripts/start_gmail_mcp.py` passes `--disabled-tools send_gmail_message`
  (verified: 14 tools, `send present: False`). Drafts remain the boundary.
  Re-enable by deleting that flag if Phase 2 needs approved sending.
- Read tools are classified `READ_ONLY` via explicit `tool_risk_overrides`
  (8 tools), so they need no confirmation; label writes stay `POWER_USER`.
- Credentials are project-local: `.openminion/gmail.env`,
  `.openminion/gmail/credentials` (gitignored).

## New defects found while verifying (see AGENTS.md ledger)

1. **`openminion tools list --verbose` crashes on a cp1252 console** —
   `UnicodeEncodeError: 'charmap' codec can't encode character '\u2713'`.
   The command prints its header and dies; plain `tools list` is unaffected.
2. **JSON CLI output is not UTF-8 on Windows** — `openminion mcp test` (and
   `doctor --json`) emit cp1252 bytes (verified: two `0x97` bytes; a UTF-8
   reader raises). Piping into Python "worked" only because the reader used the
   same legacy locale codec.
3. **Stale-daemon trap** — `openminion tools list` (default
   `--runtime-source auto` → daemon) reported 98 tools and **0 MCP tools**
   because the daemon started before the server was added;
   `--runtime-source inproc` reported 113 tools including all MCP ones. Config
   changes report `"applies": "next_startup"`, but the stale listing gives no
   warning. Restart the daemon after adding/removing an MCP server.

## Suggested plan edits

- §6 Tasks: change ⚠️ to ✅ native (`task.schedule`, `task.watch`, cron).
- §6 Delegation: ⚠️ stretch → supported by `agent delegate` + `child_permission_mode`.
- §5/§0: replace the Claude-style skill assumption with the `recipe`-based
  SKILL.md format and keep the shared `name`/`description` keys.
- §4: keep Ask; add that the built-in confirmation prompt will not enumerate
  Gmail message/label IDs, so the skill must print the batch list itself.
- §7 W1: add "restart the daemon after wiring MCP, and verify with
  `tools list --runtime-source inproc`".
