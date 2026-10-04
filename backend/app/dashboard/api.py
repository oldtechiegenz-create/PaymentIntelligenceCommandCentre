"""Dashboard REST API \u2014 GET /dashboard (Executive Radar + Operating Mode + Radar Charts, combined)."""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends

from app.dashboard.queries import get_dashboard
from app.db.connection import get_db_connection

router = APIRouter()


@router.get("/dashboard")
def dashboard(mode: str = "ALL", date: Optional[str] = None, conn: sqlite3.Connection = Depends(get_db_connection)) -> dict:
    return get_dashboard(conn, mode, date)
