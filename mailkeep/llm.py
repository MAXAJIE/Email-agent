"""Model provider settings for the OpenMinion agent.

Everything is configured in `.openminion/gmail.env`; nothing else to edit:

    MAILKEEP_LLM_PROVIDER=openrouter    # openrouter | gemini | portal | openai | nvidia | custom
    MAILKEEP_LLM_MODEL=openai/gpt-4.1-mini
    MAILKEEP_LLM_API_KEY=sk-or-...      # or the provider's own name (OPENROUTER_API_KEY, ...)
    MAILKEEP_LLM_BASE_URL=              # optional; required for "custom" unless the key is recognised
    MAILKEEP_LLM_TIMEOUT=120            # optional; seconds per model call

Short aliases LLM_PROVIDER / LLM_MODEL / LLM_API_KEY / LLM_BASE_URL also work.
Without MAILKEEP_LLM_PROVIDER the first provider whose key is present wins
(OpenRouter, Gemini, Cortensor Portal, OpenAI).

Mailkeep then writes `.openminion/mailkeep-agent.json` itself, because:

* `config init` writes `providers` *and* `system.providers`; the second one
  silently wins (report OM-02). The generated file has no `system` block.
* An agent without `default_channel` fails every turn with
  `inbound authenticity denied (reason=invalid_channel, mode=warn)`.
* OpenMinion reads `api_key_env` from the process environment only, not from
  gmail.env, so `export_key()` copies it over.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .config import ROOT, env_files, env_values, setting

GENERATED_CONFIG = ROOT / ".openminion" / "mailkeep-agent.json"


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    slot: str  # OpenMinion provider name
    base_url: str
    key_env: str
    model: str
    timeout_seconds: int = 120
    # Other spellings people use for the same key.
    alt_key_envs: tuple[str, ...] = ()
    profile: str = "default"
    api_key: str = field(default="", repr=False)


PRESETS: dict[str, Preset] = {
    # Order = auto-detect priority when no provider is named.
    "openrouter": Preset(
        "openrouter", "OpenRouter", "openrouter", "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY", "openai/gpt-4.1-mini",
        alt_key_envs=("OPENROUTER_KEY", "OPEN_ROUTER_API_KEY", "OPENROUTER_TOKEN"),
    ),
    "gemini": Preset(
        "gemini", "Gemini", "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY", "gemini-2.5-flash",
        alt_key_envs=("GOOGLE_API_KEY",),
    ),
    "portal": Preset(
        "portal", "Cortensor Portal", "openai", "https://api.cortensor.app/v1",
        "CORTENSOR_API_KEY", "oss-20b", timeout_seconds=480,
    ),
    "openai": Preset(
        "openai", "OpenAI", "openai", "https://api.openai.com/v1",
        "OPENAI_API_KEY", "gpt-4.1-mini",
    ),
    "nvidia": Preset(
        "nvidia", "NVIDIA NIM", "openai", "https://integrate.api.nvidia.com/v1",
        "NVIDIA_API_KEY", "",
        alt_key_envs=("NVIDIA_NIM_API_KEY", "NIM_API_KEY"),
    ),
    # Any OpenAI-compatible endpoint: set MAILKEEP_LLM_BASE_URL + model + key.
    # When the base URL is omitted, a recognisable key (e.g. nvapi-... for
    # NVIDIA NIM) supplies it, so "custom" never aborts before a request.
    "custom": Preset(
        "custom", "Custom (OpenAI-compatible)", "openai", "",
        "MAILKEEP_LLM_API_KEY", "",
        alt_key_envs=("OPENAI_API_KEY",),
    ),
}
ALIASES = {
    "cortensor": "portal",
    "google": "gemini",
    "or": "openrouter",
    "nim": "nvidia",
    "nvidia nim": "nvidia",
}
# Recognisable key shapes, most specific first. Used when the provider is not
# named, so a key alone (under any variable name) still selects its provider,
# and to fill in a missing base URL for the "custom" provider.
KEY_PREFIXES = (
    ("sk-or-", "openrouter"),
    ("nvapi-", "nvidia"),
    ("AIza", "gemini"),
    ("sk-", "openai"),
)


def _provider_for_key(value: str) -> str:
    return next((name for prefix, name in KEY_PREFIXES if value.startswith(prefix)), "")


def _key_by_prefix(provider: str) -> str:
    """A value in the process env or env files that looks like `provider`'s key."""
    candidates = list(env_values().values()) + list(os.environ.values())
    return next((v.strip() for v in candidates if _provider_for_key(v.strip()) == provider), "")


