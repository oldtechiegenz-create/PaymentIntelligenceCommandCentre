"""Deterministic evidence-completeness scoring — ported from the POC's evidence panel
checks (Kuber_V4_Complete_Rebuild_Prompt.txt §14). Computed purely in Python from the
payment's real fields. Note this covers *evidence* only: the answer's confidence is
reported by the LLM itself (see `SummarizerOutput.confidence`), not computed here."""
from __future__ import annotations

from typing import Any


def compute_evidence(payment: dict[str, Any], hops: list[dict[str, Any]]) -> dict[str, bool]:
    """One True/False per evidence category, exactly mirroring the POC's evidence
    panel checks (identity+UETR-or-domestic, ISO triad, >=2 lineage hops, etc.)."""
    return {
        "IDENTITY": bool(payment.get("paymentId")) and bool(payment.get("uetr") or payment.get("domain") == "DOME"),
        "ISO": bool(payment.get("messageType") and payment.get("instructionId") and payment.get("endToEndId")),
        "LINEAGE": len(hops) >= 2,
        "STATE": bool(payment.get("status") and (payment.get("intermediateStatus") or payment.get("slaState"))),
        "RISK": bool(payment.get("screening")),
        "LIQUIDITY": bool(payment.get("liquidityState")),
        "OWNER": bool(payment.get("owner") and payment.get("paymentInitiationDept")),
    }
