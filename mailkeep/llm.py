"""Model provider settings for the OpenMinion agent.

Everything is configured in `.openminion/gmail.env`; nothing else to edit:

    MAILKEEP_LLM_PROVIDER=openrouter    # openrouter | gemini | portal | openai | custom
    MAILKEEP_LLM_MODEL=openai/gpt-4.1-mini
    MAILKEEP_LLM_API_KEY=sk-or-...      # or the provider's own name (OPENROUTER_API_KEY, ...)
    MAILKEEP_LLM_BASE_URL=              # optional; required for "custom"

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
from dataclasses import dataclass, replace
from pathlib import Path

from .config import ENV_FILE, FALLBACK_ENV_FILE, ROOT, setting

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


PRESETS: dict[str, Preset] = {
    # Order = auto-detect priority when no provider is named.
    "openrouter": Preset(
        "openrouter", "OpenRouter", "openrouter", "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY", "openai/gpt-4.1-mini",
    ),
    "gemini": Preset(
        "gemini", "Gemini", "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY", "gemini-2.5-flash",
    ),
    "portal": Preset(
        "portal", "Cortensor Portal", "openai", "https://api.cortensor.app/v1",
        "CORTENSOR_API_KEY", "oss-20b", timeout_seconds=480,
    ),
    "openai": Preset(
        "openai", "OpenAI", "openai", "https://api.openai.com/v1",
        "OPENAI_API_KEY", "gpt-4.1-mini",
    ),
    # Any OpenAI-compatible endpoint: set MAILKEEP_LLM_BASE_URL + model + key.
    "custom": Preset(
        "custom", "Custom (OpenAI-compatible)", "openai", "",
        "MAILKEEP_LLM_API_KEY", "",
    ),
}
ALIASES = {"cortensor": "portal", "google": "gemini", "or": "openrouter"}


def _opt(name: str) -> str:
    """MAILKEEP_LLM_<NAME>, falling back to the short LLM_<NAME> alias."""
    return setting(f"MAILKEEP_LLM_{name}") or setting(f"LLM_{name}")


def _api_key(preset: Preset) -> str:
    return _opt("API_KEY") or setting(preset.key_env)


def selected() -> Preset | None:
    """The configured provider (with model/base URL overrides applied)."""
    key = _opt("PROVIDER").strip().lower()
    key = ALIASES.get(key, key)
    if key:
        if key not in PRESETS:
            raise ValueError(
                f"MAILKEEP_LLM_PROVIDER={key!r} is unknown; use one of {', '.join(PRESETS)}"
            )
        preset = PRESETS[key]
    else:
        preset = next((p for p in PRESETS.values() if setting(p.key_env)), None)
        if preset is None and _opt("API_KEY"):
            raise ValueError("MAILKEEP_LLM_API_KEY is set but MAILKEEP_LLM_PROVIDER is not")
        if preset is None:
            return None
    preset = replace(
        preset,
        model=_opt("MODEL") or preset.model,
        base_url=_opt("BASE_URL") or preset.base_url,
    )
    if not preset.model:
        raise ValueError(f"{preset.label}: set MAILKEEP_LLM_MODEL")
    if not preset.base_url:
        raise ValueError(f"{preset.label}: set MAILKEEP_LLM_BASE_URL")
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


def write_config(preset: Preset, path: Path = GENERATED_CONFIG) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_config(preset), indent=2), encoding="utf-8")
    return path


def describe() -> list[str]:
    """Startup diagnostics. Never prints a key, only whether one was found."""
    files = [f"{p} ({'found' if p.is_file() else 'missing'})" for p in (ENV_FILE, FALLBACK_ENV_FILE)]
    lines = ["env files: " + "; ".join(files)]
    try:
        preset = selected()
    except ValueError as exc:
        return lines + [f"LLM config error: {exc}"]
    if preset is None:
        return lines + ["LLM: none (no MAILKEEP_LLM_PROVIDER and no provider API key found)"]
    key = _api_key(preset)
    lines.append(f"LLM provider: {preset.label}  model: {preset.model}  base_url: {preset.base_url}")
    lines.append(
        f"LLM api key: {'present (' + str(len(key)) + ' chars)' if key else 'MISSING'}"
        f"  (MAILKEEP_LLM_API_KEY or {preset.key_env})"
    )
    return lines