def _opt(name: str) -> str:
    """MAILKEEP_LLM_<NAME>, falling back to the short LLM_<NAME> alias."""
    return setting(f"MAILKEEP_LLM_{name}") or setting(f"LLM_{name}")


def _api_key(preset: Preset) -> str:
    if preset.profile != "default":
        return preset.api_key
    value = _opt("API_KEY") or setting(preset.key_env)
    for name in preset.alt_key_envs:
        value = value or setting(name)
    if not value and preset.key != "custom":
        value = _key_by_prefix(preset.key)
    return value


def _inferred_base_url(key: str) -> str:
    """Base URL of the provider whose key shape matches `key` ("" if unknown).

    Only vendor-specific prefixes qualify. A bare ``sk-`` key is used by many
    vendors and local proxies, so it must not silently send traffic to OpenAI.
    """
    provider = _provider_for_key(key.strip())
    if provider in {"", "openai"}:
        return ""
    preset = PRESETS.get(provider)
    return preset.base_url if preset is not None else ""


def _timeout(default: int) -> int:
    raw = _opt("TIMEOUT").strip()
    try:
        value = int(float(raw)) if raw else default
    except ValueError:
        raise ValueError(f"MAILKEEP_LLM_TIMEOUT={raw!r} is not a number of seconds") from None
    return value if value > 0 else default


def by_name(name: str) -> Preset | None:
    """Preset for an OpenMinion provider name taken from the user's agents.json."""
    name = ALIASES.get(name.strip().lower(), name.strip().lower())
    return PRESETS.get(name)


def selected(profile: str = "default") -> Preset | None:
    """The configured provider (with model/base URL overrides applied)."""
    if profile != "default":
        return _selected_profile(profile)
    key = _opt("PROVIDER").strip().lower()
    key = ALIASES.get(key, key)
    if key:
        if key not in PRESETS:
            raise ValueError(
                f"MAILKEEP_LLM_PROVIDER={key!r} is unknown; use one of {', '.join(PRESETS)}"
            )
        preset = PRESETS[key]
    else:
        preset = next((p for p in PRESETS.values() if p.key != "custom" and any(
            setting(n) for n in (p.key_env, *p.alt_key_envs))), None)
        generic = _opt("API_KEY")
        if preset is None and generic:
            # Base URL given -> any OpenAI-compatible endpoint; else guess from the key.
            guess = "custom" if _opt("BASE_URL") else _provider_for_key(generic)
            if not guess:
                raise ValueError(
                    "MAILKEEP_LLM_API_KEY is set but MAILKEEP_LLM_PROVIDER is not; "
                    f"set it to one of {', '.join(PRESETS)}"
                )
            preset = PRESETS[guess]
        if preset is None:
            preset = next((p for p in PRESETS.values() if p.key != "custom"
                           and _key_by_prefix(p.key)), None)
        if preset is None:
            return None
    base_url = _opt("BASE_URL") or preset.base_url
    if not base_url and preset.key == "custom":
        # OPENAI_BASE_URL is the conventional name for a proxied endpoint.
        base_url = setting("OPENAI_BASE_URL")
    if not base_url and preset.key == "custom":
        base_url = _inferred_base_url(_api_key(preset))
    preset = replace(
        preset,
        model=_opt("MODEL") or preset.model,
        base_url=base_url,
        timeout_seconds=_timeout(preset.timeout_seconds),
    )
    if not preset.model:
        raise ValueError(f"{preset.label}: set MAILKEEP_LLM_MODEL")
    if not preset.base_url:
        raise ValueError(
            f"{preset.label}: set MAILKEEP_LLM_BASE_URL (the OpenAI-compatible "
            "endpoint ending in /v1, e.g. https://integrate.api.nvidia.com/v1)"
        )
    return preset


def build_config(preset: Preset) -> dict:
    provider = {
        "model": preset.model,
        "base_url": preset.base_url,
        "api_key_env": preset.key_env,
        "timeout_seconds": preset.timeout_seconds,
    }
    if preset.slot == "openrouter":
        provider["app_name"] = "mailkeep"
    return {
        "agents": {
            "openminion": {
                "name": "openminion",
                "provider": preset.slot,
                "default_channel": "console",
            }
        },
        "providers": {preset.slot: provider},
    }


