"""Customer / Rail / Mandate drilldown \u2014 everything here is a real live SQL aggregate
over the `payment`/`customer`/`ref_rail` tables, never a stored snapshot (see schema.sql's
section E header note). Mandate identity (who/what/due time/linked payment) is genuine
configured reference data \u2014 an obligation is instructed, not derived from payment traffic
\u2014 but its readiness/coverage is derived from the linked payment's actual current state
at query time, never stored."""
from __future__ import annotations

import sqlite3
from typing import Optional

from app.dashboard.queries import EXCEPTION_STATES

HAPPY_PATH_STEP_COUNT = 8  # ref_payment_state: INITIATED..COMPLETED, sort_order 1-8


def _fmt_usd(value_usd: float) -> str:
    if value_usd >= 1e12:
        return f"${value_usd / 1e12:.2f}T"
    if value_usd >= 1e9:
        return f"${value_usd / 1e9:.0f}B"
    if value_usd >= 1e6:
        return f"${value_usd / 1e6:.0f}M"
    return f"${value_usd:,.0f}"


def _fmt_latency(ms: float, latency_class: str, stat: str) -> str:
    if latency_class == "BATCH":
        return f"{ms / 3600000:.1f}h {stat}"
    return f"{ms / 1000:.1f}s {stat}"


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[idx]


def get_customers(conn: sqlite3.Connection, date: Optional[str] = None) -> list[dict]:
    """Returns every customer with at least one real payment, ordered by value desc \u2014
    the API never truncates. "Top 10" is purely a frontend display choice so searching
    can still reach customers outside the default top-10 view."""
    conditions = ["p.is_simulated = 0", "p.customer_id IS NOT NULL"]
    date_params: list[str] = []
    if date:
        conditions.append("date(p.initiated_at) = ?")
        date_params.append(date)
    where = " AND ".join(conditions)
    exc_placeholders = ",".join("?" * len(EXCEPTION_STATES))

    # A correlated EXISTS (not a JOIN) against payment_event, so a payment with several
    # events isn't counted \u2014 or summed \u2014 more than once.
    rows = conn.execute(
        f"SELECT p.customer_id AS customer_id, c.customer_name AS display_name, "
        f"COALESCE(SUM(CASE WHEN p.debit_ccy='USD' THEN p.amount ELSE 0 END), 0) AS value_usd, "
        f"COUNT(*) AS total, "
        f"COUNT(CASE WHEN EXISTS ("
        f"  SELECT 1 FROM payment_event pe WHERE pe.payment_id = p.payment_id AND pe.state IN ({exc_placeholders})"
        f") THEN 1 END) AS non_stp "
        f"FROM payment p JOIN customer c ON c.customer_id = p.customer_id "
        f"WHERE {where} GROUP BY p.customer_id, c.customer_name ORDER BY value_usd DESC",
        (*EXCEPTION_STATES, *date_params),
    ).fetchall()

    result = []
    for r in rows:
        stp_pct = round((r["total"] - r["non_stp"]) / r["total"] * 100, 1) if r["total"] else None
        result.append({
            "customerId": r["customer_id"],
            "displayName": r["display_name"],
            "valueUsd": r["value_usd"],
            "stpRatePct": stp_pct,
        })
    return result


def get_rails(conn: sqlite3.Connection, date: Optional[str] = None) -> list[dict]:
    conditions = ["p.is_simulated = 0"]
    date_params: list[str] = []
    if date:
        conditions.append("date(p.initiated_at) = ?")
        date_params.append(date)
    where = " AND ".join(conditions)

    rail_rows = conn.execute(
        f"SELECT p.rail AS rail_code, r.color_token, r.latency_class, "
        f"COALESCE(SUM(CASE WHEN p.debit_ccy='USD' THEN p.amount ELSE 0 END), 0) AS value_usd "
        f"FROM payment p JOIN ref_rail r ON r.rail_code = p.rail "
        f"WHERE {where} GROUP BY p.rail, r.color_token, r.latency_class",
        date_params,
    ).fetchall()

    latency_rows = conn.execute(
        f"SELECT p.rail AS rail_code, pe.processing_ms AS ms FROM payment_event pe "
        f"JOIN payment p ON p.payment_id = pe.payment_id WHERE {where} AND pe.processing_ms > 0",
        date_params,
    ).fetchall()
    latencies_by_rail: dict[str, list[int]] = {}
    for lr in latency_rows:
        latencies_by_rail.setdefault(lr["rail_code"], []).append(lr["ms"])

    result = []
    for r in rail_rows:
        stat = "p95" if r["latency_class"] in ("XB", "WIRE") else "avg"
        values = latencies_by_rail.get(r["rail_code"], [])
        latency_ms = _percentile(values, 95) if stat == "p95" else (sum(values) / len(values) if values else None)
        result.append({
            "railLabel": r["rail_code"],
            "railCode": r["rail_code"],
            "valueUsd": r["value_usd"],
            "displayValue": _fmt_usd(r["value_usd"]),
            "latencyDisplay": _fmt_latency(latency_ms, r["latency_class"], stat) if latency_ms is not None else "\u2014",
            "colorToken": r["color_token"],
        })
    result.sort(key=lambda x: x["valueUsd"], reverse=True)
    return result


def _mandate_readiness(conn: sqlite3.Connection, payment_id: Optional[str], payment_status: Optional[str]) -> tuple[str, Optional[float], str]:
    if payment_id is None or payment_status is None:
        return "Needs review", None, "Linked payment not found"
    if payment_status in EXCEPTION_STATES:
        max_sort = conn.execute(
            "SELECT MAX(s.sort_order) FROM payment_event pe "
            "JOIN ref_payment_state s ON s.state = pe.state AND s.is_happy_path = 1 "
            "WHERE pe.payment_id = ?",
            (payment_id,),
        ).fetchone()[0]
        coverage_pct = round((max_sort or 0) / HAPPY_PATH_STEP_COUNT * 100, 1)
        return "Needs review", coverage_pct, f"Linked payment currently {payment_status}"
    return "Ready", 100.0, f"Linked payment currently {payment_status}, on track"


def get_mandates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT m.mandate_id, m.display_no, m.description, m.due_time, m.linked_payment_id, "
        "c.customer_name, p.status AS payment_status FROM mandate_obligation m "
        "LEFT JOIN customer c ON c.customer_id = m.customer_id "
        "LEFT JOIN payment p ON p.payment_id = m.linked_payment_id "
        "ORDER BY m.display_no"
    ).fetchall()

    result = []
    for r in rows:
        readiness, coverage_pct, note = _mandate_readiness(conn, r["linked_payment_id"], r["payment_status"])
        result.append({
            "mandateId": r["mandate_id"],
            "displayNo": r["display_no"],
            "customerName": r["customer_name"],
            "description": r["description"],
            "dueTime": r["due_time"],
            "linkedPaymentId": r["linked_payment_id"],
            "coveragePct": coverage_pct,
            "readiness": readiness,
            "readinessNote": note,
        })
    return result

