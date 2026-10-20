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


def effective(profile: str) -> tuple[llm.Preset | None, str]:
    """(preset to use for `profile`, note). Falls back to the main model."""
    if profile == "default":
        try:
            return llm.selected(), ""
        except ValueError as exc:
            return None, str(exc)
    why = ""
    try:
        preset = llm.selected(profile)
    except ValueError as exc:
        preset, why = None, str(exc)
    if preset is not None and not llm._api_key(preset):
        preset, why = None, f"{preset.label} has no API key"
    if preset is not None:
        configured = bool(setting(f"MAILKEEP_LLM_{profile.upper()}_PROVIDER")
                          or setting(f"MAILKEEP_LLM_{profile.upper()}_MODEL"))
        health = _health.get(f"{preset.key}|{preset.model}")
        if not configured:
            return preset, "not set, uses the main model"
        if health is not None and health.state in ("offline", "auth_error"):
            preset, why = None, f"{preset.label} is {health.state}"
    if preset is not None:
        return preset, ""
    try:
        main = llm.selected()
    except ValueError:
        main = None
    if main is None:
        return None, (why + "; " if why else "") + "no main model either, rule parser answers"
    return main, (why + "; " if why else "") + "falls back to the main model"


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
    """Every supported provider: key present?, online?, model, roles it serves."""
    rows = _rows()

    def check(row):
        preset, key = row["preset"], row["key"]
        if not key:
            return fastllm.Health("not_configured", detail="no API key", checked_at=time.time())
        if not preset.base_url:
            return fastllm.Health("not_configured", detail="no base URL", checked_at=time.time())
        return fastllm.probe(_probe_preset(preset, key), timeout=timeout)

    if probe:
        with ThreadPoolExecutor(max_workers=max(1, len(rows))) as pool:
            results = list(pool.map(check, rows))
        with _cache_lock:
            for row, health in zip(rows, results):
                _health[f"{row['preset'].key}|{row['preset'].model}"] = health
    roles = {}
    for role in ROLES:
        preset, note = effective(role)
        roles[role] = {"purpose": ROLE_INFO[role], "provider": preset.key if preset else "",
                       "label": preset.label if preset else "rule parser",
                       "model": preset.model if preset else "", "note": note}
    providers = []
    for row in rows:
        preset = row["preset"]
        health = _health.get(f"{preset.key}|{preset.model}") or fastllm.Health(
            "not_configured" if not row["key"] else "unknown")
        providers.append({
            "key": preset.key, "label": preset.label, "model": preset.model,
            "base_url": preset.base_url, "key_present": bool(row["key"]),
            "key_source": row["source"], **health.public(),
            "roles": [r for r, info in roles.items() if info["provider"] == preset.key],
        })
    online = [p["key"] for p in providers if p["state"] == "online"]
    return {"providers": providers, "roles": roles, "online": online,
            "mode_hint": "openminion" if roles["default"]["provider"] else "rules"}


def startup_report() -> list[str]:
    """Console lines printed when localhost starts (no keys, only status)."""
    data = inventory(probe=True)
    lines = [f"providers online: {len(data['online'])}/{len(data['providers'])}"]
    for p in data["providers"]:
        roles = ", ".join(p["roles"]) or "-"
        lines.append(f"  {p['label']:<28} {p['state']:<15} model={p['model'] or '-'}  roles={roles}")
    for role, info in data["roles"].items():
        tail = f"  ({info['note']})" if info["note"] else ""
        lines.append(f"  role {role:<10} -> {info['label']} {info['model']}{tail}")
    if not data["roles"]["default"]["provider"]:
        lines.append("  no model configured: the rule parser answers. Add a key with the gear icon.")
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
