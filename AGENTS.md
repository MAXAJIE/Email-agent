# AGENTS.md — Project memory for the competition workspace

> Product target: **Mailkeep** (Gmail triage / draft steward agent) for the
> Cortensor x OpenMinion Hackathon #5. Plan verification, corrected Gmail tool
> contracts and the Day-1 findings live in `docs/mailkeep-plan-verification.md`;
> integration runbook in `docs/gmail-integration.md`.

Status: active
Established: 2026-10-19
Owner: the user (Chin Jie)
Scope: `C:\Users\Chin Jie\Desktop\competition` **only**

This file is the durable memory for every future agent session in this
workspace. Read it before changing anything. Update it whenever the
architecture, environment, or known-issues ledger changes.

---

## 1. Standing mandate from the user

1. **`openminion/` is the core of this project.** Application code, product
   surfaces, integrations, and experiments must be built *on* the OpenMinion
   runtime/SDK. Do not replace it with a parallel agent framework, do not fork
   its runtime into app code, and do not bypass its policy/tool/session owners.
2. **Understand OpenMinion completely** before making architectural calls.
   Prefer reading its owner map, READMEs, and implementation over guessing.
3. **Always be a critic.** Every time OpenMinion is used or tested, look for
   bugs, especially **SDK architecture problems** (sync/async seams, global
   mutable state, lifetime/ownership, error semantics, cross-platform
   behavior). Report findings with file:line evidence and a reproduction.
   Never hide a defect behind a workaround silently.
4. **Stay inside this folder.** This workspace is the only place with write
   access. Never read, modify, or execute anything outside it except
   system-level tooling needed to run commands here (Python, pip, git, bash).
5. **Full development rights inside this folder** are granted: edit, run,
   test, install local dependencies, create temp fixtures. Do not push,
   deploy, or rewrite history without being asked.
6. Because the user wants durable memory, **this file is the memory**: keep it
   current rather than relying on conversation history.

---

## 2. Repository layout (verified)

```text
competition/
  README.md                 # UTF-16 file that only says "Email-agent" (the app idea)
  test.py
  .venv/                    # Python 3.13 venv; openminion 0.1.7 installed as a COPY
  .openminion/              # runtime data used by the user's local daemon
    agents.json             # active runtime config (provider=echo, agent=openminion)
    memory/memory.audit.db  # OpenMinion's own memory store (agent memory, not this file)
    logs/openminiond.log
  openminion/               # the real project (nested git repo, main @ 49c776ae)
```

### OpenMinion at a glance

| Item | Value |
| --- | --- |
| Package | `openminion` 0.1.7, Apache-2.0 |
| Python | >= 3.11 (dev venv is 3.13) |
| Size | ~4,900 Python files, ~1.1M LOC (src ≈ 24 MB, tests ≈ 24 MB) |
| Test files | 1,877 `test_*.py` files |
| Entry points | `openminion`, `openminiond`, `runtimectl`, `memctl`, `brainctl`, `contextctl`, `sessctl`, `artifactctl`, `skillctl`, `a2actl`, `retrievectl`, `policyctl`, `telemetryctl`, `opsctl`, `identityctl`, `agentregctl`, `rlmctl` |

### Source owners (`openminion/src/openminion/`)

| Owner | Role | LOC (approx) |
| --- | --- | --- |
| `api/` | Public Python + HTTP runtime: `APIRuntime`, `Agent`, `turns`, routes | 12,856 |
| `base/` | Foundational config/contracts/primitives | 9,519 |
| `cli/` | CLI parsing + interactive terminal UX | 45,788 |
| `modules/` | Feature owners (brain 103k, memory 32k, controlplane 23k, tool 23k, context 21k, llm 20k, storage 16k, telemetry 15k, task 14k, session 13k, skill 11k, policy 6k, retrieve 5k, identity 5k, runtime 5k, a2a 4k, artifact 3k, registry 2k) | ~380k |
| `services/` | Cross-owner orchestration (runtime 12k, agent 10k, brain 6k, gateway 6k, health, bootstrap, context, cron, supervision) | ~35k |
| `tools/` | Tool host + package-owned tool families, `@tool` decorator | 76,971 |

