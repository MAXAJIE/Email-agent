# Mailkeep

A draft-only Gmail console assistant. It finds specific kinds of mail, labels
it in reviewed batches, writes drafts, and audits Trash. It never sends mail:
the console has no send call, and the OpenMinion MCP server has
`send_gmail_message` disabled.

```
you> find invoices from last month
you> label 1-3 as Finance
you> draft a reply to 1 saying Thanks, I will review the invoice by Friday.
you> analyze trash
you> restore 1-2
```

## What it does

| Capability | Examples |
| --- | --- |
| Find specific mail | `find invoices`, `find receipts from last month`, `find newsletters`, `find jobs`, `find travel`, `find promotions`, `find unread emails`, `find emails with attachments`, `find emails from amazon`, `find emails in trash` |
| Read | `open 2`, `labels` |
| Label | `label 1-3 as Finance`, `label all newsletters as Mailkeep/Newsletters`, `unlabel 2 from Finance` |
| Draft | `draft a reply to 1 saying ...`, `draft to bob@example.com about Q4 saying ...` |
| Trash analysis | `analyze trash` - size, types, top senders, duplicates, restore vs purge candidates |
| Cleanup | `restore 1-2`, `archive 3`, `mark 1 read`, `delete 4` (moves to Trash) |

Every write prints a preview table and asks `y/N`. Numbered references point at
the last list you saw (find results or the trash report's restore table). Use
`/search <gmail query>` for anything the rule-based parser does not cover.

## Quick start (no credentials)

```
mailkeep.cmd --demo
```

or:

```
.venv\Scripts\python.exe -m mailkeep --demo
```

Demo mode uses an in-memory mailbox with invoices, newsletters, job mail,
travel, receipts and a mixed Trash. Everything except real API calls behaves
exactly like the live app.

## Connect Gmail

The OAuth client is a Google Cloud *Desktop app* client. Its ID and secret are
read from `.openminion/gmail.env` first, then `scripts/gmail.env`, then
`gmail.env` in the project root (all gitignored; template:
`scripts/gmail.env.example`). Set `MAILKEEP_ENV_FILE=/path/to/file` to use any
other location. The same file picks the AI model: `MAILKEEP_LLM_PROVIDER`
(`openrouter`, `gemini`, `portal`, `openai`, `nvidia`, or `custom`),
`MAILKEEP_LLM_MODEL`, `MAILKEEP_LLM_API_KEY` (optional `MAILKEEP_LLM_BASE_URL`,
`MAILKEEP_LLM_TIMEOUT`). For `custom`, the base URL is inferred from a
recognised key (e.g. an NVIDIA `nvapi-` key) or from `OPENAI_BASE_URL`. The
Gmail API must be enabled in the same Google Cloud project.

```
.venv\Scripts\python.exe -m mailkeep login
.venv\Scripts\python.exe -m mailkeep status
```

`login` opens the Google consent page. The token is cached at
`.openminion/gmail/credentials/mailkeep-token.json`, so later runs are silent.
Requested scopes are `gmail.modify` (read, labels, trash) and `gmail.compose`
(drafts). Mailkeep never calls `messages.send`.

Then run the chat:

```
mailkeep.cmd
```

`python -m mailkeep --once "find invoices"` runs one command and exits;
add `--yes` to auto-confirm a write in that mode.

## Safety model

- **Drafts only.** `GmailClient` has no send method. The OpenMinion MCP server
  also runs with `--disabled-tools send_gmail_message`.
- **Review before write.** Label, archive, restore and delete all print the
  exact messages (including IDs) and wait for confirmation. Creating a label is
  part of the confirmed write, so a cancelled action leaves no empty label.
- **Delete is reversible.** Mailkeep moves messages to Trash and never empties
  it. Gmail's own 30-day retention is the only permanent deletion.
- **Local secrets.** OAuth client data and tokens stay under `.openminion/`,
  which is gitignored.

## Layout

```
mailkeep/            console app
  config.py          paths, env file, OAuth client config
  auth.py            consent flow and token refresh
  gmail.py           Gmail API wrapper (search, labels, drafts)
  query.py           natural language -> Gmail query
  intents.py         rule-based intent parser
  classify.py        invoice / newsletter / job / travel heuristics
  trash.py           trash aggregation
  assistant.py       previews, confirmations, actions
  demo.py            in-memory backend for --demo and tests
  console.py         REPL and CLI
tests/               pytest suite (no network)
skills/              OpenMinion skills (SKILL.md + fixtures/ each)
  email-triage/      classify unread mail, label after approval
  email-draft/       one-line instruction -> approved Gmail draft
  email-organize/    attachments -> typed workspace folders (stretch)
  mailkeep/          the console app's workflow for the agent path
scripts/             Gmail MCP launcher and env template
docs/                Gmail integration and plan-verification notes
```

The console does not need an LLM. The same workspace also wires Gmail into
OpenMinion over streamable HTTP MCP for scheduled agent turns; see
`docs/gmail-integration.md`. That path needs a real LLM provider key.

## OpenMinion path

- `scripts/start_gmail_mcp.py` serves 14 Gmail tools on
  `http://127.0.0.1:8001/mcp`.
- `skills/` holds four OpenMinion skills in the runtime's real `recipe` format
  (see below).
- `docs/gmail-integration.md` has the MCP registration, risk overrides and the
  Telegram digest setup.
- `docs/mailkeep-plan-verification.md` records what was verified against the
  OpenMinion source.

### Skills

| Skill | Workflow | Writes | Approval |
| --- | --- | --- | --- |
| `email-triage` | Classify unread mail into important / follow-up / ignorable and label it | Labels only | Batch list, then approve all / subset / cancel |
| `email-draft` | Turn a one-line instruction into a Gmail draft | Draft only, never send | Full draft preview, then confirm |
| `email-organize` | Save attachments into typed workspace folders (stretch) | Local files | Folder + file plan, then confirm |
| `mailkeep` | The console app's workflow, exposed to the agent | Labels, drafts, Trash moves | Per-action `y/N` |

All four share one rule: **read-only work runs freely; every write prints the
exact affected objects first and waits for approval.** The built-in OpenMinion
confirmation prompt shows only a generic argument preview, so the skill's own
printed list is the review surface.

Each skill directory contains `SKILL.md` plus `fixtures/input.json` and
`fixtures/expected.txt` for the harness. Validate and admit them with:

```
.venv\Scripts\python.exe -m openminion skill test skills\email-triage
.venv\Scripts\python.exe -m openminion skill ingest --file skills\email-triage\SKILL.md
.venv\Scripts\python.exe -m openminion skill admit --skill-id email-triage ^
  --version-hash <hash> --expected-active-version-hash none ^
  --target-status verified --reason "mailkeep MVP"
```

Ingest stages a `draft` that is invisible to `skill list` until `skill admit`
runs; that is the operator gate, not a failure.

## Tests

```
.venv\Scripts\python.exe -m pytest tests -q
```

The suite covers query translation, every intent shape, trash aggregation,
preview/confirmation behaviour, draft threading and MIME construction, and
batch label calls. It uses the demo backend, so it needs no credentials.

## Troubleshooting

- **`OAuth client credentials are missing`** - add both values to
  `.openminion/gmail.env` or `scripts/gmail.env`.
- **`access_denied` / `invalid_client`** - confirm the client type is *Desktop
  app* and the Gmail API is enabled in that project.
- **No messages found for a category** - Gmail search is literal. Try
  `/search (invoice OR receipt) newer_than:30d`.
- **Console encoding errors on Windows** - Mailkeep prints ASCII only. If a
  parent process forces cp1252, run it from Windows Terminal or set
  `PYTHONUTF8=1`.
- **MCP tools missing in OpenMinion** - restart the daemon after changing
  `mcp_servers`, then check `openminion tools list --runtime-source inproc`.

## Local chat window (`ui`)

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
python -m mailkeep ui --demo                      # try it with fake mail
python -m mailkeep ui                             # your own Gmail
```

`ui` starts a server on `http://localhost:8765/` (127.0.0.1 only) and opens an
app-style window. First run asks for the OAuth client JSON (Google Cloud
Console -> Credentials -> OAuth client ID -> **Desktop app** -> Download JSON),
then "Sign in with Google". Each person who deploys Mailkeep uses their own
client and consents for their own mailbox; tokens stay in `.openminion/`.

* Desktop clients need no redirect URI registration.
* Web-application clients must list exactly
  `http://localhost:8765/oauth/callback` (shown on the setup screen). Sending
  an unregistered URI is what caused `Error 400: redirect_uri_mismatch`.
* While the Google app is in "Testing", only listed test users can sign in and
  refresh tokens expire after 7 days.

Every write (label, archive, delete, restore, mark read, draft) appears as an
approval card; nothing changes until you press Approve, and the approval is
refused if the matching message IDs changed since the preview.

### OpenMinion agent

With an OpenMinion config at `.openminion/agents.json` whose provider is not
`echo`, chat goes through an `openminion.Agent` that drives one tool,
`mailkeep_command`. The model only translates requests into Mailkeep commands;
writes still stop at the approval card. Without a model, the rule-based
parser answers. Example:

```
python -m openminion --config .openminion/agents.json config init --provider openai
# then set the provider under BOTH "providers" and "system.providers" (see report)
export OPENAI_API_KEY=...
```

`MAILKEEP_AGENT=rules` forces the parser; `MAILKEEP_AGENT_TIMEOUT` (default 180)
bounds one agent turn.
