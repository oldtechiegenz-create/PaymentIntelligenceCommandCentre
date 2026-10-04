"""Scenario step lists, loaded from sim_scenario/sim_scenario_step (seeded in Phase 1) \u2014 never
a second, hardcoded Python copy of the same 4 scenarios. Also the per-step event builder and a
whole-scenario runner used by this phase's own tests and, later, the simulation ticker (Phase 4/7).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from app.engine.apply_event import actor_for, apply_event
from app.engine.latency import format_ms, load_latency_profile, step_latency


def load_scenario_steps(conn: sqlite3.Connection, scenario_code: str) -> list[dict]:
    rows = conn.execute(
        "SELECT step_no, state, description, iso_message FROM sim_scenario_step "
        "WHERE scenario_code = ? ORDER BY step_no",
        (scenario_code,),
    ).fetchall()
    return [dict(r) for r in rows]


def adapt_steps(steps: list[dict], payment: dict, scenario_code: str) -> list[dict]:
    """Domestic payments with no correspondent/intermediary skip those two HAPPY-path steps."""
    if scenario_code == "HAPPY" and not payment.get("correspondent") and not payment.get("intermediary"):
        return [s for s in steps if s["state"] not in ("CORRESPONDENT_PROCESSING", "IN_TRANSIT")]
    return steps


def ts_add(base: str, ms: int) -> str:
    dt = datetime.strptime(base, "%Y-%m-%d %H:%M:%S") + timedelta(milliseconds=ms)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def build_event(payment: dict, step: dict, seq: int, prev_state: str | None, event_ts: str, processing_ms: int, *, injected: bool = False) -> dict:
    state = step["state"]
    if state in ("REJECTED", "RETURNED"):
        reason = payment.get("status_reason") if payment.get("status_reason") not in (None, "\u2014") else "AC03 \u2014 Invalid Creditor Account Number"
    elif state == "FAILED":
        reason = "NACK_TIMEOUT \u2014 Failure injected" if injected else "NACK_TIMEOUT \u2014 Network SLA breached"
    elif state == "INVESTIGATION":
        reason = "Potential sanctions match" if "analyst" in step["description"] else "Correspondent ACK overdue"
    else:
        reason = "\u2014"
    return {
        "seq": seq + 1,
        "state": state,
        "prev_state": prev_state,
        "description": step["description"],
        "iso_message": step["iso_message"],
        "actor": actor_for(state, payment, prev_state),
        "reason": reason,
        "event_ts": event_ts,
        "processing_ms": processing_ms,
        "failure_injected": injected,
    }


def run_scenario(conn: sqlite3.Connection, payment: dict, scenario_code: str) -> tuple[dict, list[dict]]:
    """Run every step of a scenario against `payment` in one shot (used by seeding and tests).

    The live simulation ticker (Phase 4/7) will call the same per-step primitives
    (step_latency/build_event/apply_event) one tick at a time instead of all at once.
    """
    profile = load_latency_profile(conn)
    steps = adapt_steps(load_scenario_steps(conn, scenario_code), payment, scenario_code)
    original_message_type = payment.get("message_type")

    events: list[dict] = []
    cumulative_ms = 0
    prev_state: str | None = None
    for i, step in enumerate(steps):
        latency_ms = step_latency(
            profile, payment_id=payment["payment_id"], rail=payment.get("rail"),
            state=step["state"], step_index=i, prev_state=prev_state,
        )
        cumulative_ms += latency_ms
        event_ts = ts_add(payment["initiated_at"], cumulative_ms)
        event = build_event(payment, step, i, prev_state, event_ts, latency_ms)
        events.append(event)
        apply_event(payment, event, duration_text=format_ms(cumulative_ms), original_message_type=original_message_type)
        prev_state = step["state"]

    return payment, events
