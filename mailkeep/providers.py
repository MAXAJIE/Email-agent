"""Which model providers are online, which role each one serves, and fallback.

Roles ("profiles") are default, reasoning, draft, fast, classifier. A role that
is not configured, or whose provider has no key or is offline, falls back to the
main (default) model. If the main model is unusable too, Mailkeep runs on its
rule-based parser, so there is always something that answers.

The UI settings gear writes through ``config.set_ui_override``; .env files stay
the preset. No key is ever returned by this module, only whether one exists.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from . import fastllm, llm
from .config import set_ui_override, setting, ui_override

ROLE_INFO = {
    "default": "Main model: chat, mailbox commands (find, label, archive...)",
    "reasoning": "Planning, comparing, explaining, prioritising",
    "draft": "Writing and revising email drafts",
    "fast": "Short questions and quick lookups",
    "classifier": "Triage and classification",
}
ROLES = tuple(ROLE_INFO)

_cache_lock = threading.Lock()
_health: dict[str, fastllm.Health] = {}


def provider_key(preset: llm.Preset) -> tuple[str, str]:
    """(api key, where it came from) for one provider; never logged."""
    names = (preset.key_env, *preset.alt_key_envs)
    for name in names:
        value, source = ui_override(name)
        if value:
            return value, f"settings ({source})"
    for name in names:
        value = setting(name)
        if value:
            return value, f".env / environment ({name})"
    try:
        main = llm.selected()
    except ValueError:
        main = None
    if main is not None and main.key == preset.key:
        value = llm._api_key(main)
        if value:
            return value, ".env / environment (MAILKEEP_LLM_API_KEY)"
    if preset.key != "custom":
        value = llm._key_by_prefix(preset.key)
        if value:
            return value, ".env / environment (recognised key)"
    return "", ""


def _probe_preset(preset: llm.Preset, key: str) -> llm.Preset:
    # profile != "default" makes fastllm send exactly this key.
    return replace(preset, profile="probe", api_key=key)


HEALTH_TTL = 60.0  # seconds a probe verdict is trusted before re-probing


def _health_key(preset: llm.Preset) -> str:
    return f"{preset.key}|{preset.base_url}|{preset.model}"


def health_of(preset: llm.Preset | None, *, refresh: bool = False,
              timeout: float = 5.0) -> fastllm.Health:
    """Probe verdict for exactly this provider + endpoint + model (cached)."""
    if preset is None:
        return fastllm.Health("not_configured", detail="no model", checked_at=time.time())
    k = _health_key(preset)
    with _cache_lock:
        cached = _health.get(k)
    if cached is not None and not refresh and time.time() - cached.checked_at < HEALTH_TTL:
        return cached
    key = llm._api_key(preset)
    if not key:
        result = fastllm.Health("not_configured", detail="no API key", checked_at=time.time())
    else:
        result = fastllm.probe(_probe_preset(preset, key), timeout=timeout)
    with _cache_lock:
        _health[k] = result
    return result


def mark_failed(preset: llm.Preset | None, exc: BaseException) -> None:
    """A real call failed: stop routing to it until a fresh probe says otherwise."""
    if preset is None:
        return
    text = fastllm.describe_error(exc)
    state = "model_missing" if ("404" in text or "not found" in text.lower()) else (
        "auth_error" if any(m in text for m in ("401", "403")) else "offline")
    with _cache_lock:
        _health[_health_key(preset)] = fastllm.Health(
            state, detail=f"call failed: {text[:160]}", checked_at=time.time())


def _dedicated(profile: str) -> tuple[llm.Preset | None, str]:
    """The role's own provider + key + model, or (None, why it has none).

    A role (and the agent that runs on it) is only used when all three are
    configured for it: MAILKEEP_LLM_<ROLE>_PROVIDER, MAILKEEP_LLM_<ROLE>_MODEL
    and an API key (MAILKEEP_LLM_<ROLE>_API_KEY or that provider's own key).
    Anything less falls back to the main model instead of guessing.
    """
    prefix = f"MAILKEEP_LLM_{profile.upper()}_"
    provider = setting(prefix + "PROVIDER").strip()
    model = setting(prefix + "MODEL").strip()
    if not provider and not model:
        return None, "not set, uses the main model"
    if not provider:
        return None, f"{prefix}PROVIDER is not set (a role needs its own provider, key and model)"
    if not model:
        return None, f"{prefix}MODEL is not set (a role needs its own provider, key and model)"
    try:
        preset = llm.selected(profile)
    except ValueError as exc:
        return None, str(exc)
    if preset is None:
        return None, "no provider resolved"
    if not llm._api_key(preset):
        return None, f"{preset.label} has no API key ({prefix}API_KEY or {preset.key_env})"
    return preset, ""


def effective(profile: str) -> tuple[llm.Preset | None, str]:
    """(preset to use for `profile`, note). Falls back to the main model.

    Never returns a role model whose last probe/call said it is unusable.
    """
    try:
        main = llm.selected()
        main_error = ""
    except ValueError as exc:
        main, main_error = None, str(exc)
    if profile == "default":
        return main, main_error
    preset, why = _dedicated(profile)
    if preset is not None:
        health = health_of(preset)
        if health.state in fastllm.USABLE_STATES:
            return preset, ""
        why = f"{preset.label} {preset.model} is {health.state} ({health.detail})"
    if main is None:
        return None, (why + "; " if why else "") + "no main model either, rule parser answers"
    if why == "not set, uses the main model":
        return main, why
    return main, (why + "; " if why else "") + "falls back to the main model"


def chain(profile: str) -> list[tuple[llm.Preset, str]]:
    """Presets to try, in order, for one job: the role's model, then the main one."""
    out: list[tuple[llm.Preset, str]] = []
    preset, note = effective(profile)
    if preset is not None:
        out.append((preset, note))
    try:
        main = llm.selected()
    except ValueError:
        main = None
    if main is not None and all(_health_key(p) != _health_key(main) for p, _ in out):
        out.append((main, "main model (fallback)"))
    return out


def complete(profile: str, system: str, user: str, *, max_tokens: int = 1280,
             timeout: float | None = None, cancel=None, on_fallback=None) -> tuple[str, llm.Preset]:
    """One completion for `profile`, falling back to the main model on failure.

    Raises the last error when every candidate failed (the caller then uses
    the rule parser). Returns (text, preset that answered).
    """
    candidates = chain(profile)
    if not candidates:
        raise fastllm.NotRetryable("no model provider configured")
    last: BaseException | None = None
    for index, (preset, _note) in enumerate(candidates):
        try:
            text = fastllm.with_retries(
                lambda n, p=preset: fastllm.complete(
                    p, system, user, max_tokens=max_tokens * min(n, 3), cancel=cancel,
                    timeout=timeout or fastllm.attempt_timeout(p.timeout_seconds)),
                what=f"{profile} via {preset.label} {preset.model}",
                cancelled=(cancel.is_set if cancel is not None else (lambda: False)))
            return text, preset
        except (InterruptedError, KeyboardInterrupt):
            raise
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            last = getattr(exc, "last", exc)
            mark_failed(preset, last)
            if index + 1 < len(candidates):
                nxt = candidates[index + 1][0]
                fastllm.log_event("llm-fallback", f"{profile}: {preset.label} {preset.model} failed "
                                  f"({fastllm.describe_error(last)[:120]}); using main model "
                                  f"{nxt.label} {nxt.model}")
                if on_fallback:
                    on_fallback(preset, nxt)
    raise last or RuntimeError("no model answered")


def _rows() -> list[dict]:
    try:
        main = llm.selected()
    except ValueError:
        main = None
    rows = []
    for preset in llm.PRESETS.values():
        key, source = provider_key(preset)
        model = setting(f"MAILKEEP_UI_{preset.key.upper()}_MODEL") or preset.model
        base_url = setting(f"MAILKEEP_UI_{preset.key.upper()}_BASE_URL") or preset.base_url
        if main is not None and main.key == preset.key:
            model, base_url = main.model, main.base_url
        rows.append({"preset": replace(preset, model=model, base_url=base_url), "key": key,
                     "source": source})
    return rows


def inventory(probe: bool = True, timeout: float = 5.0) -> dict:
    """Every supported provider: key present?, really online?, model, roles."""
    rows = _rows()

    def check(row):
        preset, key = row["preset"], row["key"]
        if not key:
            return fastllm.Health("not_configured", detail="no API key", checked_at=time.time())
        if not preset.base_url:
            return fastllm.Health("not_configured", detail="no base URL", checked_at=time.time())
        if not preset.model:
            return fastllm.Health("not_configured", detail="no model name", checked_at=time.time())
        return fastllm.probe(_probe_preset(preset, key), timeout=timeout)

    if probe:
        with ThreadPoolExecutor(max_workers=max(1, len(rows))) as pool:
            results = list(pool.map(check, rows))
        with _cache_lock:
            for row, health in zip(rows, results):
                _health[_health_key(row["preset"])] = health
        # Dedicated role models are probed too (they may be other models).
        for role in ROLES[1:]:
            preset, _why = _dedicated(role)
            if preset is not None:
                health_of(preset, refresh=True, timeout=timeout)
    roles = {}
    for role in ROLES:
        preset, note = effective(role)
        if preset is None:
            state = "off"
        else:
            cached = _health.get(_health_key(preset))
            state = cached.state if cached is not None else "unknown"
        roles[role] = {"purpose": ROLE_INFO[role], "provider": preset.key if preset else "",
                       "label": preset.label if preset else "rule parser",
                       "model": preset.model if preset else "", "note": note, "state": state}
    providers = []
    for row in rows:
        preset = row["preset"]
        health = _health.get(_health_key(preset)) or fastllm.Health(
            "not_configured" if not row["key"] else "unknown")
        providers.append({
            "key": preset.key, "label": preset.label, "model": preset.model,
            "base_url": preset.base_url, "key_present": bool(row["key"]),
            "key_source": row["source"], **health.public(),
            "roles": [r for r, info in roles.items() if info["provider"] == preset.key],
        })
    online = [p["key"] for p in providers if p["state"] == "online"]
    main = roles["default"]
    main_ok = bool(main["provider"]) and main["state"] in fastllm.USABLE_STATES
    return {"providers": providers, "roles": roles, "online": online,
            "mode_hint": "openminion" if main_ok else "rules"}


def startup_report() -> list[str]:
    """Console lines printed when localhost starts (no keys, only verified status)."""
    data = inventory(probe=True)
    lines = [f"providers online: {len(data['online'])}/{len(data['providers'])}"
             "  (online = the configured model is listed by the provider or answered a test)"]
    for p in data["providers"]:
        roles = ", ".join(p["roles"]) or "-"
        detail = f"  [{p['detail']}]" if p["state"] != "online" and p["detail"] else ""
        lines.append(f"  {p['label']:<28} {p['state']:<15} model={p['model'] or '-'}  "
                     f"roles={roles}{detail}")
    for role, info in data["roles"].items():
        tail = f"  ({info['note']})" if info["note"] else ""
        state = f" [{info['state']}]" if info["provider"] else ""
        lines.append(f"  role {role:<10} -> {info['label']} {info['model']}{state}{tail}")
    main = data["roles"]["default"]
    if not main["provider"]:
        lines.append("  no model configured: the rule parser answers. Add a key with the gear icon.")
    elif main["state"] not in fastllm.USABLE_STATES:
        lines.append(f"  main model {main['label']} {main['model']} is {main['state']}: chat uses "
                     "the rule parser until it answers (fix the model name / base URL / key).")
    return lines


def apply_settings(body: dict) -> None:
    """Save what the user entered in the settings gear.

    body = {"persist": bool, "providers": {key: {"api_key", "model", "base_url"}},
            "roles": {role: provider_key | "-" | ""}}
    Empty strings leave a value unchanged; "-" clears it.
    """
    persist = bool(body.get("persist"))

    def put(name, value):
        value = str(value or "").strip()
        if not value:
            return
        set_ui_override(name, "" if value == "-" else value, persist=persist)

    for key, values in (body.get("providers") or {}).items():
        preset = llm.PRESETS.get(str(key))
        if preset is None or not isinstance(values, dict):
            raise ValueError(f"unknown provider {key!r}")
        put(preset.key_env, values.get("api_key"))
        put(f"MAILKEEP_UI_{preset.key.upper()}_MODEL", values.get("model"))
        put(f"MAILKEEP_UI_{preset.key.upper()}_BASE_URL", values.get("base_url"))
    roles = dict(body.get("roles") or {})
    try:
        main = llm.selected()
    except ValueError:
        main = None
    if main is None and not roles.get("default"):
        # Nothing active yet: the first provider that now has a key becomes main.
        first = next((k for k in (body.get("providers") or {})
                      if llm.PRESETS.get(str(k)) and provider_key(llm.PRESETS[str(k)])[0]), "")
        if first:
            roles["default"] = first
    for role, provider in roles.items():
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        provider = str(provider or "").strip()
        if provider == "":
            continue
        prefix = "MAILKEEP_LLM_" if role == "default" else f"MAILKEEP_LLM_{role.upper()}_"
        if provider == "-":
            for name in ("PROVIDER", "MODEL", "BASE_URL", "API_KEY"):
                if ui_override(prefix + name)[0]:
                    set_ui_override(prefix + name, "", persist=persist)
            continue
        preset = llm.PRESETS.get(provider)
        if preset is None:
            raise ValueError(f"unknown provider {provider!r}")
        key = provider_key(preset)[0]
        if not key:
            raise ValueError(f"{preset.label}: enter its API key first")
        model = setting(f"MAILKEEP_UI_{preset.key.upper()}_MODEL") or preset.model
        base_url = setting(f"MAILKEEP_UI_{preset.key.upper()}_BASE_URL") or preset.base_url
        if not model or not base_url:
            raise ValueError(f"{preset.label}: enter a model and base URL first")
        set_ui_override(prefix + "PROVIDER", preset.key, persist=persist)
        set_ui_override(prefix + "MODEL", model, persist=persist)
        set_ui_override(prefix + "BASE_URL", base_url, persist=persist)
        # Pin this provider's own key so a generic .env key never leaks to it.
        set_ui_override(prefix + "API_KEY", key, persist=persist)
