"""Dashboard \u2014 live-computed portfolio KPIs, persona/mode breakdown and charts.

Every number here is a real SQL aggregate over the `payment`/`payment_event` tables,
never a hardcoded snapshot (see architecture decisions memory for the rationale).

Unlike the POC (where the CBCC/DOME/Enterprise persona selector never actually
filters the KPIs/charts \u2014 it's static theater there), everything here genuinely
filters by `mode` and `date` when given, since live real data makes that just as easy
to compute correctly and far more useful for an actual ops team.
"""
from __future__ import annotations

import sqlite3

# A payment that has ever shown one of these states needed manual/exception handling,
# so it does NOT count toward STP (straight-through processing).
EXCEPTION_STATES = ("REJECTED", "RETURNED", "FAILED", "INVESTIGATION", "CANCELLED")
TERMINAL_STATUSES = ("COMPLETED", "REJECTED", "RETURNED", "FAILED", "CANCELLED")
MODES = ("ALL", "CBCC", "DOME")


def _filters(mode: str, date: str | None) -> tuple[str, tuple]:
    """Builds a `WHERE ...` clause (or "") for `payment p` queries, filtering by
    domain (mode) and/or business date. Every query below assumes `payment` is
    aliased as `p`. Simulated (shadow) payments are always excluded — a guided
    simulation must never move the enterprise's real numbers."""
    conditions: list[str] = ["p.is_simulated = 0"]
    params: list[str] = []
    if mode in ("CBCC", "DOME"):
        conditions.append("p.domain = ?")
        params.append(mode)
    if date:
        conditions.append("date(p.initiated_at) = ?")
        params.append(date)
    where = "WHERE " + " AND ".join(conditions)
    return where, tuple(params)


