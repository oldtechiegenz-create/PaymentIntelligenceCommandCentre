"""Skill-file loader \u2014 every specialist must have a matching skill file (fails loudly,
same spirit as the tool-assignment invariant), and load_skill() must raise clearly for
an unknown key rather than silently returning empty text."""
from __future__ import annotations

import pytest
from pathlib import Path

from app.ai_agents.graph.agents import SPECIALISTS
from app.ai_agents.graph.skills import load_skill


def test_every_specialist_has_a_skill_file() -> None:
    for key in SPECIALISTS:
        content = load_skill(key)
        assert content, f"{key!r}'s skill file is empty"


def test_load_skill_raises_for_unknown_agent_key() -> None:
    with pytest.raises(FileNotFoundError, match="not-a-real-agent"):
        load_skill("not-a-real-agent")


REQUIRED_SECTIONS = ("## Procedure", "## Do", "## Don't", "## Example")


def test_every_skill_has_the_required_structure_and_names_its_agent() -> None:
    for key, cfg in SPECIALISTS.items():
        content = load_skill(key)
        first_line = content.splitlines()[0]
        assert first_line.startswith("# ") and cfg["label"] in first_line, (
            f"{key!r}: title should be '# <label> \u2014 skill', got {first_line!r}"
        )
        missing = [h for h in REQUIRED_SECTIONS if h not in content]
        assert not missing, f"{key!r}'s skill file is missing section(s): {missing}"


def test_no_orphan_skill_files() -> None:
    skills_dir = Path(__file__).resolve().parent.parent / "app" / "ai_agents" / "skills"
    orphans = {f.stem for f in skills_dir.glob("*.md")} - set(SPECIALISTS)
    assert not orphans, f"skill file(s) with no matching SPECIALISTS entry: {sorted(orphans)}"
