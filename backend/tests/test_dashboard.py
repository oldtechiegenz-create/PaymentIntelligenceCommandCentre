"""Executive Radar dashboard \u2014 every stat is a real live SQL aggregate, no hardcoded snapshot."""
from __future__ import annotations

import sqlite3
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


def test_dashboard_matches_real_counts(client: TestClient, db_path: Path) -> None:
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    body = resp.json()

    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM payment").fetchone()[0]
    conn.close()

    assert body["kpis"]["payments"] == total == 68
    assert body["portfolio"]["rails"] == 5
    assert body["portfolio"]["legal_entities"] == 2
    assert body["kpis"]["investigation_cases"] == 5
    assert 0 < body["kpis"]["stp_rate_pct"] <= 100
    assert 0 < body["kpis"]["success_rate_pct"] <= 100
    assert body["kpis"]["avg_latency_ms"] > 0
    assert body["date"] == "2026-09-24"
    assert body["available_date_range"] == {"min": "2026-09-24", "max": "2026-09-24"}

    persona_totals = {p["mode"]: p["payments"] for p in body["personas"]}
    assert persona_totals["ALL"] == 68
    assert persona_totals["CBCC"] + persona_totals["DOME"] == 68

    status_total = sum(row["n"] for row in body["charts"]["status"])
    rail_total = sum(row["n"] for row in body["charts"]["rail"])
    type_total = sum(row["n"] for row in body["charts"]["type"])
    assert status_total == rail_total == type_total == 68


def test_dashboard_mode_filter_matches_persona_counts(client: TestClient) -> None:
    all_body = client.get("/dashboard?mode=ALL").json()
    cbcc_body = client.get("/dashboard?mode=CBCC").json()
    dome_body = client.get("/dashboard?mode=DOME").json()

    assert cbcc_body["kpis"]["payments"] + dome_body["kpis"]["payments"] == all_body["kpis"]["payments"]
    assert sum(r["n"] for r in cbcc_body["charts"]["rail"]) == cbcc_body["kpis"]["payments"]
    # Portfolio's `rails` count is enterprise infrastructure, unaffected by mode or date.
    assert cbcc_body["portfolio"]["rails"] == dome_body["portfolio"]["rails"] == all_body["portfolio"]["rails"]
    # corridors/legal_entities aren't mode-filtered either (only date-filtered) \u2014 same default date across all 3 calls.
    assert cbcc_body["portfolio"]["corridors"] == dome_body["portfolio"]["corridors"] == all_body["portfolio"]["corridors"]


def test_dashboard_date_filter(client: TestClient) -> None:
    real_date_body = client.get("/dashboard?date=2026-09-24").json()
    assert real_date_body["kpis"]["payments"] == 68
    assert real_date_body["date"] == "2026-09-24"

    empty_date_body = client.get("/dashboard?date=2020-01-01").json()
    assert empty_date_body["date"] == "2020-01-01"
    assert empty_date_body["kpis"]["payments"] == 0
    assert empty_date_body["kpis"]["payment_value_usd"] == 0
    assert empty_date_body["kpis"]["stp_rate_pct"] is None
    assert empty_date_body["charts"]["status"] == []
    # available_date_range always reflects the full dataset, regardless of the selected date.
    assert empty_date_body["available_date_range"] == {"min": "2026-09-24", "max": "2026-09-24"}


def test_dashboard_no_fabricated_delta_fields(client: TestClient) -> None:
    """No vs-prior-day delta fields \u2014 those would require fabricating data we don't have."""
    body = client.get("/dashboard").json()
    assert not any("delta" in k or "prior_day" in k for k in body["kpis"])


def test_dashboard_trend_is_real_and_sparse(client: TestClient) -> None:
    """Only one business date exists in the seed data \u2014 trend must reflect that honestly
    (one real point), never zero-filled/interpolated to fake a 7-day series."""
    body = client.get("/dashboard").json()
    trend = body["charts"]["trend"]
    assert trend == [{"date": "2026-09-24", "payments": 68, "value_usd": body["kpis"]["payment_value_usd"]}]

    cbcc_trend = client.get("/dashboard?mode=CBCC").json()["charts"]["trend"]
    assert cbcc_trend[0]["payments"] == 26

    # A date with no data at all returns an empty trend, not a fabricated zero point.
    empty_trend = client.get("/dashboard?date=2020-01-01").json()["charts"]["trend"]
    assert empty_trend == []