Public stability promises are only `openminion` and `openminion.api`
(documented in `openminion/API_COMPATIBILITY.md`). `modules.*`, `services.*`,
and `tools.*` are real owners but **not** blanket public API.

### Canonical turn path (declared in `api/runtime.py`)

```text
services/runtime/ingress.run_turn_payload
  -> services/request_orchestrator.run_turn
  -> GatewayService.run_once
  -> BrainBridgeService.run_turn
  -> BrainRunner.run
```

`APIRuntime.run_turn()` delegates to `services.runtime.ingress.run_turn_payload`.
`APIRuntime.submit_turn()` returns a thread-backed `RuntimeTurnHandle`
(`.result()`, `.stream()`, `.cancel()`, `.resolve_approval()`).

### SDK facade

Root `openminion` lazily exports: `APIRuntime`, `Agent`,
`AgentOutputValidationError`, `AgentRunResult`, `Handoff`, `MemoryBundle`,
`OpenMinionConfig`, `ProviderError`, `TurnTimeoutError`, `subagent`, `tool`,
`__version__`.

`openminion.api` additionally exports `RuntimeTurnHandle`, `TurnChunk`,
`TurnResponse`, `TurnTelemetry`, `ToolCallSummary`, `TurnError`,
`dispatch_request`.

`Agent` is **synchronous only**. `run_stream()` is not real streaming; it is
`run()` plus a progress callback. True iterator streaming requires
`APIRuntime.submit_turn()`.

---

## 3. Environment facts and how to run things

### Important: the venv does not use the source tree

`.venv/Lib/site-packages/openminion/` is an installed **copy**. Editing
`openminion/src/...` has no effect on plain `python`/`openminion` commands
unless you either:

- run with `PYTHONPATH=openminion/src`, or
- reinstall editable: `pip install -e "openminion[dev]"` (project convention is
  `make dev-install`, but the Makefile is POSIX-only).

Use **`PYTHONPATH=openminion/src`** for all source verification in this repo.

### Verified working invocation (Windows bash)

```bash
cd /c/Users/"Chin Jie"/Desktop/competition
export PYTHONPATH=openminion/src
export OPENMINION_HOME="$PWD/.tmp-sdk/home"
export OPENMINION_DATA_ROOT="$PWD/.tmp-sdk/data"
.venv/Scripts/python.exe -m openminion \
  --config "$PWD/.tmp-sdk/agents.json" --dir "$PWD" run "hello"
```

`.tmp-sdk/agents.json` is a credential-free **echo** config created with:

```bash
.venv/Scripts/python.exe -m openminion --config "$PWD/.tmp-sdk/agents.json" config init --provider echo
```

Echo mode exercises config/storage/session/CLI plumbing only; it never calls a
model and never calls tools. Use a fake runtime (see `openminion/tests/api/`)
for SDK unit behavior, and a real provider for model/tool behavior.

### Test tooling installed during this session

The venv originally had **no pytest**. Installed:

```bash
.venv/Scripts/python.exe -m pip install pytest pytest-asyncio pytest-timeout
.venv/Scripts/python.exe -m pip install "agent-client-protocol>=0.12,<0.13"
```

Versions: pytest 9.1.1 (project pins `<9`), Python 3.13 (project targets 3.11).
`tzdata` is **not installed** — this causes the Windows timezone bug below.

### Focused test commands

```bash
cd openminion
# Fast SDK/API suite against source
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/api -q -p no:cacheprovider

# One module
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/api/test_agent.py -q
```

Known baseline on 2026-10-19: `tests/api` → **297 passed, 5 failed** (failures
listed in §4). `tests/api/test_agent.py` → 37/37 pass.

---

## 4. Known-issues ledger (verified, with evidence)

Severity is about impact on building the product on OpenMinion. Keep this
section current; add a date and a repro for every entry.

### P0 — Running `Agent.run()` inside an event loop blocks the loop for the whole turn

