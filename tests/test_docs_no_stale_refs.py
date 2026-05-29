"""Guard against doc/skill drift.

Agent-facing docs must not reference removed tools, the retired static tool
registry, or link to skill directories that don't exist. These files are loaded
as authoritative context — skills/ feed the plan-data and model_generator LLM
prompts, and CLAUDE.md / copilot-instructions guide coding agents — so stale
references silently degrade behaviour.

The FORBIDDEN entries are deliberately specific (CLI flags, link forms, image
paths) so legitimate prose does not false-positive: e.g. the README scope note
that *mentions* the left-behind tools, and the accurate "the old
create-temporary-model CLI has been removed" notes, are fine.

Update FORBIDDEN only if a tool/command is genuinely reintroduced.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FORBIDDEN = [
    "eis-intervals",             # removed console script
    "eis-reduce-events",         # removed console script
    "iceberg-packager",          # removed console script
    "--list-tools",              # retired registry browser
    "--help-tool",               # retired registry browser
    "--show-data",               # never-shipped command
    "registry.py",               # retired module
    "skills/time-resolved",      # skill dir does not exist
    "skills/data-packaging",     # skill dir does not exist
    "ghcr.io/mdoucet/analyzer",  # old image namespace (now neutrons-ai/nr-analyzer)
]


def _targets():
    files = list((REPO / "docs").glob("*.md"))
    files += list((REPO / "analyzer_tools" / "skills").rglob("SKILL.md"))
    for extra in ("CLAUDE.md", "README.md", ".github/copilot-instructions.md"):
        p = REPO / extra
        if p.exists():
            files.append(p)
    return sorted(files)


def test_no_stale_tool_or_registry_references():
    violations = []
    for f in _targets():
        for i, line in enumerate(f.read_text().splitlines(), 1):
            for tok in FORBIDDEN:
                if tok in line:
                    violations.append(f"{f.relative_to(REPO)}:{i}: {tok!r} in: {line.strip()}")
    assert not violations, "Stale doc/skill references found:\n" + "\n".join(violations)


def test_targets_actually_scanned():
    # Sanity: the scan must cover the agent-facing surface, not silently no-op.
    names = {p.name for p in _targets()}
    assert "CLAUDE.md" in names
    assert any(p.parent.name == "distributable" for p in _targets())
