"""Phase 4 \u2014 live ingestion + simulation ticker + WebSocket: POST /events creates a new
payment on first push and appends to an existing one; malformed input is rejected; the
guided-simulation ticker reproduces the exact event/ISO/exception counts from POC \u00a711;
the WebSocket delivers exactly one notification per event (no double-delivery)."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_connection, get_db_connection
from app.db.migrate import run_migration
from app.db.seed_reference_data import seed_reference_data
from app.main import app
from app.seed.generator import seed_payments
from app.simulation import ticker


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    run_migration(path)
    seed_reference_data(path)
    seed_payments(path)
    return path


@pytest.fixture()
def client(db_path: Path):
    def _override_db():
        conn = get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db_connection] = _override_db
    ticker.use_db_path(db_path)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    ticker.use_db_path(None)
    ticker.RUNNING_TASKS.clear()


def _conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_ingest_event_appends_to_existing_payment(client: TestClient, db_path: Path) -> None:
    resp = client.post(
        "/events",
        json={"payment_id": "PAY-CB-000001", "state": "REJECTED", "msg_type": "pacs.002", "tx_sts": "RJCT", "reason_code": "AC03"},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "REJECTED"

    conn = _conn(db_path)
    row = conn.execute("SELECT status, status_reason FROM payment WHERE payment_id = ?", ("PAY-CB-000001",)).fetchone()
    assert row["status"] == "REJECTED"
    assert row["status_reason"].startswith("AC03")
    events = conn.execute(
        "SELECT seq, state FROM payment_event WHERE payment_id = ? AND sim_run_id IS NULL ORDER BY seq", ("PAY-CB-000001",)
    ).fetchall()
    assert events[-1]["state"] == "REJECTED"
    conn.close()


def test_ingest_event_creates_new_payment_when_id_omitted(client: TestClient, db_path: Path) -> None:
    resp = client.post(
        "/events",
        json={
            "state": "INITIATED",
            "msg_type": "pain.001",
            "new_payment": {
                "domain": "CBCC", "rail": "SWIFT CBPR+", "payment_type": "Cross-Border Customer Credit Transfer",
                "settlement_method": "COVE", "debtor": "Test Corp", "creditor": "Test Bank Ltd",
                "amount": 1000.0, "debit_ccy": "USD", "initiated_at": "2026-09-27 10:00:00",
                "uetr": "b1c2d3e4-1111-4222-8333-444455556666",
            },
        },
    )
    assert resp.status_code == 201
    payment_id = resp.json()["payment_id"]
    assert payment_id.startswith("PAY-LIVE-")

    conn = _conn(db_path)
    row = conn.execute("SELECT * FROM payment WHERE payment_id = ?", (payment_id,)).fetchone()
    assert row is not None
    assert row["debtor"] == "Test Corp"
    assert row["credit_ccy"] == "USD"  # defaulted from debit_ccy
    events = conn.execute("SELECT seq, prev_state FROM payment_event WHERE payment_id = ?", (payment_id,)).fetchall()
    assert len(events) == 1
    assert events[0]["seq"] == 1
    assert events[0]["prev_state"] is None
    conn.close()


def test_ingest_event_unknown_msg_type_rejected(client: TestClient) -> None:
    resp = client.post(
        "/events",
        json={"payment_id": "PAY-CB-000001", "state": "SENT", "msg_type": "swift.mt103"},
    )
    assert resp.status_code == 422


def test_ingest_event_unknown_state_rejected(client: TestClient) -> None:
    resp = client.post(
        "/events",
        json={"payment_id": "PAY-CB-000001", "state": "BOGUS_STATE", "msg_type": "pacs.008"},
    )
    assert resp.status_code == 422


def _wait_for_run_mode(db_path: Path, run_id: str, mode: str, timeout_s: float = 5.0) -> sqlite3.Row:
    deadline = time.time() + timeout_s
    conn = _conn(db_path)
    try:
        while time.time() < deadline:
            run = conn.execute("SELECT * FROM sim_run WHERE run_id = ?", (run_id,)).fetchone()
            if run is not None and run["mode"] == mode:
                return run
            time.sleep(0.1)
        pytest.fail(f"sim_run {run_id} never reached mode={mode!r}")
    finally:
        conn.close()


def test_happy_scenario_via_ticker_produces_expected_counts(client: TestClient, db_path: Path) -> None:
    # PAY-CB-000001 has both a correspondent and an intermediary, so HAPPY runs
    # all 8 steps (no domestic hop-skipping) \u2014 matches POC \u00a711's 8/7/0 table.
    resp = client.post("/sim/PAY-CB-000001/start", json={"scenario_code": "HAPPY", "speed_code": "10x"})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    run = _wait_for_run_mode(db_path, run_id, "FINISHED")
    assert run["event_count"] == 8
    assert run["iso_message_count"] == 7
    assert run["exception_count"] == 0
    assert run["outcome"] == "COMPLETED"

    conn = _conn(db_path)
    count = conn.execute("SELECT COUNT(*) FROM payment_event WHERE sim_run_id = ?", (run_id,)).fetchone()[0]
    assert count == 8
    shadow = conn.execute("SELECT status, is_simulated, source_payment_id FROM payment WHERE payment_id = ?", ("SIM-PAY-CB-000001",)).fetchone()
    assert shadow["status"] == "COMPLETED"
    assert shadow["is_simulated"] == 1
    assert shadow["source_payment_id"] == "PAY-CB-000001"
    # the source payment itself was never touched
    source = conn.execute("SELECT status, is_simulated FROM payment WHERE payment_id = ?", ("PAY-CB-000001",)).fetchone()
    assert source["status"] == "IN_PROGRESS"
    assert source["is_simulated"] == 0
    conn.close()


def test_reset_deletes_shadow_and_run_record(client: TestClient, db_path: Path) -> None:
    conn = _conn(db_path)
    before = dict(conn.execute("SELECT * FROM payment WHERE payment_id = ?", ("PAY-CB-000001",)).fetchone())
    conn.close()

    resp = client.post("/sim/PAY-CB-000001/start", json={"scenario_code": "HAPPY", "speed_code": "10x"})
    run_id = resp.json()["run_id"]
    _wait_for_run_mode(db_path, run_id, "FINISHED")

    reset_resp = client.post("/sim/PAY-CB-000001/reset")
    assert reset_resp.status_code == 200

    conn = _conn(db_path)
    after = dict(conn.execute("SELECT * FROM payment WHERE payment_id = ?", ("PAY-CB-000001",)).fetchone())
    assert after["status"] == before["status"]  # source untouched throughout
    assert after["intermediate_status"] == before["intermediate_status"]

    shadow = conn.execute("SELECT 1 FROM payment WHERE payment_id = ?", ("SIM-PAY-CB-000001",)).fetchone()
    assert shadow is None  # shadow deleted

    remaining_events = conn.execute("SELECT COUNT(*) FROM payment_event WHERE sim_run_id = ?", (run_id,)).fetchone()[0]
    assert remaining_events == 0  # cascaded with the shadow

    run_row = conn.execute("SELECT 1 FROM sim_run WHERE run_id = ?", (run_id,)).fetchone()
    assert run_row is None  # no run history is kept — Reset clears the sim_run row too
    conn.close()


def test_restarting_replaces_stale_shadow(client: TestClient, db_path: Path) -> None:
    r1 = client.post("/sim/PAY-CB-000001/start", json={"scenario_code": "HAPPY", "speed_code": "10x"})
    run1 = r1.json()["run_id"]
    _wait_for_run_mode(db_path, run1, "FINISHED")

    r2 = client.post("/sim/PAY-CB-000001/start", json={"scenario_code": "AC03", "speed_code": "10x"})
    run2 = r2.json()["run_id"]
    _wait_for_run_mode(db_path, run2, "FINISHED")

    conn = _conn(db_path)
    # no history is kept, so run IDs may be reused — check the shadow's own event set instead:
    # only the AC03 run's events should be present (HAPPY's 8-step run never leaked through).
    events = conn.execute(
        "SELECT state FROM payment_event WHERE payment_id = ? ORDER BY seq", ("SIM-PAY-CB-000001",)
    ).fetchall()
    assert [e["state"] for e in events] == ["INITIATED", "ACCEPTED", "SCREENING", "SENT", "IN_TRANSIT", "REJECTED", "RETURNED"]
    shadow = conn.execute("SELECT status FROM payment WHERE payment_id = ?", ("SIM-PAY-CB-000001",)).fetchone()
    assert shadow["status"] in ("REJECTED", "RETURNED")  # AC03 scenario outcome
    conn.close()


def test_cannot_simulate_an_already_simulated_payment(client: TestClient, db_path: Path) -> None:
    resp = client.post("/sim/PAY-CB-000001/start", json={"scenario_code": "HAPPY", "speed_code": "10x"})
    run_id = resp.json()["run_id"]
    _wait_for_run_mode(db_path, run_id, "FINISHED")

    resp2 = client.post("/sim/SIM-PAY-CB-000001/start", json={"scenario_code": "HAPPY", "speed_code": "10x"})
    assert resp2.status_code == 400


def test_only_one_sim_run_row_kept_per_payment(client: TestClient, db_path: Path) -> None:
    run_ids: list[str] = []
    for _ in range(3):
        resp = client.post("/sim/PAY-CB-000001/start", json={"scenario_code": "HAPPY", "speed_code": "10x"})
        run_id = resp.json()["run_id"]
        _wait_for_run_mode(db_path, run_id, "FINISHED")
        run_ids.append(run_id)

    conn = _conn(db_path)
    remaining = conn.execute("SELECT run_id FROM sim_run WHERE payment_id = ?", ("PAY-CB-000001",)).fetchall()
    conn.close()

    assert len(remaining) == 1  # no history kept — only the most recent run's row survives
    assert remaining[0]["run_id"] == run_ids[-1]


def test_dashboard_excludes_simulated_payments(client: TestClient, db_path: Path) -> None:
    before = client.get("/dashboard").json()["kpis"]["payments"]

    resp = client.post("/sim/PAY-CB-000001/start", json={"scenario_code": "HAPPY", "speed_code": "10x"})
    run_id = resp.json()["run_id"]
    _wait_for_run_mode(db_path, run_id, "FINISHED")

    after = client.get("/dashboard").json()["kpis"]["payments"]
    assert after == before  # the shadow must never move enterprise totals


def test_discovery_search_excludes_simulated_payments_by_default(client: TestClient, db_path: Path) -> None:
    resp = client.post("/sim/PAY-CB-000001/start", json={"scenario_code": "HAPPY", "speed_code": "10x"})
    run_id = resp.json()["run_id"]
    _wait_for_run_mode(db_path, run_id, "FINISHED")

    default_search = client.get("/payments?q=SIM-PAY-CB-000001&cols=paymentId").json()
    assert default_search["total"] == 0

    with_sim = client.get("/payments?q=SIM-PAY-CB-000001&cols=paymentId&sim=true").json()
    assert with_sim["total"] == 1


def test_sim_scenarios_and_speeds_are_real_catalogue_data(client: TestClient) -> None:
    scenarios = client.get("/sim/scenarios").json()
    codes = {s["scenarioCode"] for s in scenarios}
    assert codes == {"HAPPY", "TIMEOUT", "AC03", "SCREEN"}
    happy = next(s for s in scenarios if s["scenarioCode"] == "HAPPY")
    assert len(happy["steps"]) == 8
    assert happy["steps"][0]["state"] == "INITIATED"

    speeds = client.get("/sim/speeds").json()
    assert {s["speedCode"] for s in speeds} == {"1x", "2x", "5x", "10x"}


def test_sim_status_before_any_run(client: TestClient) -> None:
    body = client.get("/sim/PAY-CB-000001").json()
    assert body["sourcePaymentId"] == "PAY-CB-000001"
    assert body["shadowPaymentId"] is None
    assert body["activeRun"] is None
    assert body["events"] == []
    assert len(body["steps"]) == 8  # HAPPY default, PAY-CB-000001 has correspondent+intermediary


def test_sim_status_reflects_live_run(client: TestClient, db_path: Path) -> None:
    resp = client.post("/sim/PAY-CB-000001/start", json={"scenario_code": "HAPPY", "speed_code": "10x"})
    run_id = resp.json()["run_id"]
    _wait_for_run_mode(db_path, run_id, "FINISHED")

    body = client.get("/sim/PAY-CB-000001").json()
    assert body["shadowPaymentId"] == "SIM-PAY-CB-000001"
    assert body["shadowPayment"]["status"] == "COMPLETED"
    assert body["activeRun"]["runId"] == run_id
    assert body["activeRun"]["mode"] == "FINISHED"
    assert len(body["events"]) == 8


def test_sim_status_unknown_payment_is_404(client: TestClient) -> None:
    resp = client.get("/sim/PAY-DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_sim_status_after_reset_shows_no_active_run(client: TestClient, db_path: Path) -> None:
    resp = client.post("/sim/PAY-CB-000001/start", json={"scenario_code": "HAPPY", "speed_code": "10x"})
    run_id = resp.json()["run_id"]
    _wait_for_run_mode(db_path, run_id, "FINISHED")

    client.post("/sim/PAY-CB-000001/reset")

    body = client.get("/sim/PAY-CB-000001").json()
    assert body["shadowPaymentId"] is None
    assert body["activeRun"] is None  # Control Plane must not show the deleted shadow's stale state
    assert body["events"] == []


def test_websocket_delivers_exactly_one_notification_per_event(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        r1 = client.post("/events", json={"payment_id": "PAY-CB-000001", "state": "SCREENING", "msg_type": "pacs.008"})
        msg1 = ws.receive_json()
        r2 = client.post("/events", json={"payment_id": "PAY-CB-000001", "state": "SENT", "msg_type": "pacs.008"})
        msg2 = ws.receive_json()

    assert msg1["event_id"] == r1.json()["event_id"]
    assert msg2["event_id"] == r2.json()["event_id"]
    assert msg1["event_id"] != msg2["event_id"]
