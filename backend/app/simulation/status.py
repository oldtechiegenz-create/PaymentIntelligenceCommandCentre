"""Read-only views for the Simulation Engine screen: scenario/speed catalogues and the
current live status of a payment's guided simulation (its active run, if any, and that
run's shadow payment and event stream). No run history is retained — a payment has at
most one sim_run row at a time."""
from __future__ import annotations

import sqlite3
from typing import Optional

from app.engine.state_machine import adapt_steps, load_scenario_steps
from app.payments.detail import get_payment_detail
from app.simulation.ticker import shadow_id_for


def list_scenarios(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT scenario_code, scenario_name, description, expected_outcome FROM sim_scenario ORDER BY scenario_code"
    ).fetchall()
    scenarios = []
    for r in rows:
        steps = load_scenario_steps(conn, r["scenario_code"])
        scenarios.append({
            "scenarioCode": r["scenario_code"],
            "scenarioName": r["scenario_name"],
            "description": r["description"],
            "expectedOutcome": r["expected_outcome"],
            "steps": [
                {"stepNo": s["step_no"], "state": s["state"], "description": s["description"], "isoMessage": s["iso_message"]}
                for s in steps
            ],
        })
    return scenarios


def list_speeds(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT speed_code, step_delay_ms FROM sim_speed ORDER BY step_delay_ms DESC").fetchall()
    return [{"speedCode": r["speed_code"], "stepDelayMs": r["step_delay_ms"]} for r in rows]


def _run_dict(r: sqlite3.Row) -> dict:
    return {
        "runId": r["run_id"],
        "scenarioCode": r["scenario_code"],
        "speedCode": r["speed_code"],
        "mode": r["mode"],
        "outcome": r["outcome"],
        "nextStepNo": r["next_step_no"],
        "failureInjected": bool(r["failure_injected"]),
        "eventCount": r["event_count"],
        "isoMessageCount": r["iso_message_count"],
        "exceptionCount": r["exception_count"],
        "simulatedLatencyMs": r["simulated_latency_ms"],
        "startedAt": r["started_at"],
        "finishedAt": r["finished_at"],
    }


def get_sim_status(conn: sqlite3.Connection, payment_id: str, scenario_code: Optional[str] = None) -> Optional[dict]:
    source_row = conn.execute("SELECT is_simulated FROM payment WHERE payment_id = ?", (payment_id,)).fetchone()
    if source_row is None:
        return None

    history_rows = conn.execute(
        "SELECT * FROM sim_run WHERE payment_id = ? ORDER BY started_at DESC LIMIT 1", (payment_id,)
    ).fetchall()
    run = _run_dict(history_rows[0]) if history_rows else None

    shadow_id = shadow_id_for(payment_id)
    shadow_detail = get_payment_detail(conn, shadow_id)

    # A run is only "active" (drives the Control Plane's live state machine/banner/event
    # stream) while its shadow still exists. Reset deletes both the shadow and the sim_run
    # row together, so this guard mainly protects against a shadow being mid-recreation.
    active_run = run if shadow_detail is not None else None

    effective_scenario = scenario_code or (active_run["scenarioCode"] if active_run else "HAPPY")
    source_payment = conn.execute("SELECT * FROM payment WHERE payment_id = ?", (payment_id,)).fetchone()
    steps = adapt_steps(load_scenario_steps(conn, effective_scenario), dict(source_payment), effective_scenario)
    step_views = [
        {"stepNo": i + 1, "state": s["state"], "description": s["description"], "isoMessage": s["iso_message"]}
        for i, s in enumerate(steps)
    ]

    events = []
    if shadow_detail is not None and active_run is not None:
        event_rows = conn.execute(
            "SELECT seq, state, prev_state, description, iso_message, actor, reason_code, reason_text, "
            "event_ts, processing_ms FROM payment_event WHERE payment_id = ? AND sim_run_id = ? ORDER BY seq",
            (shadow_id, active_run["runId"]),
        ).fetchall()
        events = [dict(e) for e in event_rows]

    return {
        "sourcePaymentId": payment_id,
        "shadowPaymentId": shadow_id if shadow_detail is not None else None,
        "shadowPayment": shadow_detail["payment"] if shadow_detail is not None else None,
        "hops": shadow_detail["hops"] if shadow_detail is not None else [],
        "activeRun": active_run,
        "scenarioCode": effective_scenario,
        "steps": step_views,
        "events": events,
    }
