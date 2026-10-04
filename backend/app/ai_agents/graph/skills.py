"""Per-agent skill files \u2014 richer behavioral guidance (procedure, do's/don'ts, example
Q&A) than the one-line role description in `graph/agents.py`. See
`docs/Kuber_Agentic_Implementation_Plan.md` \u00a75 for why this is a file, not a longer
string: it's editable/auditable by a compliance team without a code deploy. Loaded once
per call (no hot-reload for v1 \u2014 see the plan's \u00a78 open items)."""
from __future__ import annotations

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def load_skill(agent_key: str) -> str:
    path = _SKILLS_DIR / f"{agent_key}.md"
    if not path.exists():
        raise FileNotFoundError(f"No skill file for agent {agent_key!r} \u2014 expected {path}")
    return path.read_text(encoding="utf-8").strip()
