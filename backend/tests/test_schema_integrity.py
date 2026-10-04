"""Phase 1 — schema.sql applies cleanly and creates every expected table."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.db.migrate import run_migration

EXPECTED_TABLES = {
    "bank", "ref_domain", "ref_rail", "ref_scheme", "ref_payment_type", "ref_iso_message_type",
    "ref_mt_mx_mapping", "ref_iso_tx_status", "ref_reason_code", "ref_payment_status",
    "ref_payment_state", "ref_state_transition", "customer", "legal_entity", "persona", "persona_tag",
    "field_category", "field_catalogue", "column_preset", "column_preset_field",
    "nl_query_vocabulary", "sim_scenario", "sim_scenario_step", "sim_speed",
    "sim_latency_profile", "payment", "payment_agent_hop", "sim_run", "payment_event",
    "mandate_obligation",
}


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    run_migration(path)
    return path


def test_all_expected_tables_exist(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        actual = {r[0] for r in rows}
        missing = EXPECTED_TABLES - actual
        assert not missing, f"missing tables: {missing}"
    finally:
        conn.close()


def test_migration_is_idempotent(db_path: Path) -> None:
    # Running migration twice against the same file must not raise.
    run_migration(db_path)


def test_payment_table_key_columns(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(payment)").fetchall()}
        for expected in ("payment_id", "uetr", "status", "ultimate_status", "intermediate_status",
                         "sim_state", "completed_at", "debtor", "creditor", "rail", "domain"):
            assert expected in cols, f"payment.{expected} missing"
    finally:
        conn.close()