def export_key(preset: Preset) -> bool:
    """Put the key where OpenMinion looks (process env, under key_env)."""
    value = _api_key(preset)
    if value:
        os.environ[preset.key_env] = value
    return bool(value)


def write_config(preset: Preset, path: Path | None = None) -> Path:
    # Resolve the default at call time: the module global is what tests and
    # embedders override, and a bound default silently ignored it.
    path = path or GENERATED_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_config(preset), indent=2), encoding="utf-8")
    return path


def describe() -> list[str]:
    """Startup diagnostics. Never prints a key, only whether one was found."""
    files = [f"{p} ({'found' if p.is_file() else 'missing'})" for p in env_files()]
    lines = ["env files: " + "; ".join(files)]
    try:
        preset = selected()
    except ValueError as exc:
        return lines + [f"LLM config error: {exc}"]
    if preset is None:
        return lines + ["LLM: none (no MAILKEEP_LLM_PROVIDER and no provider API key found)"]
    key = _api_key(preset)
    names = list(dict.fromkeys(("MAILKEEP_LLM_API_KEY", preset.key_env, *preset.alt_key_envs)))
    lines.append(f"LLM provider: {preset.label}  model: {preset.model}  base_url: {preset.base_url}")
    lines.append(
        f"LLM api key: {'present (' + str(len(key)) + ' chars)' if key else 'MISSING'}"
        f"  ({' or '.join(names)})"
    )
    return lines


PROFILES = ("default", "fast", "reasoning", "draft", "classifier")


def _selected_profile(profile: str) -> Preset | None:
    """A task-specific override of the default provider.

    Each profile reads MAILKEEP_LLM_<PROFILE>_PROVIDER / _MODEL / _API_KEY /
    _BASE_URL / _TIMEOUT. A model-only override inherits the default provider,
    endpoint and key; a different provider never receives the default's key.
    """
    if profile not in PROFILES:
        raise ValueError(f"Unknown model profile: {profile}")
    prefix = f"MAILKEEP_LLM_{profile.upper()}_"
    opt = lambda name: setting(prefix + name)
    base = selected()
    provider = opt("PROVIDER").lower()
    provider = ALIASES.get(provider, provider)
    if provider:
        preset = by_name(provider)
        if preset is None:
            raise ValueError(f"{prefix}PROVIDER is unknown")
    else:
        preset = base
    if preset is None:
        return None
    same_provider = base is not None and base.key == preset.key
    endpoint = opt("BASE_URL") or (base.base_url if same_provider else preset.base_url)
    model = opt("MODEL") or (base.model if same_provider else preset.model)
    secret = opt("API_KEY")
    if not secret and same_provider:
        secret = _api_key(base)
    if not secret:
        secret = setting(preset.key_env)
        for name in preset.alt_key_envs:
            secret = secret or setting(name)
    # No generic-key or prefix scan across other profiles: never send one
    # vendor's credentials to a different vendor's endpoint.
    timeout = positive_seconds(
        prefix + "TIMEOUT", base.timeout_seconds if same_provider else preset.timeout_seconds
    )
    if not model or not endpoint:
        raise ValueError(f"{prefix}MODEL and {prefix}BASE_URL must resolve to non-empty values")
    return replace(
        preset,
        profile=profile,
        model=model,
        base_url=endpoint,
        key_env=f"MAILKEEP_RUNTIME_{profile.upper()}_API_KEY",
        api_key=secret,
        timeout_seconds=int(timeout),
    )


def positive_seconds(name: str, default: float) -> float:
    import math

    raw = setting(name)
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        raise ValueError(f"{name} must be positive seconds") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite positive seconds")
    return value


def concurrency() -> int:
    raw = setting("MAILKEEP_MAX_AGENTS") or "3"
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("MAILKEEP_MAX_AGENTS must be an integer from 1 to 16") from None
    if not 1 <= value <= 16:
        raise ValueError("MAILKEEP_MAX_AGENTS must be from 1 to 16")
    return value


def profile_for(text: str) -> str:
    """Pick the model profile for a user message."""
    import re

    text = text.lower()
    if re.search(r"\b(draft|reply|compose|write)\b", text):
        return "draft"
    if re.search(r"\b(reason|plan|compare|explain|prioritize|analyse|analyze)\b", text):
        return "reasoning"
    if re.search(r"\b(classify|categorize|triage)\b", text):
        return "classifier"
    if re.search(r"\b(find|search|open|list|labels)\b", text):
        return "fast"
    return "default"
