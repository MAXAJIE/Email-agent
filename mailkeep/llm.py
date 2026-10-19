"""Model provider presets for the OpenMinion agent (Portal, Gemini, OpenRouter).

Set in `.openminion/gmail.env`:

    MAILKEEP_LLM_PROVIDER=portal        # portal | gemini | openrouter
    CORTENSOR_API_KEY=...               # or GEMINI_API_KEY / OPENROUTER_API_KEY
    MAILKEEP_LLM_MODEL=...              # optional override

Mailkeep then writes `.openminion/mailkeep-agent.json` itself, because:

* OpenMinion 0.1.7 has ONE `openai` provider slot. Cortensor Portal and Gemini
  are both "openai-compatible", so switching means rewriting that slot.
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
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT, setting

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
    # Values from openminion/modules/llm/setup_catalog.py (0.1.7).
    "portal": Preset(
        "portal", "Cortensor Portal", "openai", "https://api.cortensor.app/v1",
        "CORTENSOR_API_KEY", "oss-20b", timeout_seconds=480,
    ),
    "gemini": Preset(
        "gemini", "Gemini", "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY", "gemini-2.5-flash",
    ),
    "openrouter": Preset(
        "openrouter", "OpenRouter", "openrouter", "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY", "openai/gpt-4.1-mini",
    ),
}


def selected() -> Preset | None:
    """The configured preset, or None when the user did not pick one."""
    key = setting("MAILKEEP_LLM_PROVIDER").strip().lower()
    if not key:
        return None
    if key not in PRESETS:
        raise ValueError(f"MAILKEEP_LLM_PROVIDER={key!r}; use one of {', '.join(PRESETS)}")
    return PRESETS[key]


def build_config(preset: Preset) -> dict:
    return {
        "agents": {
            "openminion": {
                "name": "openminion",
                "provider": preset.slot,
                "default_channel": "console",
            }
        },
        "providers": {
            preset.slot: {
                "model": setting("MAILKEEP_LLM_MODEL") or preset.model,
                "base_url": preset.base_url,
                "api_key_env": preset.key_env,
                "timeout_seconds": preset.timeout_seconds,
            }
        },
    }


def export_key(preset: Preset) -> bool:
    value = setting(preset.key_env)
    if value:
        os.environ.setdefault(preset.key_env, value)
    return bool(value)


def write_config(preset: Preset, path: Path = GENERATED_CONFIG) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_config(preset), indent=2), encoding="utf-8")
    return path
