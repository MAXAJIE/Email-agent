"""Scratch: report skill discovery + harness results for this workspace."""

from pathlib import Path

from openminion.modules.skill.diagnostics.harness import (
    discover_skill_roots,
    run_skill_harness,
)

root = Path(__file__).resolve().parents[1]
roots = discover_skill_roots(root)
print(f"root={root}")
print(f"discovered={len(roots)}")
for path in roots:
    print(f"  ROOT {path.relative_to(root).as_posix()}")

report = run_skill_harness(root)
print(
    f"ok={report.ok} total={report.total_skills} passed={report.passed_skills} "
    f"warnings={report.warning_count} errors={report.error_count}"
)
for result in report.results:
    name = Path(result.skill_root).name
    print(
        f"  {name}: ok={result.ok} steps={result.recipe_step_count} "
        f"tools={list(result.recipe_tool_bindings)}"
    )
    for warning in result.warnings:
        print(f"    WARN {warning}")
    for error in result.errors:
        print(f"    ERR  {error}")
    for key in result.unknown_front_matter_keys:
        print(f"    UNKNOWN_KEY {key}")

