# Mailkeep: fixes, local chat UI, and OpenMinion findings

Base: `MAXAJIE/Email-agent` @ `9ad93c5` · SDK: `openminion 0.1.7` (PyPI) · Python 3.13 · checked 2026-10-19.

Patch: `mailkeep.patch` (16 files). Apply it from the repo root:

```bash
git apply --check mailkeep.patch && git apply mailkeep.patch
git rm -r --cached .tmp-sdk .idea        # these are tracked now; .gitignore alone won't untrack them
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q             # 53 passed
.venv/bin/python -m mailkeep ui --demo    # chat window with fake mail
.venv/bin/python -m mailkeep ui           # your own Gmail
```

The README stops being UTF-16, so the patch includes a git binary hunk for it. Plain `patch -p1` can't apply that hunk. Use `git apply`.

---

## 1. What changed

| Area | Change |
|---|---|
| Google sign-in | Fixes the `redirect_uri_mismatch` in your screenshot. See §2. |
| Local chat window | `python -m mailkeep ui` starts a stdlib HTTP server on 127.0.0.1:8765 and opens a Chrome/Edge `--app` window, or falls back to a browser tab. You don't need Node or a hosted URL. |
| Setup in the UI | Paste the client JSON → "Sign in with Google" popup → consent → chat. "Disconnect" revokes the token at Google and deletes the local copy. |
| Approvals | Every write (label/unlabel/archive/delete/restore/mark read/draft) shows up as an Approve/Cancel card. On Approve, the command is replayed from a snapshot taken before it ran. The write goes ahead only if the message IDs match the preview the user saw. |
| OpenMinion agent | `mailkeep/agent.py`: an `openminion.Agent` with one `@tool`, `mailkeep_command`. The model only turns a request into the existing command grammar. It can't write to the mailbox, because writes stop at the approval card. Without a model, or with `echo`, the rule-based parser answers. |
| Local server safety | Only answers on 127.0.0.1. Requests with any other Host header are rejected (blocks DNS-rebinding attacks). Each launch embeds a token in the page, and every API call must send it (blocks forged requests from other sites). The OAuth code is never logged. Mail text is shown as plain text, never as HTML. |
| Tests | Added `test_session.py`, `test_oauth_config.py`, `test_web.py` (11 new). The full suite passes: 53. |
| Hygiene | README converted UTF-16 → UTF-8. `.tmp-sdk/` and `.idea/` added to `.gitignore`. `tzdata` added for Windows. Added a `mailkeep.sh` launcher. |

### What I verified, and what I didn't
- **Verified:** all 53 tests pass. In the UI I ran `find invoices` → `label 1-2 as Finance` → Approve on the demo mailbox, and the label was created. One live OpenMinion turn with a real model worked end to end (find → label → approval card → Approve → label exists).
- **Not verified:** Google consent against a real Google account. I have no OAuth client for your project. The code follows Google's loopback flow, and the fail-early check for Web clients is unit-tested.
- **Not verified:** stability across repeated live model runs. One later run hit the provider's rate limit. The app fell back to the rule-based parser as intended.

---

## 2. Root cause of `Error 400: redirect_uri_mismatch`

Three things combined to cause it:
1. `config.client_config()` always declared the client as `"installed"` (Desktop), whatever the real client type was. If the console client is a **Web application**, Google only accepts registered URIs, and `http://localhost:8080/` usually isn't one of them. Even `http://localhost:8080` (no trailing slash) counts as a different string.
2. The port was pinned to 8080. That's the most common dev-server port, so it often collides with something else.
3. Nothing checked the URI before sending the user to Google, so the first sign of a problem was Google's error page.

The fix:
- Mailkeep now reads the downloaded `client_secret.json` and uses its real type.
- For Desktop clients, the console login picks a random free port (allowed, no registration needed).
- The UI redirects to `http://localhost:8765/oauth/callback`. For Web clients it checks that URI against the registered list first, and gives a clear error naming the exact string to add.
- The setup screen shows that string.

Recommendation: use a **Desktop app** client. Anyone who deploys locally creates their own (or you ship one). Google treats Desktop client secrets as not confidential.

Two Google-side limits still apply while the consent screen is in "Testing":
- Only users listed as test users can sign in.
- Refresh tokens expire after 7 days.

