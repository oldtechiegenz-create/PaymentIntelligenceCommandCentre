"""SQLite connection helper — single WAL-mode connection pool.

WAL mode allows concurrent reads (Discovery/dashboard) without blocking writes
(ingestion/simulation ticker/WebSocket publisher) under the async FastAPI +
background-task load this app uses in later phases.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "payment_command_center.db"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a new SQLite connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def get_db_connection():
    """FastAPI dependency — yields a connection, closes it after the request.

    Override with `app.dependency_overrides[get_db_connection] = ...` in tests
    to point at a temporary database instead of the real one.
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
