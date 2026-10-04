"""Phase 3 \u2014 seed generator: the 68 canonical + generated payments and their events load
correctly and reproduce the POC's canonical field values exactly."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db.migrate import run_migration
from app.db.seed_reference_data import seed_reference_data
from app.seed.generator import seed_payments


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    run_migration(path)
    seed_reference_data(path)
    seed_payments(path)
    return path


def _row(conn: sqlite3.Connection, payment_id: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM payment WHERE payment_id = ?", (payment_id,)).fetchone()


def test_row_counts(seeded_db: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("SELECT COUNT(*) FROM payment").fetchone()[0] == 68
        assert conn.execute("SELECT COUNT(*) FROM payment_event").fetchone()[0] == 450
        assert conn.execute("SELECT COUNT(*) FROM payment_agent_hop").fetchone()[0] == 173
    finally:
        conn.close()


def test_pay_cb_000001_matches_poc_exactly(seeded_db: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    conn.row_factory = sqlite3.Row
    try:
        p = _row(conn, "PAY-CB-000001")
        assert p["status"] == "IN_PROGRESS"
        assert p["ultimate_status"] == "IN_PROGRESS"
        assert p["intermediate_status"] == "WAITING_CORRESPONDENT"
        assert p["debtor"] == "Global Trading AG"
        assert p["ultimate_debtor"] == "Global Trading Holdings AG"
        assert p["debtor_agent"] == "JPMorgan Chase Bank N.A."
        assert p["correspondent"] == "Citibank N.A."
        assert p["intermediary"] == "Deutsche Bank AG"
        assert p["creditor_agent"] == "HSBC Bank plc"
        assert p["creditor"] == "HSBC Treasury Ltd"
        assert p["hops"] == 4
        assert p["investigation_id"] == "INV-90231"
        assert p["owner"] == "M. Patel"
        assert p["nostro"] == "USD NOSTRO \u2022 004821"
        assert p["ai_agent"] == "Correspondent Agent"
        assert abs(p["ai_confidence"] - 0.94) < 1e-9

        events = conn.execute(
            "SELECT seq, state, prev_state FROM payment_event WHERE payment_id = ? ORDER BY seq",
            ("PAY-CB-000001",),
        ).fetchall()
        assert len(events) == 5
        assert events[0]["state"] == "INITIATED" and events[0]["prev_state"] is None
        assert events[-1]["state"] == "IN_TRANSIT" and events[-1]["prev_state"] == "SENT"
    finally:
        conn.close()


def test_pay_return_000001_rejected_never_completed(seeded_db: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    conn.row_factory = sqlite3.Row
    try:
        p = _row(conn, "PAY-RETURN-000001")
        assert p["status"] == "REJECTED"
        assert p["ultimate_status"] == "REJECTED"
        assert p["status_reason"].startswith("AC03")
        assert p["investigation_id"] == "INV-90244"
    finally:
        conn.close()


def test_pay_timeout_investigation_ultimate_in_progress(seeded_db: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    conn.row_factory = sqlite3.Row
    try:
        p = _row(conn, "PAY-TIMEOUT-000001")
        assert p["status"] == "INVESTIGATION"
        assert p["ultimate_status"] == "IN_PROGRESS"
    finally:
        conn.close()


def test_no_payment_shows_completed_unless_status_completed(seeded_db: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT payment_id, status, completed_at FROM payment").fetchall()
        for r in rows:
            if r["status"] != "COMPLETED":
                assert r["completed_at"] in (None, ""), f"{r['payment_id']} has completed_at set but status={r['status']}"
    finally:
        conn.close()


def test_reseeding_is_idempotent(seeded_db: Path) -> None:
    seed_payments(seeded_db)
    conn = sqlite3.connect(seeded_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM payment").fetchone()[0] == 68
        assert conn.execute("SELECT COUNT(*) FROM payment_event").fetchone()[0] == 450
    finally:
        conn.close()


def test_legal_entity_assignment(seeded_db: Path) -> None:
    """DOME is always ENT-US (US-only rails); CBCC follows the debtor's country."""
    conn = sqlite3.connect(seeded_db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT payment_id, domain, debtor_country, legal_entity_id FROM payment").fetchall()
        assert all(r["legal_entity_id"] is not None for r in rows)
        for r in rows:
            if r["domain"] == "DOME":
                assert r["legal_entity_id"] == "ENT-US", r["payment_id"]
        non_us_cbcc = [r for r in rows if r["domain"] == "CBCC" and r["debtor_country"] not in (None, "US")]
        assert non_us_cbcc, "expected at least one non-US-debtor CBCC payment in the fixture"
        assert all(r["legal_entity_id"] == "ENT-UK" for r in non_us_cbcc)
    finally:
        conn.close()

