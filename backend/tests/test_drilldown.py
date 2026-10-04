"""Drilldown & Mandates \u2014 customers/rails are live SQL aggregates over the real seeded
`payment` table; mandate readiness/coverage are derived from the linked payment's actual
current state. Nothing here is a hardcoded snapshot."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_connection, get_db_connection
from app.db.migrate import run_migration
from app.db.seed_reference_data import seed_reference_data
from app.main import app
from app.seed.generator import seed_payments


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
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_drilldown_customers_ordered_by_real_payment_value(client: TestClient, db_path: Path) -> None:
    resp = client.get("/drilldown/customers")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) > 0
    # strictly descending by valueUsd, and matches a real live SUM over `payment`
    values = [c["valueUsd"] for c in body]
    assert values == sorted(values, reverse=True)

    conn = get_connection(db_path)
    gta = next(c for c in body if c["customerId"] == "C-GLOBAL-TRADING-AG")
    real_sum = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payment "
        "WHERE customer_id = 'C-GLOBAL-TRADING-AG' AND debit_ccy = 'USD' AND is_simulated = 0"
    ).fetchone()[0]
    conn.close()
    assert gta["valueUsd"] == real_sum
    assert gta["displayName"] == "Global Trading AG"
    assert 0 <= gta["stpRatePct"] <= 100


def test_drilldown_rails_are_real_aggregates_no_hardcoded_other_bucket(client: TestClient, db_path: Path) -> None:
    resp = client.get("/drilldown/rails")
    assert resp.status_code == 200
    body = resp.json()
    # exactly the modelled rails that actually appear in the seeded payment set \u2014 no
    # static "Other" placeholder, since every payment.rail is FK-constrained to ref_rail
    assert {r["railCode"] for r in body} == {"SWIFT CBPR+", "Fedwire", "ACH", "RTP", "FedNow"}
    values = [r["valueUsd"] for r in body]
    assert values == sorted(values, reverse=True)

    conn = get_connection(db_path)
    swift = next(r for r in body if r["railCode"] == "SWIFT CBPR+")
    real_sum = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payment WHERE rail = 'SWIFT CBPR+' AND debit_ccy = 'USD' AND is_simulated = 0"
    ).fetchone()[0]
    conn.close()
    assert swift["valueUsd"] == real_sum
    assert swift["latencyDisplay"].endswith("p95")  # SWIFT CBPR+ is latency_class XB
    ach = next(r for r in body if r["railCode"] == "ACH")
    assert ach["latencyDisplay"].endswith("avg") and "h" in ach["latencyDisplay"]  # BATCH rail -> hours


def test_mandates_readiness_and_coverage_derived_from_linked_payment_state(client: TestClient, db_path: Path) -> None:
    resp = client.get("/mandates")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 4

    conn = get_connection(db_path)
    for m in body:
        status = conn.execute(
            "SELECT status FROM payment WHERE payment_id = ?", (m["linkedPaymentId"],)
        ).fetchone()["status"]
        assert m["readinessNote"].startswith(f"Linked payment currently {status}")
        if status in ("REJECTED", "RETURNED", "FAILED", "INVESTIGATION", "CANCELLED"):
            assert m["readiness"] == "Needs review"
            assert 0 <= m["coveragePct"] <= 100
        else:
            assert m["readiness"] == "Ready"
            assert m["coveragePct"] == 100.0
    conn.close()

    gta = next(m for m in body if m["mandateId"] == "MND-GTA-1800")
    assert gta["customerName"] == "Global Trading AG"
    assert gta["linkedPaymentId"] == "PAY-CB-000001"
    needs_review = [m for m in body if m["readiness"] == "Needs review"]
    assert len(needs_review) == 2
