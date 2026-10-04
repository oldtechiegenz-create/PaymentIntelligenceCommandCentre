"""Payment Discovery search \u2014 structured filters + a natural-language-ish free-text query
parser, both ported from the POC's applyFilters()/parseQuery() (kept in the DB as
nl_query_vocabulary rather than hardcoded a second time, matching this project's "config
over hardcoding" convention elsewhere).
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

AMOUNT_OVER_RE = re.compile(r"(over|above|greater than|more than|>=?|at least)\s*\$?\s*([\d.,]+)\s*(k|m|mm|b|bn|million|billion|thousand)?\b")
AMOUNT_UNDER_RE = re.compile(r"(under|below|less than|<=?)\s*\$?\s*([\d.,]+)\s*(k|m|mm|b|bn|million|billion|thousand)?\b")
KEY_VALUE_RE = re.compile(r'\b(\w+):("[^"]+"|\S+)')
MSG_RE = re.compile(r"^(pacs|pain|camt)\.\d{3}$")
CCY_RE = re.compile(r"^(usd|eur|gbp|jpy|inr|sgd|chf|cad)$")

# Free-text key: -> SQL expression searched against (flat schema simplification \u2014 e.g.
# `bic` only covers the debtor agent's BIC, not every hop's, since hop BICs live in the
# separate payment_agent_hop table).
KEY_EXPR = {
    "bic": "bic",
    "country": "country || ' ' || COALESCE(creditor_country,'')",
    "debtor": "debtor || ' ' || COALESCE(ultimate_debtor,'')",
    "creditor": "creditor || ' ' || COALESCE(ultimate_creditor,'')",
    "id": "payment_id",
    "uetr": "COALESCE(uetr,'')",
    "owner": "COALESCE(owner,'')",
    "reason": "COALESCE(status_reason,'')",
}


class Vocabulary:
    """Loaded once per request from `nl_query_vocabulary` \u2014 never a second hardcoded copy."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT token, token_kind, maps_to FROM nl_query_vocabulary").fetchall()
        self.status_words = {r["token"]: r["maps_to"] for r in rows if r["token_kind"] == "STATUS"}
        self.rail_words = {r["token"]: r["maps_to"] for r in rows if r["token_kind"] == "RAIL"}
        self.key_alias = {r["token"]: r["maps_to"] for r in rows if r["token_kind"] == "KEY_ALIAS"}
        self.stopwords = {r["token"] for r in rows if r["token_kind"] == "STOPWORD"}
        rail_rows = conn.execute("SELECT rail_code FROM ref_rail").fetchall()
        self.rail_by_lower = {r["rail_code"].lower(): r["rail_code"] for r in rail_rows}


def scale_num(n: str, unit: Optional[str]) -> float:
    v = float(n.replace(",", ""))
    unit = (unit or "").lower()
    if unit in ("k", "thousand"):
        v *= 1e3
    elif unit in ("m", "mm", "million"):
        v *= 1e6
    elif unit in ("b", "bn", "billion"):
        v *= 1e9
    return v


def parse_query(raw: str, vocab: Vocabulary) -> dict:
    qf: dict = {"status": [], "rail": [], "domain": None, "msg": [], "ccy": [], "min": None, "max": None, "text": [], "keys": {}}
    s = " " + raw.lower().replace("in progress", "in_progress").replace("cross-border", "cbcc").replace("cross border", "cbcc").replace("domestic", "dome") + " "

    def _over(m: re.Match) -> str:
        qf["min"] = scale_num(m.group(2), m.group(3))
        return " "

    s = AMOUNT_OVER_RE.sub(_over, s)

    def _under(m: re.Match) -> str:
        qf["max"] = scale_num(m.group(2), m.group(3))
        return " "

    s = AMOUNT_UNDER_RE.sub(_under, s)

    def _keyval(m: re.Match) -> str:
        k, v = m.group(1), m.group(2).strip('"')
        kk = vocab.key_alias.get(k)
        if not kk:
            return m.group(0)
        if kk == "status":
            qf["status"].append(vocab.status_words.get(v, v.upper()))
        elif kk == "rail":
            qf["rail"].append(vocab.rail_by_lower.get(v) or vocab.rail_words.get(v) or v)
        elif kk == "ccy":
            qf["ccy"].append(v.upper())
        elif kk == "msg":
            qf["msg"].append(v)
        elif kk == "domain":
            qf["domain"] = v.upper()
        else:
            qf["keys"][kk] = v
        return " "

    s = KEY_VALUE_RE.sub(_keyval, s)

    for w in s.split():
        if w in vocab.status_words:
            code = vocab.status_words[w]
            if code not in qf["status"]:
                qf["status"].append(code)
        elif w in vocab.rail_words:
            code = vocab.rail_words[w]
            if code not in qf["rail"]:
                qf["rail"].append(code)
        elif w in ("cbcc", "dome"):
            qf["domain"] = w.upper()
        elif MSG_RE.match(w):
            qf["msg"].append(w)
        elif CCY_RE.match(w):
            qf["ccy"].append(w.upper())
        elif w not in vocab.stopwords:
            qf["text"].append(w)
    return qf


def _field_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT field_id, source_column FROM field_catalogue").fetchall()
    return {r["field_id"]: r["source_column"] for r in rows}


