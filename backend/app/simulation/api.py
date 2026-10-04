"""Live ingestion + simulation control REST API.

POST /events is the "showcase" endpoint: push one ISO-shaped event, it flows
through the shared pipeline, the DB commits, then (and only then) the change
is broadcast over the WebSocket. POST /sim/... drives the guided-simulation
ticker (app/simulation/ticker.py) using the same scenario step lists as
Phase 2's engine tests.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.db.connection import get_db_connection
from app.realtime.publisher import publisher
from app.simulation import status, ticker
from app.simulation.pipeline import process_event
from app.simulation.schemas import IngestEventRequest, SimStartRequest

router = APIRouter()


@router.get("/sim/scenarios")
def sim_scenarios(conn: sqlite3.Connection = Depends(get_db_connection)) -> list[dict]:
    return status.list_scenarios(conn)


@router.get("/sim/speeds")
def sim_speeds(conn: sqlite3.Connection = Depends(get_db_connection)) -> list[dict]:
    return status.list_speeds(conn)


@router.get("/sim/{payment_id}")
def sim_status(payment_id: str, scenario_code: Optional[str] = None, conn: sqlite3.Connection = Depends(get_db_connection)) -> dict:
    result = status.get_sim_status(conn, payment_id, scenario_code)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown payment_id {payment_id!r}")
    return result


@router.post("/events", status_code=201)
def ingest_event(payload: IngestEventRequest, conn: sqlite3.Connection = Depends(get_db_connection)) -> dict:
    try:
        result = process_event(
            conn,
            payment_id=payload.payment_id,
            state=payload.state,
            msg_type=payload.msg_type,
            tx_sts=payload.tx_sts,
            reason_code=payload.reason_code,
            orgnl_end_to_end_id=payload.orgnl_end_to_end_id,
            orgnl_uetr=payload.orgnl_uetr,
            description=payload.description,
            failure_injected=payload.failure_injected,
            new_payment_fields=payload.new_payment.model_dump() if payload.new_payment else None,
            source="NETWORK",
        )
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    publisher.notify(result["notification"])
    return {"event_id": result["event_id"], "payment_id": result["payment"]["payment_id"], "status": result["payment"]["status"]}


@router.post("/sim/{payment_id}/start", status_code=202)
def sim_start(payment_id: str, payload: SimStartRequest, conn: sqlite3.Connection = Depends(get_db_connection)) -> dict:
    row = conn.execute("SELECT 1 FROM payment WHERE payment_id = ?", (payment_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown payment_id {payment_id!r}")
    try:
        run_id = ticker.start_run(conn, payment_id, payload.scenario_code, payload.speed_code)
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run_id, "mode": "RUNNING"}


@router.post("/sim/{payment_id}/pause")
def sim_pause(payment_id: str, conn: sqlite3.Connection = Depends(get_db_connection)) -> dict:
    run = conn.execute(
        "SELECT run_id FROM sim_run WHERE payment_id = ? AND mode = 'RUNNING' ORDER BY started_at DESC LIMIT 1",
        (payment_id,),
    ).fetchone()
    if run is None:
        raise HTTPException(status_code=404, detail="No running simulation for this payment")
    ticker.pause_run(conn, run["run_id"])
    return {"run_id": run["run_id"], "mode": "PAUSED"}


@router.post("/sim/{payment_id}/reset")
def sim_reset(payment_id: str, conn: sqlite3.Connection = Depends(get_db_connection)) -> dict:
    ticker.reset_run(conn, payment_id)
    return {"payment_id": payment_id, "mode": "RESET"}


@router.post("/sim/{payment_id}/inject")
def sim_inject(payment_id: str, conn: sqlite3.Connection = Depends(get_db_connection)) -> dict:
    run = conn.execute(
        "SELECT run_id FROM sim_run WHERE payment_id = ? AND mode = 'RUNNING' ORDER BY started_at DESC LIMIT 1",
        (payment_id,),
    ).fetchone()
    if run is None:
        raise HTTPException(status_code=404, detail="No running simulation for this payment")
    ticker.inject_failure(conn, run["run_id"])
    return {"run_id": run["run_id"], "failure_injected": True}
