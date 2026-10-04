"""Runs schema.sql against the SQLite database file, creating any missing tables.

Usage: uv run python -m app.db.migrate
Never invoked automatically from app.main's lifespan — schema creation is a
deliberate, explicit action (see architecture decisions memory).
"""
from __future__ import annotations

from pathlib import Path

from app.db.connection import DB_PATH, get_connection

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def run_migration(db_path: Path = DB_PATH) -> None:
    schema_sql = SCHEMA_PATH.read_text()
    conn = get_connection(db_path)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
    print(f"Migration applied to {DB_PATH}")
