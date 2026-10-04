"""Simulated network latency per step \u2014 loaded from the sim_latency_profile table seeded in
Phase 1 (config over hardcoding, not a second Python copy of the same numbers).
"""
from __future__ import annotations

import sqlite3

RAIL_TO_LATENCY_CLASS = {"SWIFT CBPR+": "XB", "ACH": "BATCH", "Fedwire": "WIRE"}  # everything else -> INSTANT


def latency_class(rail: str | None) -> str:
    return RAIL_TO_LATENCY_CLASS.get(rail or "", "INSTANT")


def fnv1a_hash(s: str) -> int:
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def load_latency_profile(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    rows = conn.execute("SELECT latency_class, state, base_ms FROM sim_latency_profile").fetchall()
    return {(r["latency_class"], r["state"]): r["base_ms"] for r in rows}


def step_latency(
    profile: dict[tuple[str, str], int],
    *,
    payment_id: str,
    rail: str | None,
    state: str,
    step_index: int,
    prev_state: str | None,
) -> int:
    base = profile.get((latency_class(rail), state), 0)
    if state == "SCREENING" and prev_state == "INVESTIGATION":
        base = 600000
    jitter = fnv1a_hash(f"{payment_id}|{step_index}{state}") % 600
    return round(base * (0.7 + jitter / 1000))


def format_ms(ms: int) -> str:
    if ms < 1000:
        return f"{round(ms)} ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    if ms < 3600000:
        return f"{ms // 60000}m {round((ms % 60000) / 1000)}s"
    return f"{ms / 3600000:.1f}h"