def get_available_date_range(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT MIN(date(initiated_at)), MAX(date(initiated_at)) FROM payment WHERE is_simulated = 0").fetchone()
    return {"min": row[0], "max": row[1]}


def get_portfolio_stats(conn: sqlite3.Connection, date: str | None) -> dict:
    """`rails` is the enterprise's total configured infrastructure (not date-scoped
    \u2014 it describes what's available, not what happened on one day). `corridors`
    and `legal_entities` describe that day's actual book, matching the POC's own
    "Yesterday \u00b7 synthetic enterprise portfolio" framing."""
    where, params = _filters("ALL", date)
    rails = conn.execute("SELECT COUNT(*) FROM ref_rail").fetchone()[0]
    corridors = conn.execute(
        f"SELECT COUNT(DISTINCT p.country) FROM payment p {where}"
        f"{' AND' if where else 'WHERE'} p.country IS NOT NULL",
        params,
    ).fetchone()[0]
    legal_entities = conn.execute(
        f"SELECT COUNT(DISTINCT p.legal_entity_id) FROM payment p {where}"
        f"{' AND' if where else 'WHERE'} p.legal_entity_id IS NOT NULL",
        params,
    ).fetchone()[0]
    return {"rails": rails, "corridors": corridors, "legal_entities": legal_entities}


def get_persona_counts(conn: sqlite3.Connection, date: str | None) -> list[dict]:
    counts = {}
    for m in MODES:
        where, params = _filters(m, date)
        counts[m] = conn.execute(f"SELECT COUNT(*) FROM payment p {where}", params).fetchone()[0]
    return [{"mode": m, "payments": counts[m]} for m in ("CBCC", "DOME", "ALL")]


def get_kpis(conn: sqlite3.Connection, mode: str, date: str | None) -> dict:
    where, params = _filters(mode, date)
    total = conn.execute(f"SELECT COUNT(*) FROM payment p {where}", params).fetchone()[0]

    payment_value_usd = conn.execute(
        f"SELECT COALESCE(SUM(p.amount), 0) FROM payment p {where}"
        f"{' AND' if where else 'WHERE'} p.debit_ccy = 'USD'",
        params,
    ).fetchone()[0]
    non_usd_count = conn.execute(
        f"SELECT COUNT(*) FROM payment p {where}{' AND' if where else 'WHERE'} p.debit_ccy != 'USD'",
        params,
    ).fetchone()[0]

    exc_placeholders = ",".join("?" * len(EXCEPTION_STATES))
    non_stp = conn.execute(
        f"SELECT COUNT(DISTINCT pe.payment_id) FROM payment_event pe "
        f"JOIN payment p ON p.payment_id = pe.payment_id {where}"
        f"{' AND' if where else 'WHERE'} pe.state IN ({exc_placeholders})",
        params + EXCEPTION_STATES,
    ).fetchone()[0]
    stp_rate_pct = round((total - non_stp) / total * 100, 2) if total else None

    term_placeholders = ",".join("?" * len(TERMINAL_STATUSES))
    terminal_counts = dict(
        conn.execute(
            f"SELECT p.status, COUNT(*) FROM payment p {where}"
            f"{' AND' if where else 'WHERE'} p.status IN ({term_placeholders}) GROUP BY p.status",
            params + TERMINAL_STATUSES,
        ).fetchall()
    )
    terminal_total = sum(terminal_counts.values())
    completed = terminal_counts.get("COMPLETED", 0)
    success_rate_pct = round(completed / terminal_total * 100, 2) if terminal_total else None

    exceptions_count = conn.execute(
        f"SELECT COUNT(*) FROM payment p {where}{' AND' if where else 'WHERE'} p.status IN ({exc_placeholders})",
        params + EXCEPTION_STATES,
    ).fetchone()[0]
    exceptions_pct = round(exceptions_count / total * 100, 2) if total else None

    avg_latency_row = conn.execute(
        f"SELECT AVG(pe.processing_ms) FROM payment_event pe "
        f"JOIN payment p ON p.payment_id = pe.payment_id "
        f"JOIN ref_rail r ON r.rail_code = p.rail {where}"
        f"{' AND' if where else 'WHERE'} pe.processing_ms > 0 AND r.latency_class IN ('INSTANT','WIRE')",
        params,
    ).fetchone()
    avg_latency_ms = round(avg_latency_row[0]) if avg_latency_row[0] is not None else None

    investigation_cases = conn.execute(
        f"SELECT COUNT(*) FROM payment p {where}{' AND' if where else 'WHERE'} p.investigation_id IS NOT NULL",
        params,
    ).fetchone()[0]

    return {
        "payments": total,
        "payment_value_usd": payment_value_usd,
        "payment_value_excludes_non_usd_count": non_usd_count,
        "stp_rate_pct": stp_rate_pct,
        "success_rate_pct": success_rate_pct,
        "success_rate_terminal_total": terminal_total,
        "exceptions_pct": exceptions_pct,
        "exceptions_count": exceptions_count,
        "avg_latency_ms": avg_latency_ms,
        "investigation_cases": investigation_cases,
    }


def get_status_breakdown(conn: sqlite3.Connection, mode: str, date: str | None) -> list[dict]:
    where, params = _filters(mode, date)
    rows = conn.execute(
        f"SELECT p.status AS status, COUNT(*) AS n, "
        f"COALESCE(SUM(CASE WHEN p.debit_ccy='USD' THEN p.amount ELSE 0 END), 0) AS usd "
        f"FROM payment p {where} GROUP BY p.status ORDER BY n DESC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_rail_breakdown(conn: sqlite3.Connection, mode: str, date: str | None) -> list[dict]:
    where, params = _filters(mode, date)
    rows = conn.execute(
        f"SELECT p.rail AS rail, COUNT(*) AS n, "
        f"COALESCE(SUM(CASE WHEN p.debit_ccy='USD' THEN p.amount ELSE 0 END), 0) AS usd "
        f"FROM payment p {where} GROUP BY p.rail ORDER BY n DESC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_type_breakdown(conn: sqlite3.Connection, mode: str, date: str | None) -> list[dict]:
    where, params = _filters(mode, date)
    rows = conn.execute(
        f"SELECT rpt.radar_bucket AS bucket, COUNT(*) AS n, "
        f"COALESCE(SUM(CASE WHEN p.debit_ccy='USD' THEN p.amount ELSE 0 END), 0) AS usd "
        f"FROM payment p JOIN ref_payment_type rpt ON rpt.payment_type = p.payment_type {where} "
        f"GROUP BY rpt.radar_bucket ORDER BY n DESC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_trend(conn: sqlite3.Connection, mode: str, date: str, window_days: int = 7) -> list[dict]:
    """Real daily volume/value for the `window_days` ending at `date`, filtered by mode.

    Deliberately sparse \u2014 only returns rows for dates that actually have payments,
    never zero-filled for missing days. A missing day could mean "zero payments" or
    "before this system had any data" and we can't tell those apart, so we don't guess."""
    where, params = _filters(mode, None)
    date_condition = "date(p.initiated_at) BETWEEN date(?, ?) AND date(?)"
    date_params = (date, f"-{window_days - 1} days", date)
    if where:
        query = f"{where} AND {date_condition}"
    else:
        query = f"WHERE {date_condition}"
    rows = conn.execute(
        f"SELECT date(p.initiated_at) AS d, COUNT(*) AS n, "
        f"COALESCE(SUM(CASE WHEN p.debit_ccy='USD' THEN p.amount ELSE 0 END), 0) AS usd "
        f"FROM payment p {query} GROUP BY d ORDER BY d",
        params + date_params,
    ).fetchall()
    return [{"date": r["d"], "payments": r["n"], "value_usd": r["usd"]} for r in rows]


def get_dashboard(conn: sqlite3.Connection, mode: str = "ALL", date: str | None = None) -> dict:
    if mode not in MODES:
        mode = "ALL"
    date_range = get_available_date_range(conn)
    effective_date = date or date_range["max"]
    return {
        "date": effective_date,
        "available_date_range": date_range,
        "mode": mode,
        "portfolio": get_portfolio_stats(conn, effective_date),
        "kpis": get_kpis(conn, mode, effective_date),
        "personas": get_persona_counts(conn, effective_date),
        "charts": {
            "status": get_status_breakdown(conn, mode, effective_date),
            "rail": get_rail_breakdown(conn, mode, effective_date),
            "type": get_type_breakdown(conn, mode, effective_date),
            "trend": get_trend(conn, mode, effective_date),
        },
    }