- **Where:** `openminion/src/openminion/services/runtime/ingress/gateway_call.py:51-85`
  (`_run_coro_sync` spawns a daemon thread and calls `thread.join()` when a loop
  is already running).
- **Why it matters:** OpenMinion's public SDK is advertised for embedding in
  Python apps, but `Agent.run()`/`run_stream()` are sync-only. In FastAPI,
  Jupyter, or any `async def`, the entire event loop stalls for the turn
  (measured: 0 heartbeat ticks during an 0.85 s turn).
- **Repro:** `asyncio.run(main())` where `main` starts a 50 ms heartbeat task
  and calls `Agent(...).run(...)`.
- **Direction:** first-class `async def run()/arun()` on `Agent` that awaits the
  handle instead of thread-joining; keep the sync wrapper for compatibility.

### P0 — Concurrent `Agent` runs on a shared runtime serialize on a global tool-registry lock

- **Where:** `openminion/src/openminion/api/agent.py:299-304` — `_run_once`
  acquires `registry.temporary_registration_lock` and holds it across the
  **entire** `runtime.run_turn(...)` call (the `with` block wraps the turn).
- **Why it matters:** any Agent constructed with `@tool`-decorated tools or
  handoffs serializes with every other such Agent on the same `APIRuntime`.
  A shared runtime therefore cannot serve concurrent SDK agents; latency adds
  linearly.
- **Measured:** 4 threads × echo turn (~0.96 s each) on one runtime completed at
  0.96 / 2.13 / 3.05 / 4.05 s, total 4.05 s instead of ~1 s.
- **Direction:** make user tools **turn-scoped bindings** (pass specs through
  the payload/session) instead of mutating a process-global registry, or take
  the lock only for register/unregister, not for the provider call.

### P0 — Timezone handling is broken by default on Windows

- **Where:** `modules/task/scheduling/schedule.py:508-528` and
  `tools/time/plugin.py:53-66` use `zoneinfo.ZoneInfo`; `pyproject.toml` does
  not declare `tzdata`, and `_default_timezone_name()` falls back to `"UTC"`.
- **Why it matters:** On Windows there is no system IANA tz database, so
  `ZoneInfo("UTC")` raises `ZoneInfoNotFoundError`. Creating a scheduled task
  with `tz="UTC"` returns HTTP 400 `unknown timezone: UTC`; the default path
  (no tz) also resolves to `"UTC"` and fails. Cron/task scheduling is
  effectively unusable on Windows out of the box.
- **Evidence:** `tests/api/routes/test_tasks_route.py::test_tasks_route_creates_and_deduplicates_user_schedule`
  fails with `unknown timezone: UTC`; direct `ZoneInfo("UTC")` fails in the venv.
