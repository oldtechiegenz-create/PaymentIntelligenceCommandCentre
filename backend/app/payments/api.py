"""Payment Discovery REST API \u2014 GET /fields, GET /payments, GET /payments/export, GET /payments/{id}."""
from __future__ import annotations

import csv
import io
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from app.db.connection import get_db_connection
from app.payments.detail import get_payment_detail
from app.payments.fields import get_field_catalogue_response
from app.payments.search import search_payments, search_payments_for_export

router = APIRouter()


@router.get("/fields")
def fields(conn: sqlite3.Connection = Depends(get_db_connection)) -> dict:
    return get_field_catalogue_response(conn)


def _parse_cols(cols: Optional[str]) -> Optional[list[str]]:
    return [c for c in cols.split(",") if c] if cols else None


@router.get("/payments")
def payments(
    mode: str = "ALL",
    domain: Optional[str] = None,
    rail: Optional[str] = None,
    status: Optional[str] = None,
    msg: Optional[str] = None,
    ccy: Optional[str] = None,
    min: Optional[float] = None,
    q: Optional[str] = None,
    sort: Optional[str] = None,
    dir: int = 1,
    cols: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
    sim: bool = False,
    conn: sqlite3.Connection = Depends(get_db_connection),
) -> dict:
    return search_payments(
        conn, mode=mode, domain=domain, rail=rail, status=status, msg=msg, ccy=ccy,
        min_amount=min, query_text=q, sort_key=sort, sort_dir=dir, cols=_parse_cols(cols),
        page=page, page_size=page_size, include_simulated=sim,
    )


@router.get("/payments/export")
def payments_export(
    mode: str = "ALL",
    domain: Optional[str] = None,
    rail: Optional[str] = None,
    status: Optional[str] = None,
    msg: Optional[str] = None,
    ccy: Optional[str] = None,
    min: Optional[float] = None,
    q: Optional[str] = None,
    cols: Optional[str] = None,
    conn: sqlite3.Connection = Depends(get_db_connection),
) -> Response:
    selected = _parse_cols(cols) or []
    rows = search_payments_for_export(
        conn, cols=selected, mode=mode, domain=domain, rail=rail, status=status,
        msg=msg, ccy=ccy, min_amount=min, query_text=q,
    )
    display_names = dict(
        conn.execute("SELECT field_id, display_name FROM field_catalogue").fetchall()
    )
    used_cols = selected or (list(rows[0].keys()) if rows else [])

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([display_names.get(c, c) for c in used_cols])
    for row in rows:
        writer.writerow([row.get(c, "") for c in used_cols])

    csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="payment-command-center-view.csv"'},
    )


@router.get("/payments/{payment_id}")
def payment_detail(payment_id: str, conn: sqlite3.Connection = Depends(get_db_connection)) -> dict:
    detail = get_payment_detail(conn, payment_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Payment '{payment_id}' not found")
    return detail

