"""Payment Discovery \u2014 field catalogue, structured filters, NL-ish query parsing, CSV export."""
from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_connection, get_db_connection
from app.db.migrate import run_migration
from app.db.seed_reference_data import seed_reference_data
from app.main import app
from app.seed.generator import seed_payments


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    run_migration(path)
    seed_reference_data(path)
    seed_payments(path)
    return path


@pytest.fixture()
def client(db_path: Path):
    def _override_db():
        conn = get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db_connection] = _override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_fields_catalogue_matches_poc(client: TestClient) -> None:
    body = client.get("/fields").json()
    assert len(body["categories"]) == 20
    assert len(body["fields"]) == 64
    preset_codes = {p["preset_code"] for p in body["presets"]}
    assert preset_codes == {"DEFAULT_ALL", "DEFAULT_CBCC", "DEFAULT_DOME", "Operations", "ISO 20022", "Network", "Risk & Liquidity"}


def test_structured_filters(client: TestClient) -> None:
    body = client.get("/payments?status=REJECTED&cols=paymentId,status").json()
    assert body["total"] == 1
    assert body["rows"][0]["paymentId"] == "PAY-RETURN-000001"


def test_mode_filter_matches_domain(client: TestClient) -> None:
    all_total = client.get("/payments").json()["total"]
    cbcc_total = client.get("/payments?mode=CBCC").json()["total"]
    dome_total = client.get("/payments?mode=DOME").json()["total"]
    assert cbcc_total + dome_total == all_total == 68


def test_nl_query_rejected_swift_over_1m(client: TestClient) -> None:
    """POC's own example query."""
    body = client.get("/payments", params={"q": "rejected swift over 1m", "cols": "paymentId,rail,status,amount"}).json()
    assert body["total"] == 1
    row = body["rows"][0]
    assert row["paymentId"] == "PAY-RETURN-000001"
    assert row["status"] == "REJECTED"
    assert row["rail"] == "SWIFT CBPR+"
    assert row["amount"] > 1_000_000


def test_nl_query_key_value_uetr_search(client: TestClient) -> None:
    body = client.get("/payments", params={"q": "uetr:a9f4c1e8", "cols": "paymentId,uetr"}).json()
    assert body["total"] == 1
    assert body["rows"][0]["paymentId"] == "PAY-CB-000001"


def test_nl_query_bic_substring_search(client: TestClient) -> None:
    body = client.get("/payments", params={"q": "bic:CHASUS33", "cols": "paymentId,bic"}).json()
    assert body["total"] >= 1
    assert all(r["bic"] == "CHASUS33" for r in body["rows"])


def test_nl_query_name_substring_search(client: TestClient) -> None:
    body = client.get("/payments", params={"q": "Global Trading", "cols": "paymentId,debtor"}).json()
    assert any("Global Trading" in r["debtor"] for r in body["rows"])


def test_pagination(client: TestClient) -> None:
    page1 = client.get("/payments?page=1&page_size=10&cols=paymentId").json()
    page2 = client.get("/payments?page=2&page_size=10&cols=paymentId").json()
    assert len(page1["rows"]) == 10
    assert len(page2["rows"]) == 10
    ids1 = {r["paymentId"] for r in page1["rows"]}
    ids2 = {r["paymentId"] for r in page2["rows"]}
    assert ids1.isdisjoint(ids2)


def test_sorting(client: TestClient) -> None:
    body = client.get("/payments?sort=amount&dir=-1&cols=paymentId,amount&page_size=68").json()
    amounts = [r["amount"] for r in body["rows"]]
    assert amounts == sorted(amounts, reverse=True)


def test_csv_export_row_count_matches_filtered_count(client: TestClient) -> None:
    filtered = client.get("/payments?status=COMPLETED").json()
    resp = client.get("/payments/export?status=COMPLETED&cols=paymentId,status")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    body = resp.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(body))
    rows = list(reader)
    assert rows[0] == ["Payment ID", "Payment Status"]
    assert len(rows) - 1 == filtered["total"]


def test_csv_export_has_utf8_bom_and_crlf(client: TestClient) -> None:
    resp = client.get("/payments/export?cols=paymentId")
    assert resp.content.startswith("\ufeff".encode("utf-8"))
    assert b"\r\n" in resp.content


def test_payment_detail_returns_real_identity_and_hops(client: TestClient) -> None:
    resp = client.get("/payments/PAY-CB-000001")
    assert resp.status_code == 200
    body = resp.json()
    p = body["payment"]
    assert p["paymentId"] == "PAY-CB-000001"
    assert p["uetr"]
    assert p["debtor"] and p["creditor"]
    # 4 real hops (debtor agent, correspondent, intermediary, creditor agent)
    assert [h["role"] for h in body["hops"]] == ["DEBTOR_AGENT", "CORRESPONDENT", "INTERMEDIARY", "CREDITOR_AGENT"]
    assert body["hops"][0]["bic"] == p["bic"]


def test_payment_detail_events_are_real_and_ordered(client: TestClient) -> None:
    body = client.get("/payments/PAY-RETURN-000001").json()
    events = body["events"]
    assert len(events) >= 2
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert events[-1]["state"] == "REJECTED"
    assert body["payment"]["status"] == "REJECTED"


def test_payment_detail_message_chain_derived_from_events(client: TestClient) -> None:
    body = client.get("/payments/PAY-CB-000001").json()
    real_chain: list[str] = []
    for e in body["events"]:
        msg = e["iso_message"]
        if msg and msg != "\u2014" and msg not in real_chain:
            real_chain.append(msg)
    assert body["messageChain"][: len(real_chain)] == real_chain



def test_payment_detail_unknown_id_is_404(client: TestClient) -> None:
    resp = client.get("/payments/PAY-DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_payment_detail_iso_messages_use_real_field_values(client: TestClient) -> None:
    body = client.get("/payments/PAY-CB-000001").json()
    p = body["payment"]
    pacs008 = body["messages"]["pacs.008"]
    assert p["uetr"] in pacs008
    assert p["businessMsgId"] in pacs008
    assert f"{p['amount']:.2f}" in pacs008
    assert set(body["messages"].keys()) == {
        "pain.001", "pacs.008", "pacs.009", "pacs.002", "pacs.004",
        "camt.052", "camt.053", "camt.054", "camt.056", "camt.029",
    }


def test_payment_detail_returned_payment_shows_return_reason(client: TestClient) -> None:
    body = client.get("/payments/PAY-RETURN-000001").json()
    pacs004 = body["messages"]["pacs.004"]
    assert "RtrRsnInf" in pacs004
    assert body["payment"]["endToEndId"] in pacs004