- **Secondary:** even with `tzdata`, Windows local names (e.g. "Malay Peninsula
  Standard Time") are not IANA keys; `_default_timezone_name()` silently falls
  back to UTC, so "local 9am" schedules can fire at the wrong wall-clock time.
- **Fixed 2026-10-19:** added `tzdata; sys_platform == 'win32'` to
  `project.dependencies`; `openminion schedule create --tz UTC` and
  `--tz Asia/Kuala_Lumpur` now succeed.
- **Still open:** map Windows local names (e.g. "Malay Peninsula Standard Time")
  to IANA keys via `tzlocal` or explicit resolution; fail loudly if local tz
  cannot be resolved instead of silently using UTC.

### P1 — `@tool` silently accepts `async def`, then returns an un-awaited coroutine

- **Where:** `openminion/src/openminion/tools/decorator.py:113-118` (`_handler`
  calls `func(**values)` and returns whatever it gets).
- **Why it matters:** `@tool` on an async function is accepted at import time;
  at execution the tool result is a coroutine object, causing a confusing
  serialization error and a "coroutine was never awaited" warning. This is a
  common SDK trap.
- **Evidence:** a `@tool async def` handler returned `coroutine` from
  `derive_tool_specs(...)[0].handler(...)`.
- **Direction:** either reject async callables at decoration time with a clear
  error, or await them in the executor.

### P1 — `@tool` user tools cannot access runtime context

- **Where:** `openminion/src/openminion/tools/decorator.py:113-118` discards
  `context` (`del context`).
- **Why it matters:** an SDK tool cannot see workspace root, session, agent id,
  policy facts, or artifacts through the documented decorator. Anything
  stateful forces users into undocumented deep internals.
- **Direction:** expose a typed context parameter/annotation (e.g.
  `ctx: ToolContext`) without breaking existing pure functions.

### P1 — Unknown tool names in an Agent allowlist are silently accepted

- **Where:** `api/agent.py:168-169` sends `allowed_tools` as-is;
  `services/runtime/ingress/payloads.py:93-97` turns it into
  `turn_tool_allowlist`; no validation against `runtime.tools.list()`.
- **Why it matters:** a typo such as `tools=["not.a.real.tool"]` runs
  successfully with zero tools. The model then either answers without tools or
  hallucinates tool use; the developer gets no signal.
- **Evidence:** echo run with `tools=["not.a.real.tool"]` returned a normal
  reply.
- **Direction:** validate allowlist entries at `Agent.run()` start (excluding
  names intentionally deferred to MCP/dynamic providers) and raise/warn with the
  closest matches.

### P1 — User-tool exposure is global while a turn is in flight

- **Where:** `tools/decorator.py:150-157` registers a profile with
  `default_active=True`; `modules/tool/exposure/service.py:266-274` returns
  `visible` for any `default_active` profile, regardless of session.
- **Why it matters:** Agent A's run-scoped decorated tools are registered in
  the shared registry and visible to *other* sessions during that window
  (concurrency is currently masked by the P0 lock, but the design is unsafe).
- **Direction:** scope the exposure profile to the turn/session, or use an
  ephemeral per-turn registry.

### P1 — Tool handler dispatch retries a failing handler with swapped arguments

- **Where:** `modules/tool/runtime/registry_toolspec.py:341-354` — the
  ctx-name heuristic (`"ctx" in name` / `"context" in name`) decides argument
  order; if the first call raises **any** exception, it calls the handler a
  second time with `(runtime_ctx, arguments)` and only then re-raises.
- **Why it matters:** any ToolSpec whose handler param names do not match the
  heuristic can be executed twice on failure — dangerous for non-idempotent
  side effects (payments, email, file writes). Name-based signature detection
  is fragile in general.
- **Direction:** use explicit registration metadata (handler kind) rather than
  parameter-name guessing; never retry a handler after it has begun executing.

### P1 — `--json` CLI output is not UTF-8 on Windows (and verbose text can crash)

- **Where:** CLI writes JSON/text through the locale-encoded stdout; on Windows
  that yields cp1252 bytes. A literal em dash (0x97) appears at byte 65047 of
  the 268 KB `doctor --json` payload, and two `0x97` bytes appear in the 47 KB
  `openminion mcp test` payload (verified 2026-10-19: a UTF-8 reader raises
  `UnicodeDecodeError`; piping into Python only worked because the reader also
  used the legacy locale codec).
- **Same root cause, worse symptom:** `openminion tools list --verbose` aborts
  with `UnicodeEncodeError: 'charmap' codec can't encode character '\u2713'`
  after printing only the header (`Available tools (98):` + `[CORE]`). Plain
  `tools list` is unaffected.
- **Why it matters:** machine consumers (`jq`, other services, CI) that assume
  UTF-8 fail to parse `openminion doctor --json` / `mcp test`, defeating the
  documented `--json` automation surface; one of the advertised Day-1 operator
  commands crashes outright on a default Windows console.
- **Direction:** encode JSON output explicitly as UTF-8 (e.g.
  `sys.stdout.reconfigure(encoding="utf-8")` or write bytes) for all `--json`
  commands, and use ASCII fallbacks in verbose renderers.

### P2 — Windows path separators break the API layout validator

- **Where:** `openminion/scripts/validate/api_layout.py` compares
  `Path.relative_to(...)` strings (backslashes) against baselines written with
  forward slashes (`scripts/baselines/api_route_owner_imports.tsv`).
- **Evidence:** `validate/api_layout.py` reports 3 "new" + 3 "stale" imports
  (`routes\debug.py` vs `routes/debug.py`); `tests/api/test_api_layout_validator.py`
  fails 2/6.
- **Why it matters:** `make lint` / CI gates cannot pass on Windows even with
  no real ownership violation.
- **Direction:** normalize with `.as_posix()` before comparison/baseline IO.

### P2 — Daemon logs are ~97% heartbeat spam

- **Where:** `.openminion/logs/openminiond.log` contained 2,747 heartbeat lines
  out of 2,841 total in 22 minutes (~2.06 lines/s), growing ~67 MB/day at the
  default level.
- **Why it matters:** default INFO logging of `component.heartbeat` drowns
  diagnostics and burns disk; real errors are hard to find.
- **Direction:** demote recurring heartbeats to DEBUG or sample them; add
  rotation/retention defaults.

### P2 — SDK/runtime cold start is heavy for embedding

- **Measured:** `APIRuntime.from_config_path(echo)` took **3.33 s** and
  registered **167 tools** eagerly, before any turn.
- **Why it matters:** every short-lived SDK script/CLI invocation pays seconds
  of startup and loads tool families it will never use.
- **Direction:** lazy tool-family registration and lazy subsystem bootstrap;
  publish a `minimal` profile for embedded SDK use.

### P2 — Turn-deadline tests are timing-flaky on Windows

- `tests/api/test_turn_deadline.py` produced different failures across runs
  (`test_sdk_timeout_discards_running_tool_result`,
  `test_submitted_turn_timeout_has_failed_receipt`,
  `test_sdk_timeout_has_no_late_requests_tools_or_answer[True]`), while the
  same tests pass in other runs. A 1 s deadline often expires during ~1 s of
  bootstrap, before the tool can start.
- **Direction:** measure the deadline from turn start excluding bootstrap, or
  make tests assert behavior rather than sub-second wall-clock windows.

### P0 — MCP stdio transport is unusable on Windows

- **Where:** `tools/mcp/transport.py:54,110` creates
  `selectors.DefaultSelector()` and `transport.py:496` registers the child
  process's **pipes** (stdin/stdout). On Windows the default selector is
  `SelectSelector`, which only supports sockets.
- **Symptom:** every stdio MCP server fails on first read with
  `OSError: [WinError 10038] An operation was attempted on something that is
  not a socket`.
- **Evidence:** `pytest tests/mcp` on this machine → 32 failed, 3 errors, 211
  passed; 67 occurrences of WinError 10038. HTTP transport tests pass.
- **Impact:** blocks every stdio MCP integration (google_workspace_mcp, npx
  servers, the bundled mock servers) on Windows.
- **Direction:** use a reader thread + `queue.Queue` (or `asyncio`
  `connect_read_pipe` on POSIX / thread pump on Windows) instead of
  `selectors` over pipes; keep the selector path for POSIX only.

### P1 — stdio MCP children inherit an empty environment by default

- **Where:** `tools/mcp/transport.py:147-150` passes
  `stdio_sandbox.inherit_env_allowlist` (default `[]`) into
  `build_subprocess_env(inherit_parent=...)`; an empty list is falsy, so the
  child env is `{}` plus configured keys (`base/config/env/subprocess.py:28-38`).
- **Impact:** even after the selector fix, `uvx`/`npx`-launched servers lose
  `PATH`, `SYSTEMROOT`, `TEMP`, and `USERPROFILE`; launchers may not resolve and
  child runtimes can misbehave.
- **Direction:** treat an empty allowlist as "inherit the default allowlist" or
  require an explicit `inherit_env_allowlist` in config validation.

### P1 — MCP risk resolution discards read-only annotations

- **Where:** `tools/mcp/risk.py:26-36`. `readOnlyHint` is honored only when
  `server.trusted` is true, and `openWorldHint: true` then downgrades
  `READ_ONLY` to `WRITE_SAFE` anyway.
- **Impact:** a read-only network API tool (Gmail search, Drive list) can never
  be classified `READ_ONLY`, so `action_policy.allow_read_only_without_prompt`
  never applies and unattended/scheduled use requires per-call confirmation.
  Measured: all 15 google_workspace_mcp Gmail tools report `WRITE_SAFE`,
  `idempotent=false` despite `readOnlyHint`/`idempotentHint`.
- **Workaround in use:** explicit `tool_risk_overrides` per tool in
  `.openminion/agents.json` (see `docs/gmail-integration.md`).
- **Direction:** treat `openWorldHint` as a network/egress signal, not a write
  signal; honor `readOnlyHint` independent of stdio trust once the operator has
  added the server, or expose a per-server `trust_annotations` flag.

### P2 — `openminion mcp add` cannot set server environment

- **Where:** `cli/commands/mcp.py:_mcp_add` only accepts
  `--url/--cwd/--trusted/--required/--disabled/--command`.
- **Impact:** secrets/URLs for a server cannot be configured from the CLI even
  though `MCPServerConfig` supports `env` and `env_secret_refs`. Users must hand
  edit `agents.json`.
- **Direction:** add repeatable `--env KEY=VALUE` and
  `--env-ref KEY=secret:REF`, with the same redaction rules as `mcp get`.

### P2 — `tools list` silently hides MCP tools when the daemon predates a config change

- **Where:** `openminion tools list` defaults to `--runtime-source auto`, which
  answers from the running daemon; MCP server changes report
  `"applies": "next_startup"` but the stale listing carries no warning.
- **Evidence (2026-10-19):** after registering the Gmail MCP server,
  `tools list` (daemon) showed 98 tools / 0 `mcp.*`; `tools list
  --runtime-source inproc` showed 113 tools / 15 `mcp.workspace.*`.
- **Impact:** an operator can believe an MCP integration failed when the only
  problem is that the daemon needs a restart.
- **Direction:** surface config staleness in `tools list`/`status` (e.g. a
  "daemon config is older than on-disk config" banner), or make `auto` fall back
  to inproc with an explicit note.

### Note (not a bug): `run_stream()` does not stream tokens

Documented, but the name misleads. Real streaming is
`APIRuntime.submit_turn()` → `RuntimeTurnHandle.stream()`.

---

## 5. Architectural observations (for design decisions)

1. **The SDK is a thin sync facade over an async runtime.** The async core is
   thread-bridged at `ingress/gateway_call.py`; there is no async public Agent.
   Any product that is itself async (web backend, websocket server) will fight
   the SDK.
2. **Turn-scoped state is implemented by mutating process-global registries.**
   `Agent` registers decorated tools/handoff families into `APIRuntime.tools`
   for the duration of a turn, then unregisters. This is the root cause of the
   serialization and exposure-leak findings. A true SDK would bind tools to the
   request.
3. **The runtime is broad rather than composable.** One `APIRuntime` eagerly
   wires brain, memory (Sophiagraph), identity, policy, telemetry, retrieve,
   context, sessions, and 167 tools. There is no small "agent only" profile.
4. **Layering is disciplined and enforced.** `modules/` (domain) vs
   `services/` (orchestration) vs `api/` (transport) is real, with validator
   scripts and baselines. This is a strength; preserve it when extending.
5. **Policy/approval is a real owner, not a stub.** Tool execution goes through
   `PolicyCtl` with typed decisions/grants; `resolve_approval` accepts only
   `allow_once` / `deny`. Do not bypass it in product code.
6. **Memory is Sophiagraph-backed** (`runtime.memory_provider=memory_v2`,
   `memory.backend.provider=sophiagraph|none|external`), with candidate
   review/promotion, bi-temporal validity, and delegated read-only grants.
7. **Public compatibility is taken seriously.** `API_COMPATIBILITY.md` and
   `__since__` metadata define a 0.1.x additive contract; prefer adding
   surfaces over changing existing ones.

---

## 6. Working rules for future sessions

1. Reproduce before fixing. Add a failing test or a minimal script under
   `.tmp-sdk/` (git-ignored/scratch) that shows the defect.
2. Prefer fixes at the owning boundary (per engineering patterns in
   `openminion/docs/engineering-patterns.md`), not app-level workarounds.
3. Keep `openminion`'s layering: `api` = transport/composition, `services` =
   orchestration, `modules` = domain, `tools` = tool host.
4. Do not add new fake memory records to `.openminion/memory/`; that store
   belongs to the running agent. This file is the durable instruction memory.
5. Use `PYTHONPATH=openminion/src` when verifying source changes; reinstall the
   package only if you deliberately want to update the venv.
6. Re-run at least `tests/api/test_agent.py` plus any directly affected suite
   after SDK changes. For cross-platform claims, test on Windows explicitly.
7. Report every observed defect in chat, with severity, evidence, and proposed
   direction. Do not silently paper over failures.

---

## 7. Session log

### 2026-10-20 — provider-failure fallback covered by tests
- Added `tests/test_fallback.py` (4 cases): `ProviderError` → rule-parser answer
  with the provider code/message shown, `TurnTimeoutError` → same, other
  exceptions still propagate, and no rule-parser re-run while a write approval
  is pending. The tests replace `_agent.run_stream` with a raising stub, so they
  need no network or credentials.
- Verified: `tests` → **92 passed** (88 before + 4 new).

### 2026-10-19 — custom LLM provider aborted before any request
- **Symptom:** `MAILKEEP_LLM_PROVIDER=custom` never contacted the endpoint and
  the UI fell back to the rule parser. No provider error, no HTTP request.
- **Root cause:** the user's `gmail.env` had `MAILKEEP_LLM_PROVIDER=custom`,
  `MAILKEEP_LLM_API_KEY=nvapi-...`, and a NVIDIA model, but no
  `MAILKEEP_LLM_BASE_URL`. `llm.selected()` raised `set MAILKEEP_LLM_BASE_URL`,
  which `resolve_mode()` caught and turned into `mode=rules` — so the OpenMinion
  agent was never constructed.
- **Fix:** add an `nvidia` preset (slot `openai`,
  `https://integrate.api.nvidia.com/v1`, `NVIDIA_API_KEY`); infer the base URL
  for `custom` from an unambiguous key prefix (`nvapi-`, `AIza`, `sk-or-`),
  never from a bare `sk-`; accept `OPENAI_BASE_URL` as a `custom` alias; keep
  the explicit error when inference is impossible.
- **Related:** `write_config()` now resolves `GENERATED_CONFIG` at call time
  (tests monkeypatched the global but a bound default ignored it).
- **Related:** moved `from openminion import ProviderError, TurnTimeoutError`
  to the failure path — the lazy export cost ~5 s cold before every turn and
  made Ctrl+C/cancel unresponsive during startup.
- Verified: `tests` → **88 passed**; end-to-end custom endpoint turn replies.
- Note: NVIDIA's `deepseek-ai/deepseek-v4.1-flash` accepts `/v1/models` but did
  not return chat headers within 150 s from this machine; that is upstream
  capacity/model latency, not the provider wiring (which now sends requests).

### 2026-10-19 — three spec skills authored in the verified recipe format
- Added `skills/email-triage`, `skills/email-draft`, `skills/email-organize`
  from the skill spec, but **rewritten into OpenMinion's real format** rather
  than the Claude-style frontmatter the spec assumed: `id`, `version`, `risk`,
  `tools`, `verification`, and a structured `recipe` block, plus body sections
  `# Summary` / `# Procedure` / `# Verification` / `# Rollback` / `# Safety`.
- Every skill has `fixtures/input.json` + `fixtures/expected.txt`, so the
  harness reports **0 warnings** (the pre-existing `mailkeep` skill was missing
  fixtures; added them too).
- Harness result: `ok=True total=4 passed=4 warnings=0 errors=0`.
- Verified end-to-end: `skill ingest` → `skill admit` → `skill show` returns
  `status: verified` with the full recipe.
- Verified recipe tool binding with a real registry: all tool steps bind
  (`search-unread -> mcp.workspace.search_gmail_messages`, etc.). The `None`
  steps are the deliberate non-tool approval gates (`print-batch`,
  `print-draft`, `print-plan`).
- Verified UTF-8 integrity: `重要` / `需跟进` / `可忽略` survive ingest intact.
  The mojibake in `skill show` output is the already-logged cp1252 console
  defect, not data corruption. Added an ASCII fallback to the triage skill so a
  demo cannot crash on a default Windows console.
- Found two new defects: `discover_skill_roots` rejects a `str` root, and
  `skill show` nulls every `tool_id` when no tool registry is configured.

### 2026-10-19 — Mailkeep plan verified against source
- Answered the plan's Day-1 checklist: permissions (4 postures, Ask default,
  confirmation preview + arg whitelist), skills frontmatter keys + `skill test`,
  native task scheduling/watch, delegation, memory (sophiagraph/memctl),
  diagnostics; wrote `docs/mailkeep-plan-verification.md`.
