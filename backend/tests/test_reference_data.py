"""Phase 1 — seed_reference_data.py populates the expected reference/config rows."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db.migrate import run_migration
from app.db.seed_reference_data import seed_reference_data


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    run_migration(path)
    seed_reference_data(path)
    return path


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_row_counts(seeded_db: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    try:
        assert _count(conn, "bank") == 14
        assert _count(conn, "sim_scenario") == 4
        assert _count(conn, "field_catalogue") == 64
        assert _count(conn, "field_category") == 20
        assert _count(conn, "column_preset") == 7
        assert _count(conn, "mandate_obligation") == 4
        assert _count(conn, "persona") == 3
        assert _count(conn, "customer") == 17
        assert _count(conn, "legal_entity") == 2
    finally:
        conn.close()


def test_scenario_step_counts_match_poc_spec(seeded_db: Path) -> None:
    # POC §11: Happy 8, Timeout 7, AC03 7, Screen 8 steps.
    conn = sqlite3.connect(seeded_db)
    try:
        expected = {"HAPPY": 8, "TIMEOUT": 7, "AC03": 7, "SCREEN": 8}
        for scenario_code, n in expected.items():
            actual = conn.execute(
                "SELECT COUNT(*) FROM sim_scenario_step WHERE scenario_code = ?", (scenario_code,)
            ).fetchone()[0]
            assert actual == n, f"{scenario_code}: expected {n} steps, got {actual}"
    finally:
        conn.close()


def test_spot_check_values(seeded_db: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    try:
        assert conn.execute(
            "SELECT reason_name FROM ref_reason_code WHERE reason_code = 'AC03'"
        ).fetchone()[0] == "Invalid Creditor Account Number"
        assert conn.execute(
            "SELECT bank_name FROM bank WHERE bic = 'CHASUS33'"
        ).fetchone()[0] == "JPMorgan Chase Bank N.A."
        assert conn.execute(
            "SELECT COUNT(*) FROM sim_latency_profile WHERE latency_class='XB' AND state='INVESTIGATION'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_seeding_is_idempotent(seeded_db: Path) -> None:
    # Re-running must not raise or duplicate rows (INSERT OR IGNORE).
    seed_reference_data(seeded_db)
    conn = sqlite3.connect(seeded_db)
    try:
        assert _count(conn, "bank") == 14
    finally:
        conn.close()
