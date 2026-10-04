"""Guided-simulation background sequencer \u2014 one asyncio task per running sim_run.

Ticks through a scenario's steps at the selected speed, calling process_event()
for each one (same shared pipeline the REST ingestion endpoint uses). Runs
independent of any open browser tab/WebSocket connection, matching the plan's
"survives independent of any single connection" requirement.

A run never mutates its source payment: Start clones the source into a shadow
row `SIM-{payment_id}` (`is_simulated=1`) and the ticker only ever writes to
the shadow. Reset is therefore a plain delete of the shadow, not a
snapshot/restore \u2014 see schema.sql's header note.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.db.connection import DB_PATH, get_connection
from app.engine.latency import format_ms, load_latency_profile, step_latency
from app.engine.state_machine import adapt_steps, load_scenario_steps
from app.realtime.publisher import publisher
from app.realtime.registry import registry
from app.seed.generator import PAYMENT_COLUMNS
from app.simulation.pipeline import process_event

EXCEPTION_STATES = {"REJECTED", "FAILED", "INVESTIGATION", "RETURNED", "CANCELLED"}
POLL_INTERVAL_S = 0.2

# run_id -> the concurrent.futures.Future returned by run_coroutine_threadsafe.
# In-process only (single dev/demo instance, matches realtime/registry.py).
RUNNING_TASKS: dict[str, object] = {}

# Test-only override: sim_start() runs in FastAPI's threadpool (sync route
# handler) and can't use the per-request get_db_connection dependency for a
# background task, so tests point this at a temp DB instead of the real one.
_db_path_override: Optional[Path] = None


def use_db_path(path: Optional[Path]) -> None:
    global _db_path_override
    _db_path_override = path


def shadow_id_for(payment_id: str) -> str:
    return f"SIM-{payment_id}"


def _new_run_id(conn: sqlite3.Connection) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"SIM-{today}-"
    row = conn.execute(
        "SELECT run_id FROM sim_run WHERE run_id LIKE ? ORDER BY run_id DESC LIMIT 1", (f"{prefix}%",)
    ).fetchone()
    next_n = int(row["run_id"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"{prefix}{next_n:04d}"


def _load_payment(conn: sqlite3.Connection, payment_id: str) -> dict:
    row = conn.execute("SELECT * FROM payment WHERE payment_id = ?", (payment_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown payment_id {payment_id!r}")
    return dict(row)


def _delete_shadow(conn: sqlite3.Connection, shadow_id: str) -> None:
    # payment_event / payment_agent_hop rows cascade via ON DELETE CASCADE.
    conn.execute("DELETE FROM payment WHERE payment_id = ? AND is_simulated = 1", (shadow_id,))


def _clone_payment_as_shadow(conn: sqlite3.Connection, source_id: str, shadow_id: str) -> None:
    source = _load_payment(conn, source_id)
    source["payment_id"] = shadow_id
    source["is_simulated"] = 1
    source["source_payment_id"] = source_id
    if source.get("uetr"):  # uetr is UNIQUE; the shadow needs its own distinct value
        source["uetr"] = f"{source['uetr']}-SIM"
    placeholders = ", ".join(["?"] * len(PAYMENT_COLUMNS))
    conn.execute(
        f"INSERT INTO payment ({', '.join(PAYMENT_COLUMNS)}) VALUES ({placeholders})",
        tuple(source.get(c) for c in PAYMENT_COLUMNS),
    )
    hops = conn.execute(
        "SELECT hop_seq, agent_role, bic FROM payment_agent_hop WHERE payment_id = ? ORDER BY hop_seq", (source_id,)
    ).fetchall()
    conn.executemany(
        "INSERT INTO payment_agent_hop (payment_id, hop_seq, agent_role, bic) VALUES (?, ?, ?, ?)",
        [(shadow_id, h["hop_seq"], h["agent_role"], h["bic"]) for h in hops],
    )


def start_run(conn: sqlite3.Connection, payment_id: str, scenario_code: str, speed_code: str) -> str:
    """Resume the payment's most recent paused run if its shadow is still alive, else start
    a fresh one \u2014 auto-replacing any stale (finished/orphaned) shadow for this payment. No
    run history is kept: a payment has at most one sim_run row at a time."""
    source = _load_payment(conn, payment_id)
    if source["is_simulated"]:
        raise ValueError(f"{payment_id!r} is already a simulated payment; simulate its source instead")

    shadow_id = shadow_id_for(payment_id)
    existing = conn.execute(
        "SELECT run_id FROM sim_run WHERE payment_id = ? AND mode = 'PAUSED' ORDER BY started_at DESC LIMIT 1",
        (payment_id,),
    ).fetchone()
    shadow_alive = conn.execute("SELECT 1 FROM payment WHERE payment_id = ?", (shadow_id,)).fetchone() is not None

    if existing and shadow_alive:
        run_id = existing["run_id"]
        conn.execute("UPDATE sim_run SET mode = 'RUNNING', speed_code = ? WHERE run_id = ?", (speed_code, run_id))
    else:
        _delete_shadow(conn, shadow_id)
        _clone_payment_as_shadow(conn, payment_id, shadow_id)
        conn.execute("DELETE FROM sim_run WHERE payment_id = ?", (payment_id,))
        run_id = _new_run_id(conn)
        conn.execute(
            "INSERT INTO sim_run (run_id, scenario_code, payment_id, shadow_payment_id, speed_code, mode, outcome, next_step_no) "
            "VALUES (?, ?, ?, ?, ?, 'RUNNING', 'NO RUN', 1)",
            (run_id, scenario_code, payment_id, shadow_id, speed_code),
        )
    conn.commit()

    if run_id not in RUNNING_TASKS or RUNNING_TASKS[run_id].done():
        loop = registry.loop
        if loop is None:
            raise RuntimeError("No event loop registered — app lifespan hasn't started (registry.set_loop never called)")
        RUNNING_TASKS[run_id] = asyncio.run_coroutine_threadsafe(_tick_loop(run_id), loop)
    return run_id


def pause_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute("UPDATE sim_run SET mode = 'PAUSED' WHERE run_id = ? AND mode = 'RUNNING'", (run_id,))
    conn.commit()


def inject_failure(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute("UPDATE sim_run SET failure_injected = 1 WHERE run_id = ? AND mode = 'RUNNING'", (run_id,))
    conn.commit()


def reset_run(conn: sqlite3.Connection, payment_id: str) -> None:
    """Cancel any active run for `payment_id` and delete both its shadow payment and its
    sim_run row — no run history is kept, so Reset fully clears the slate. The shadow must be
    deleted first: it cascades away the payment_event rows still referencing this sim_run via
    sim_run_id, which has no ON DELETE CASCADE of its own."""
    runs = conn.execute("SELECT run_id FROM sim_run WHERE payment_id = ?", (payment_id,)).fetchall()
    for run in runs:
        future = RUNNING_TASKS.pop(run["run_id"], None)
        if future is not None:
            future.cancel()  # best-effort — the row being deleted below is what the loop actually honors
    _delete_shadow(conn, shadow_id_for(payment_id))
    conn.execute("DELETE FROM sim_run WHERE payment_id = ?", (payment_id,))
    conn.commit()


def _reason_text_for_step(state: str, description: str, injected: bool) -> Optional[str]:
    if state in ("REJECTED", "RETURNED"):
        return "AC03 \u2014 Invalid Creditor Account Number"
    if state == "FAILED":
        return "NACK_TIMEOUT \u2014 Failure injected" if injected else "NACK_TIMEOUT \u2014 Network SLA breached"
    if state == "INVESTIGATION":
        return "SCREEN_MATCH \u2014 Potential sanctions match" if "analyst" in description else "CORR_ACK_OVERDUE \u2014 Correspondent acknowledgement overdue (SLA)"
    return None


async def _tick_loop(run_id: str) -> None:
    conn = get_connection(_db_path_override or DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        while True:
            run = conn.execute("SELECT * FROM sim_run WHERE run_id = ?", (run_id,)).fetchone()
            if run is None or run["mode"] in ("FINISHED", "RESET"):
                return
            if run["mode"] == "PAUSED":
                await asyncio.sleep(POLL_INTERVAL_S)
                continue

            shadow_id = run["shadow_payment_id"]
            payment = _load_payment(conn, shadow_id)
            profile = load_latency_profile(conn)
            steps = adapt_steps(load_scenario_steps(conn, run["scenario_code"]), payment, run["scenario_code"])
            next_step_no = run["next_step_no"]

            if run["failure_injected"] and payment["status"] not in ("COMPLETED", "REJECTED", "RETURNED", "FAILED"):
                delay_row = conn.execute("SELECT step_delay_ms FROM sim_speed WHERE speed_code = ?", (run["speed_code"],)).fetchone()
                await asyncio.sleep((delay_row["step_delay_ms"] if delay_row else 1700) / 1000)
                result = process_event(
                    conn, payment_id=shadow_id, state="FAILED", msg_type="pacs.002", tx_sts="RJCT",
                    description="Failure injected by operator", reason_text_override=_reason_text_for_step("FAILED", "", True),
                    failure_injected=True, sim_run_id=run_id, processing_ms=delay_row["step_delay_ms"] if delay_row else 1700,
                    source="SIMULATION",
                )
                conn.execute(
                    "UPDATE sim_run SET mode='FINISHED', outcome=?, event_count=event_count+1, exception_count=exception_count+1, "
                    "finished_at=datetime('now') WHERE run_id=?",
                    (result["payment"]["status"], run_id),
                )
                conn.commit()
                publisher.notify(result["notification"])
                return

            if next_step_no > len(steps):
                conn.execute("UPDATE sim_run SET mode='FINISHED', outcome=?, finished_at=datetime('now') WHERE run_id=?", (payment["status"], run_id))
                conn.commit()
                return

            step = steps[next_step_no - 1]
            delay_row = conn.execute("SELECT step_delay_ms FROM sim_speed WHERE speed_code = ?", (run["speed_code"],)).fetchone()
            await asyncio.sleep((delay_row["step_delay_ms"] if delay_row else 1700) / 1000)

            latency_ms = step_latency(
                profile, payment_id=payment["payment_id"], rail=payment.get("rail"),
                state=step["state"], step_index=next_step_no - 1, prev_state=payment.get("sim_state"),
            )
            cumulative_ms = run["simulated_latency_ms"] + latency_ms
            reason_override = _reason_text_for_step(step["state"], step["description"], False)
            result = process_event(
                conn, payment_id=shadow_id, state=step["state"], msg_type=step["iso_message"].split(" ")[0] if step["iso_message"] != "\u2014" else "pain.001",
                tx_sts=step["iso_message"].split(" ")[1] if " " in step["iso_message"] else None,
                description=step["description"], reason_text_override=reason_override,
                sim_run_id=run_id, processing_ms=latency_ms, duration_text=format_ms(cumulative_ms), source="SIMULATION",
            )
            is_exception = step["state"] in EXCEPTION_STATES
            is_iso = step["iso_message"] != "\u2014"
            is_final = next_step_no >= len(steps)
            conn.execute(
                "UPDATE sim_run SET next_step_no = next_step_no + 1, event_count = event_count + 1, "
                "iso_message_count = iso_message_count + ?, exception_count = exception_count + ?, "
                "simulated_latency_ms = simulated_latency_ms + ?, "
                "mode = CASE WHEN ? THEN 'FINISHED' ELSE mode END, "
                "outcome = CASE WHEN ? THEN ? ELSE outcome END, "
                "finished_at = CASE WHEN ? THEN datetime('now') ELSE finished_at END "
                "WHERE run_id = ?",
                (int(is_iso), int(is_exception), latency_ms, is_final, is_final, result["payment"]["status"], is_final, run_id),
            )
            conn.commit()
            publisher.notify(result["notification"])
    except asyncio.CancelledError:
        pass
    finally:
        conn.close()
        RUNNING_TASKS.pop(run_id, None)
