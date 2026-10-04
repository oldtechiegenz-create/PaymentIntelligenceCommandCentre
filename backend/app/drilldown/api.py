"""Drilldown & Mandates REST API \u2014 GET /drilldown/customers, GET /drilldown/rails, GET /mandates."""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends

from app.db.connection import get_db_connection
from app.drilldown.queries import get_customers, get_mandates, get_rails

router = APIRouter()


@router.get("/drilldown/customers")
def drilldown_customers(date: Optional[str] = None, conn: sqlite3.Connection = Depends(get_db_connection)) -> list[dict]:
    return get_customers(conn, date)


@router.get("/drilldown/rails")
def drilldown_rails(date: Optional[str] = None, conn: sqlite3.Connection = Depends(get_db_connection)) -> list[dict]:
    return get_rails(conn, date)


@router.get("/mandates")
def mandates(conn: sqlite3.Connection = Depends(get_db_connection)) -> list[dict]:
    return get_mandates(conn)