- Replaced the plan's guessed Gmail tool names with the real 14-tool contract
  (`user_google_email` required on every call).
- Hardened the server: `--disabled-tools send_gmail_message` (verified 14 tools,
  send absent).
- Found three CLI defects: `tools list --verbose` crashes on cp1252, JSON
  output is not UTF-8, stale-daemon `tools list` hides MCP tools.

### 2026-10-19 — Gmail integration wired
- Registered google_workspace_mcp over streamable HTTP on `127.0.0.1:8001`
  (`workspace` server, 15 Gmail tools, protocol 2026-07-28).
- Added `tool_risk_overrides` so read tools are `READ_ONLY`; verified via
  `openminion mcp test` and `tools run mcp.workspace.search_gmail_messages`.
- Added `tzdata` (Windows) and verified `schedule create` with
  `Asia/Kuala_Lumpur`; created `gmail-morning-digest` at 08:00 local.
- Added `scripts/start_gmail_mcp.py`, `scripts/gmail.env.example`, root
  `.gitignore`, `docs/gmail-integration.md`.
- Remaining: user must supply Google OAuth client, a real LLM provider key, and
  a Telegram bot token before the digest can run and push to a phone.

### 2026-10-19 — initial deep read
- Mapped owners, turn path, public surfaces, memory/policy/brain roles.
- Ran `tests/api`: 297 passed / 5 failed (Windows path + tzdata + flaky
  deadline).