To let anyone sign in, publish the app. `gmail.modify` is a restricted scope, so a public app needs Google verification.

### About `@connector:google_mail`
I didn't use Lovable's Gmail connector for this. It only works for apps running on Lovable's hosted gateway with a Lovable project key, so a locally deployed agent can't use it, and that key can't be handed to strangers. A per-user Google OAuth consent flow, running locally, gives the same result ("anyone links their own Gmail after consenting"). Each person keeps their own credentials.

---

## 3. Bugs in Mailkeep (fixed in the patch)

| # | Bug | Evidence | Fix |
|---|---|---|---|
| MK-1 | redirect_uri_mismatch | §2 | §2 |
| MK-2 | `mark N read` wrote to Gmail with no preview or confirmation. The README says every write asks for confirmation. | `assistant._mark_read` called `label_messages` directly | Added a preview and confirmation. Test: `test_mark_read_requires_approval` |
| MK-3 | A refreshed token was never saved, so every run after expiry refreshed again. If a refresh failed, the stale token was silently discarded. | `auth.load_credentials` | `cached_credentials()` saves the refreshed token |
| MK-4 | "Delete token" only removed the local file; the access stayed granted at Google. | `auth.forget_token` | Best-effort revoke at Google, then delete |
| MK-5 | The app claimed to be "built on OpenMinion" but never imported it. All logic was a regex parser. | `grep -r openminion mailkeep/` showed comments only | Added `agent.py` |
| MK-6 | README was UTF-16LE with CRLF line endings, which breaks diffs, grep and many renderers. | `file README.md` | Converted to UTF-8 |
| MK-7 | 119 scratch files under `.tmp-sdk/` (sqlite DBs, probes) and `.idea/` are committed. | `git ls-files \| grep -c tmp-sdk` = 119 | Added to `.gitignore`. You also need `git rm --cached` (see top). |
| MK-8 | `openminion/` is a gitlink (mode 160000) with no `.gitmodules`, so a fresh clone gets an empty folder, and the AGENTS.md workflow (`PYTHONPATH=openminion/src`) can't be reproduced. | `git ls-files -s` | Not changed. Either add `.gitmodules` or remove the gitlink and depend on PyPI (now in `requirements.txt`). |

Still open (not fixed):
- `label all newsletters as X` only labels up to `--limit` (20) matches and doesn't say so. The approval card at least shows the exact rows.
- `Assistant` keeps one mailbox state per process. The UI is built for one person on one machine.

---

## 4. OpenMinion 0.1.7: problems found while building on it

Each item has a reproduction. Items marked **new** aren't in your AGENTS.md ledger.

