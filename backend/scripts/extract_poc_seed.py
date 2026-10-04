"""ONE-TIME extraction tool: parses the reference kuber_v4_seed.sql's payment/payment_party/
payment_agent_hop/payment_event INSERT blocks into backend/app/seed/data/*.json (our own,
self-contained assets). Not part of the running app; delete after use.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SEED_SQL = Path(
    "/Users/khanvisa/Library/CloudStorage/OneDrive-DeutscheBankAG/Desktop/programming/"
    "payment-command-center/PaymentIntelligenceCommandCentre-POC/schema_package/kuber_v4_seed.sql"
)
OUT_DIR = Path(__file__).parent.parent / "app" / "seed" / "data"


def extract_insert_block(sql: str, table: str) -> tuple[list[str], str]:
    """Return (column_names, raw_values_text) for `INSERT INTO {table} (...) VALUES ...;`."""
    m = re.search(rf"INSERT INTO {re.escape(table)} \(([^)]*)\) VALUES\n", sql)
    if not m:
        raise ValueError(f"INSERT INTO {table} not found")
    columns = [c.strip() for c in m.group(1).split(",")]
    start = m.end()
    # Scan forward to find the terminating ';' that closes this statement (respecting quotes).
    depth = 0
    in_str = False
    i = start
    while i < len(sql):
        ch = sql[i]
        if in_str:
            if ch == "'":
                if sql[i + 1 : i + 2] == "'":
                    i += 2
                    continue
                in_str = False
        else:
            if ch == "'":
                in_str = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == ";" and depth == 0:
                break
        i += 1
    return columns, sql[start:i]


def split_top_level(s: str, sep: str = ",") -> list[str]:
    parts: list[str] = []
    depth = 0
    in_str = False
    buf = []
    i = 0
    while i < len(s):
        ch = s[i]
        if in_str:
            buf.append(ch)
            if ch == "'":
                if s[i + 1 : i + 2] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts]


def parse_scalar(tok: str):
    tok = tok.strip()
    if tok == "NULL":
        return None
    if tok.startswith("ARRAY[") :
        inner = tok[len("ARRAY[") : tok.rindex("]")]
        if not inner.strip():
            return []
        return [parse_scalar(x) for x in split_top_level(inner)]
    if tok.startswith("'") and (tok.endswith("'") or "::" in tok):
        end = tok.rindex("'")
        raw = tok[1:end]
        return raw.replace("''", "'")
    try:
        if re.fullmatch(r"-?\d+", tok):
            return int(tok)
        return float(tok)
    except ValueError:
        return tok


def parse_rows(columns: list[str], raw_values: str) -> list[dict]:
    rows = []
    for row_text in split_top_level(raw_values.strip().rstrip(","), sep="\n"):
        row_text = row_text.strip()
        if row_text.startswith(","):
            row_text = row_text[1:].strip()
        if not (row_text.startswith("(") and row_text.rstrip(",").endswith(")")):
            continue
        inner = row_text.strip()
        inner = inner[1 : inner.rfind(")")]
        values = [parse_scalar(v) for v in split_top_level(inner)]
        rows.append(dict(zip(columns, values)))
    return rows


def main() -> None:
    sql = SEED_SQL.read_text()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for table in ["payment", "payment_party", "payment_agent_hop", "investigation", "payment_event"]:
        columns, raw = extract_insert_block(sql, table)
        rows = parse_rows(columns, raw)
        out_path = OUT_DIR / f"{table}.json"
        out_path.write_text(json.dumps(rows, indent=1, default=str))
        print(f"{table}: {len(rows)} rows -> {out_path}")


if __name__ == "__main__":
    main()
