"""Phase 2 \u2014 core engine: exact scenario counts (POC \u00a711) and the mandatory state
consistency rule (a payment must never report status COMPLETED unless it actually completed)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db.migrate import run_migration
from app.db.seed_reference_data import seed_reference_data
from app.engine.state_machine import run_scenario

EXCEPTION_STATES = {"REJECTED", "FAILED", "INVESTIGATION", "RETURNED", "CANCELLED"}


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    run_migration(db_path)
    seed_reference_data(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def make_payment(**overrides) -> dict:
    payment = {
        "payment_id": "PAY-CB-000001",
        "uetr": "a9f4c1e8-2b3d-4d77-9a12-81e7b2c3d901",
        "message_type": "pacs.008",
        "domain": "CBCC",
        "rail": "SWIFT CBPR+",
        "debtor": "Global Trading AG",
        "debtor_agent": "JPMorgan Chase Bank N.A.",
        "correspondent": "Citibank N.A.",
        "intermediary": "Deutsche Bank AG",
        "creditor_agent": "HSBC Bank plc",
        "creditor": "HSBC Treasury Ltd",
        "amount": 18420000,
        "fx_rate": 1,
        "initiated_at": "2026-09-24 09:42:11",
        "status_reason": "\u2014",
        "screening": "CLEARED",
        "investigation_id": None,
        "payment_initiation_dept": "Equity Operations",
        "owner": "M. Patel",
        "status": "IN_PROGRESS",
        "ultimate_status": "IN_PROGRESS",
        "intermediate_status": "INITIATED",
    }
    payment.update(overrides)
    return payment


@pytest.mark.parametrize(
    "scenario_code,expected_events,expected_iso,expected_exceptions,expected_final_status",
    [
        ("HAPPY", 8, 7, 0, "COMPLETED"),
        ("TIMEOUT", 7, 5, 2, "FAILED"),
        ("AC03", 7, 6, 2, "RETURNED"),
        ("SCREEN", 8, 6, 1, "COMPLETED"),
    ],
)
def test_scenario_counts_match_poc_spec(
    conn, scenario_code, expected_events, expected_iso, expected_exceptions, expected_final_status
):
    payment = make_payment()
    final_payment, events = run_scenario(conn, payment, scenario_code)

    assert len(events) == expected_events
    iso_count = sum(1 for e in events if e["iso_message"] != "\u2014")
    assert iso_count == expected_iso
    exception_count = sum(1 for e in events if e["state"] in EXCEPTION_STATES)
    assert exception_count == expected_exceptions
    assert final_payment["status"] == expected_final_status


def test_happy_path_ends_completed_with_settled_intermediate(conn):
    payment = make_payment()
    final_payment, events = run_scenario(conn, payment, "HAPPY")
    assert final_payment["status"] == "COMPLETED"
    assert final_payment["ultimate_status"] == "COMPLETED"
    assert final_payment["intermediate_status"] == "SETTLED"
    assert final_payment["completed_at"] is not None
    assert events[-1]["state"] == "COMPLETED"


@pytest.mark.parametrize("scenario_code", ["TIMEOUT", "AC03", "SCREEN"])
def test_never_completed_mid_run(conn, scenario_code):
    """At every intermediate step of a non-happy-ending run, status must never be COMPLETED
    unless the payment actually reached the COMPLETED state (mandatory state consistency rule)."""
    payment = make_payment()
    profile_payment = make_payment()
    _, events = run_scenario(conn, profile_payment, scenario_code)

    running_payment = make_payment()
    from app.engine.apply_event import apply_event
    from app.engine.latency import format_ms

    cumulative_ms = 0
    for event in events:
        cumulative_ms += event["processing_ms"]
        apply_event(running_payment, event, duration_text=format_ms(cumulative_ms))
        if event["state"] != "COMPLETED":
            assert running_payment["status"] != "COMPLETED", (
                f"{scenario_code}: status showed COMPLETED after non-COMPLETED event {event['state']}"
            )


def test_ac03_return_reason_is_ac03(conn):
    payment = make_payment(status_reason="AC03 \u2014 Invalid Creditor Account Number")
    final_payment, events = run_scenario(conn, payment, "AC03")
    assert final_payment["status"] == "RETURNED"
    # RETURNED is a reason-carrying state (like REJECTED/FAILED/INVESTIGATION), so the AC03
    # reason persists through to the final RETURNED event, not cleared to "\u2014".
    assert final_payment["status_reason"].startswith("AC03")
    rejected_events = [e for e in events if e["state"] == "REJECTED"]
    assert rejected_events and rejected_events[0]["reason"].startswith("AC03")


def test_domestic_happy_path_skips_correspondent_hops(conn):
    payment = make_payment(
        payment_id="PAY-RTP-000001", domain="DOME", rail="RTP",
        correspondent=None, intermediary=None,
        debtor="US Retail Corp", debtor_agent="Bank of America N.A.", creditor_agent="Wells Fargo Bank N.A.",
    )
    _, events = run_scenario(conn, payment, "HAPPY")
    states = [e["state"] for e in events]
    assert "CORRESPONDENT_PROCESSING" not in states
    assert "IN_TRANSIT" not in states
    assert len(events) == 6