### OM-01 (P0, new): every later turn in a session replays the first turn's reply
```python
a = Agent(config_path=cfg, session_id="s1")
a.run("alpha").text   # 'openminion: alpha'
a.run("beta").text    # 'openminion: alpha'  <- wrong
Agent(config_path=cfg, session_id="s1").run("gamma").text  # 'openminion: alpha'
a.run("delta", session_id="s2").text   # 'delta'
a.run("epsilon", session_id="s2").text # 'delta'  <- wrong
```
- This happens with the echo provider and with a real model. With a real model, the second turn came back in 0.0 s with turn 1's text, so no model call was made.
- It survives a process restart: the replay is keyed by `session_id` and stored on disk.
- This makes `session_id` (OpenMinion's documented memory feature) unusable for multi-turn chat.
- **Workaround in the patch:** a fresh session id for each turn, a unique prefix per launch, and a 3-turn transcript Mailkeep keeps itself.

### OM-02 (P0, new): `system.providers` silently overrides `providers`
- `config init` writes both blocks. The loader merges them with `system` winning (`base/config/parser/payload.py:82`).
- So editing the obvious top-level `providers.openai.base_url` / `api_key_env` has no effect. `load_config()` still returns `https://api.openai.com/v1` / `OPENAI_API_KEY`.
- You get no warning. The error message even tells you to export the wrong variable.
- You have to edit **both** blocks. I spent a large part of the integration time tracking this down.

### OM-03 (P1, new): the first agent turn is slow and the default deadline is 90 s
- Measured cold turns of 72.8 s and 68.4 s on the same short request. The default deadline (90 s) then raised `TurnTimeoutError`.
- `TurnTimeoutError` is a different class from `ProviderError`, so an app that only catches `ProviderError` (the pattern your `test.py` shows) crashes.
- The patch catches both, uses a 180 s deadline (`MAILKEEP_AGENT_TIMEOUT`), and falls back to the rule-based parser.

### OM-04 (P1, new): the runtime leaks its own prompt scaffolding as the reply
- One live turn returned `"[CONSTRAINTS & POLICY] Respond directly … [BUDGET TELEMETRY] {"budget_envelope_status": …}"` as `result.text`.
- Another returned "the decision context exceeded the available token budget" after only ~12 tool calls on a 16-message mailbox.
- The patch filters out replies containing these markers.

### OM-05 (P2, new): replies are prefixed with the profile name
`result.text` starts with `"openminion: "` even when `Agent(name="mailkeep")` is set. Apps have to strip it.

### OM-06 (P1, already in your ledger, confirmed): `@tool` handlers get no context
- The decorator throws the context away (`tools/decorator.py: del context`).
- So a tool can't know which user, session or mailbox it's serving.
- The patch has to put the active session in a module-level global, guarded by a lock. That works only because the SDK already serialises turns that use decorated tools (your P0 lock finding).
- In other words, two SDK problems cancel each other out, and fixing either one alone would break apps that rely on this.

### OM-07 (P2, new): `approval_callback` signature mismatch
- `Agent.run(approval_callback=…)` takes 3 arguments: `(tool_name, args, approval_id)`.
- Internally it's wrapped into a 4-argument callback that drops `policy_facts`.
- So the public callback can't see why approval was requested. The `allowed_tools` allowlist and the approval policy are two separate systems.

### OM-08 (P2): the echo provider can't test tools
Echo never calls tools, so in practice the documented credential-free mode tests none of the agent behaviour. Mailkeep detects `echo` and uses its own parser.

---

## 5. Where OpenMinion is harder to use than a focused library

Compared with LangChain/LangGraph, the OpenAI Agents SDK, or a plain Gmail API wrapper:

| Task | OpenMinion 0.1.7 | Focused library |
|---|---|---|
| Point a model at an OpenAI-compatible endpoint | Generate a ~200-line `agents.json`, edit **two** provider blocks (OM-02), name the key's env var, check by trial and error | `ChatOpenAI(base_url=…, api_key=…)` in one line |
| Give a tool per-request state (user, mailbox) | Impossible through `@tool` (OM-06); needs a global plus a lock | LangChain `RunnableConfig` / injected tool args; OpenAI Agents `RunContextWrapper` |
| Multi-turn memory | `session_id` replays stale replies (OM-01) | `RunnableWithMessageHistory`, checkpointers, or passing the message list yourself |
| Async apps (FastAPI) | `Agent.run` is sync-only and blocks the event loop (your P0) | `ainvoke` / `Runner.run` are native async |
| Streaming | `run_stream` doesn't stream tokens; real streaming needs `APIRuntime.submit_turn` | `astream` / `stream_events` |
| Errors | `ProviderError`, `TurnTimeoutError`, `AgentOutputValidationError` live in different modules; timeout isn't a provider error | One error base per library |
| Footprint | ~1.1M lines of code, 167 tools registered at start-up, ~3 s cold start, sqlite stores scattered under the data root | Install only what you use |
| Human approval | Global `action_policy` with token lists ("yes", "sure", …) parsed from chat text, plus a separate callback | LangGraph `interrupt()`, which is explicit and resumable |

Where it does better:
- Policy and approval, memory, MCP, skills and telemetry ship together in one package.
- The read-only/write risk model is a good idea, once it works (your MCP `readOnlyHint` finding).

For one focused job like Gmail triage, a small app has to work around OpenMinion more than it gets to use it. Mailkeep ends up using one tool and one call to `Agent.run`.

---

## 6. Remaining risks

- **Restricted scope:** `gmail.modify` means public distribution needs Google's restricted-scope verification (a security assessment). For a hackathon, keep the app in "Testing" with listed test users.
- **The model writes the commands:** it can still choose the wrong rows. The approval card with message IDs is the safety net, so don't add an auto-approve setting.
- **Cost and limits:** turns are slow (OM-03) and can hit the provider's rate limit. Mailkeep falls back to the rule-based parser instead of failing.
