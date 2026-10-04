"""Wipes the SQLite DB file and rebuilds it from scratch: schema + reference data + the 68
payments/events. This is the ONLY sanctioned way to get a fresh dataset — schema/seed scripts
are never run automatically on app startup (see architecture decisions memory). Use
deliberately, not routinely.

Usage: uv run python -m app.db.reset
"""
from __future__ import annotations

from pathlib import Path

from app.db.connection import DB_PATH
from app.db.migrate import run_migration
from app.db.seed_reference_data import seed_reference_data
from app.seed.generator import seed_payments


def reset_database(db_path: Path = DB_PATH) -> None:
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(db_path) + suffix)
        if f.exists():
            f.unlink()
    run_migration(db_path)
    seed_reference_data(db_path)
    seed_payments(db_path)


if __name__ == "__main__":
    reset_database()
    print(f"Database reset and reseeded at {DB_PATH}")