- Live-probed echo runtime: Agent run, concurrency serialization, event-loop
  blocking, unknown tool names, async tool decoration, cold start.
- Created `.tmp-sdk/` scratch config/data and probe scripts (safe to delete).
- Installed pytest tooling and ACP extra into `.venv`.

---

## 8. Gmail-first integration (current direction)

- Interpreted mailbox commands pass through `Session.propose_command`; explicit
  slash commands retain `run_command`. This separates intent confirmation from
  execution while preserving the existing message-ID checked write previews.
- Label browsing resolves real names to IDs and uses Gmail `labelIds`; label
  renaming uses `labels.patch` with an ID-checked approval, so membership is not
  changed and a replaced label cannot receive an old approval.

- **Product goal:** an email agent built on OpenMinion; Gmail is the primary
  surface, with phone notifications and scheduled digests.
- **Provider:** `taylorwilsdon/google_workspace_mcp` (MIT), run as a local
  streamable-HTTP MCP server. Full details and commands:
  `docs/gmail-integration.md`.
- **Do not** use its stdio transport on Windows until the selector bug above is
  fixed.
- **Gmail tools require `user_google_email`** in every call, even with
  `--single-user`.
- **Credential paths stay inside the workspace:**
  `.openminion/gmail.env` (OAuth client) and
  `.openminion/gmail/credentials` (tokens). Never commit either.
- **Phone push** goes through OpenMinion's Telegram channel; isolated scheduled
  tasks default to `delivery={mode: announce, channel: last}`.
- **Blocked on user-supplied secrets:** Google OAuth client id/secret, LLM
  provider API key (config is still `provider: echo`), Telegram bot token.
