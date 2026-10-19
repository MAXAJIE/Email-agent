"""Scratch: verify recipe tool binding and UTF-8 integrity of the new skills."""

import json
import sys
from pathlib import Path

from openminion.modules.skill.runtime.skill import Skill

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

GMAIL_TOOLS = [
    "mcp.workspace.search_gmail_messages",
    "mcp.workspace.get_gmail_messages_content_batch",
    "mcp.workspace.get_gmail_message_content",
    "mcp.workspace.get_gmail_thread_content",
    "mcp.workspace.list_gmail_labels",
    "mcp.workspace.modify_gmail_message_labels",
    "mcp.workspace.batch_modify_gmail_message_labels",
    "mcp.workspace.draft_gmail_message",
    "mcp.workspace.get_gmail_attachment_content",
    "file.write",
    "file.list_dir",
]

out: list[str] = []

for name in ("email-triage", "email-draft", "email-organize"):
    path = SKILLS / name / "SKILL.md"
    ctl = Skill(known_tools=GMAIL_TOOLS)
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(path, scope="project")
        package = ctl.get_skill(skill_id, version_hash)
    finally:
        ctl.close()

    out.append(f"=== {name} ===")
    out.append(f"skill_id={package.skill_id} risk={package.risk_class}")
    out.append(f"tools={package.tools}")
    out.append(f"warnings={warnings}")
    if package.recipe:
        bound = [
            (s.step_id, s.tool_id) for s in package.recipe.steps
        ]
        out.append(f"steps={len(bound)}")
        for step_id, tool_id in bound:
            out.append(f"  {step_id} -> {tool_id}")
    else:
        out.append("recipe=None")

    # UTF-8 integrity: the bucket names must survive ingest unchanged.
    body = "\n".join(package.sections.values())
    for token in ("重要", "需跟进", "可忽略"):
        out.append(f"  contains {token!r}: {token in body}")
    out.append("")

report = ROOT / ".tmp-sdk" / "verify-skills.txt"
report.write_text("\n".join(out), encoding="utf-8")
print("\n".join(out))
sys.exit(0)