def _searchable_columns(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT source_column FROM field_catalogue WHERE searchable = 1").fetchall()
    return [r["source_column"] for r in rows]


def build_where(
    conn: sqlite3.Connection,
    *,
    mode: str = "ALL",
    domain: Optional[str] = None,
    rail: Optional[str] = None,
    status: Optional[str] = None,
    msg: Optional[str] = None,
    ccy: Optional[str] = None,
    min_amount: Optional[float] = None,
    query_text: Optional[str] = None,
    include_simulated: bool = False,
) -> tuple[str, list]:
    conditions: list[str] = []
    params: list = []

    if not include_simulated:
        conditions.append("is_simulated = 0")
    if mode in ("CBCC", "DOME"):
        conditions.append("domain = ?")
        params.append(mode)
    if domain:
        conditions.append("domain = ?")
        params.append(domain)
    if rail:
        conditions.append("rail = ?")
        params.append(rail)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if msg:
        conditions.append("message_type = ?")
        params.append(msg)
    if ccy:
        conditions.append("(debit_ccy = ? OR credit_ccy = ?)")
        params.extend([ccy, ccy])
    if min_amount:
        conditions.append("amount >= ?")
        params.append(min_amount)

    if query_text:
        qf = parse_query(query_text, Vocabulary(conn))
        if qf["status"]:
            placeholders = ",".join("?" * len(qf["status"]))
            conditions.append(f"(status IN ({placeholders}) OR intermediate_status IN ({placeholders}))")
            params.extend(qf["status"] * 2)
        if qf["rail"]:
            placeholders = ",".join("?" * len(qf["rail"]))
            conditions.append(f"rail IN ({placeholders})")
            params.extend(qf["rail"])
        if qf["domain"]:
            conditions.append("domain = ?")
            params.append(qf["domain"])
        if qf["msg"]:
            placeholders = ",".join("?" * len(qf["msg"]))
            conditions.append(f"message_type IN ({placeholders})")
            params.extend(qf["msg"])
        if qf["ccy"]:
            placeholders = ",".join("?" * len(qf["ccy"]))
            conditions.append(f"(debit_ccy IN ({placeholders}) OR credit_ccy IN ({placeholders}))")
            params.extend(qf["ccy"] * 2)
        if qf["min"] is not None:
            conditions.append("amount >= ?")
            params.append(qf["min"])
        if qf["max"] is not None:
            conditions.append("amount <= ?")
            params.append(qf["max"])
        for k, v in qf["keys"].items():
            expr = KEY_EXPR.get(k)
            if expr:
                conditions.append(f"LOWER({expr}) LIKE ?")
                params.append(f"%{v.lower()}%")
        if qf["text"]:
            searchable = _searchable_columns(conn)
            for t in qf["text"]:
                token_or = " OR ".join(f"LOWER({col}) LIKE ?" for col in searchable)
                conditions.append(f"({token_or})")
                params.extend([f"%{t}%"] * len(searchable))

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where_sql, params


def search_payments(
    conn: sqlite3.Connection,
    *,
    mode: str = "ALL",
    domain: Optional[str] = None,
    rail: Optional[str] = None,
    status: Optional[str] = None,
    msg: Optional[str] = None,
    ccy: Optional[str] = None,
    min_amount: Optional[float] = None,
    query_text: Optional[str] = None,
    sort_key: Optional[str] = None,
    sort_dir: int = 1,
    cols: Optional[list[str]] = None,
    page: int = 1,
    page_size: int = 25,
    include_simulated: bool = False,
) -> dict:
    field_map = _field_map(conn)
    where_sql, params = build_where(
        conn, mode=mode, domain=domain, rail=rail, status=status, msg=msg, ccy=ccy,
        min_amount=min_amount, query_text=query_text, include_simulated=include_simulated,
    )

    total = conn.execute(f"SELECT COUNT(*) FROM payment {where_sql}", params).fetchone()[0]

    notional_row = conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) FROM payment {where_sql}{' AND' if where_sql else 'WHERE'} debit_ccy = 'USD'",
        params,
    ).fetchone()
    exceptions_row = conn.execute(
        f"SELECT COUNT(*) FROM payment {where_sql}"
        f"{' AND' if where_sql else 'WHERE'} status IN ('REJECTED','RETURNED','FAILED','INVESTIGATION','CANCELLED')",
        params,
    ).fetchone()

    order_sql = ""
    if sort_key and sort_key in field_map:
        order_sql = f"ORDER BY {field_map[sort_key]} {'DESC' if sort_dir == -1 else 'ASC'}"

    selected = [c for c in (cols or []) if c in field_map] or list(field_map.keys())[:10]
    select_sql = ", ".join(f"{field_map[c]} AS {c}" for c in selected)

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"SELECT {select_sql} FROM payment {where_sql} {order_sql} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    return {
        "rows": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "columns": selected,
        "stats": {
            "notional_usd": notional_row[0],
            "exceptions_count": exceptions_row[0],
        },
    }


def search_payments_for_export(conn: sqlite3.Connection, *, cols: list[str], **filters) -> list[dict]:
    field_map = _field_map(conn)
    where_sql, params = build_where(conn, **filters)
    selected = [c for c in cols if c in field_map] or list(field_map.keys())
    select_sql = ", ".join(f"{field_map[c]} AS {c}" for c in selected)
    rows = conn.execute(f"SELECT {select_sql} FROM payment {where_sql}", params).fetchall()
    return [dict(r) for r in rows]
