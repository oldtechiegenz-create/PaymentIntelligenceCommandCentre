"""Field catalogue + column presets \u2014 GET /fields backend.

Read-only reference data (seeded in Phase 1). `source_column` on each field maps
directly to a real `payment` table column, letting the search/sort/export layer
build queries generically instead of hand-writing 64 special cases.
"""
from __future__ import annotations

import json
import sqlite3


def get_categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT category_name, sort_order FROM field_category ORDER BY sort_order").fetchall()
    return [dict(r) for r in rows]


def get_fields(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT field_id, display_name, category_name, domain_code, message_types, data_type, "
        "filter_type, sortable, groupable, description, example, source_column, iso_path_hint "
        "FROM field_catalogue"
    ).fetchall()
    fields = []
    for r in rows:
        f = dict(r)
        f["message_types"] = json.loads(f["message_types"])
        fields.append(f)
    return fields


def get_column_presets(conn: sqlite3.Connection) -> list[dict]:
    presets = conn.execute("SELECT preset_code, preset_label, mode_code FROM column_preset").fetchall()
    result = []
    for p in presets:
        field_ids = [
            r["field_id"]
            for r in conn.execute(
                "SELECT field_id FROM column_preset_field WHERE preset_code = ? ORDER BY position",
                (p["preset_code"],),
            ).fetchall()
        ]
        result.append({"preset_code": p["preset_code"], "preset_label": p["preset_label"], "mode_code": p["mode_code"], "fields": field_ids})
    return result


def get_field_catalogue_response(conn: sqlite3.Connection) -> dict:
    return {
        "categories": get_categories(conn),
        "fields": get_fields(conn),
        "presets": get_column_presets(conn),
    }
