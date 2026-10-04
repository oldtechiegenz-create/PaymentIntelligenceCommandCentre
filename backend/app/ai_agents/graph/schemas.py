"""Structured LLM outputs — enforced via with_structured_output(), not just prompted,
so routing/evidence-citation can't be silently dropped by whichever provider is active."""
from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, field_validator

from app.ai_agents.graph.agents import SPECIALISTS

EvidenceCategory = Literal["IDENTITY", "ISO", "LINEAGE", "STATE", "RISK", "LIQUIDITY", "OWNER"]
EVIDENCE_CATEGORIES: tuple[str, ...] = get_args(EvidenceCategory)

# Built from the roster, so adding a specialist to SPECIALISTS makes it routable with no
# second edit here (a hand-maintained Literal would silently lag behind the roster).
RoutingKey = Literal[tuple(SPECIALISTS)]  # type: ignore[valid-type]


class RouterOutput(BaseModel):
    routing_key: RoutingKey = Field(
        description="Which specialist should answer this question about the payment."
    )
    reasoning: str = Field(description="One short sentence on why this specialist was chosen.")


class SummarizerOutput(BaseModel):
    answer: str = Field(description="The final answer to the user's question, grounded only in the evidence gathered.")
    evidence_used: list[EvidenceCategory] = Field(
        description="Which evidence categories the answer actually drew on. Only these exact values are allowed: "
        + ", ".join(EVIDENCE_CATEGORIES)
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description=(
            "How confident you are in the answer, from 0.0 to 1.0, based only on how completely the "
            "tool results support it. Lower it when evidence is missing, a tool returned an error, "
            "or the payment is in a failed/exception state."
        ),
    )

    @field_validator("evidence_used", mode="before")
    @classmethod
    def _normalize_evidence(cls, v: Any) -> Any:
        """Tolerate harmless LLM drift ('risk', ' State ', duplicates) but never let an
        unknown category through: unrecognised values are dropped, not passed on."""
        if not isinstance(v, list):
            return v
        cleaned: list[str] = []
        for item in v:
            key = str(item).strip().upper()
            if key in EVIDENCE_CATEGORIES and key not in cleaned:
                cleaned.append(key)
        return cleaned
