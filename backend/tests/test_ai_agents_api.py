"""POST /kuber/ask \u2014 the endpoint wires request/response around run_kuber_agent();
LLM calls are stubbed with the same scripted fake chat model used in
test_ai_agents_graph.py so this runs without any real provider credentials."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.ai_agents.graph import graph as graph_module
from app.ai_agents.graph.schemas import RouterOutput, SummarizerOutput
from app.ai_agents.mcp_servers import kuber_mcp
from app.db.connection import get_connection, get_db_connection
from app.db.migrate import run_migration
from app.db.seed_reference_data import seed_reference_data
from app.main import app
from app.seed.generator import seed_payments

PAYMENT_ID = "PAY-CB-000001"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    run_migration(path)
    seed_reference_data(path)
    seed_payments(path)
    kuber_mcp.use_db_path(path)
    yield path
    kuber_mcp.use_db_path(None)


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


class _FakeToolCaller:
    def __init__(self, tool_name: str, tool_args: dict) -> None:
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._called = False

    async def ainvoke(self, messages):
        if not self._called:
            self._called = True
            return AIMessage(content="", tool_calls=[{"name": self._tool_name, "args": self._tool_args, "id": "call_1"}])
        return AIMessage(content="done")


class _FakeStructured:
    def __init__(self, value) -> None:
        self._value = value

    async def ainvoke(self, messages):
        return self._value


class _FakeChatModel:
    def __init__(self, router_output, summarizer_output, tool_name, tool_args) -> None:
        self._router_output = router_output
        self._summarizer_output = summarizer_output
        self._tool_caller = _FakeToolCaller(tool_name, tool_args)

    def with_structured_output(self, schema):
        return _FakeStructured(self._router_output if schema is RouterOutput else self._summarizer_output)

    def bind_tools(self, tools):
        return self._tool_caller


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeChatModel(
        router_output=RouterOutput(routing_key="risk", reasoning="Screening question."),
        summarizer_output=SummarizerOutput(answer="No sanctions exposure found.", evidence_used=["RISK"], confidence=0.88),
        tool_name="get_risk_screening",
        tool_args={"payment_id": PAYMENT_ID},
    )
    monkeypatch.setattr(graph_module, "get_llm", lambda provider=None: fake)


def test_kuber_agents_lists_roster(client: TestClient) -> None:
    resp = client.get("/kuber/agents")
    assert resp.status_code == 200
    keys = {a["agentKey"] for a in resp.json()}
    assert keys == {"orchestrator", "investigator", "risk", "liquidity"}


def test_kuber_ask_unknown_payment_is_404(client: TestClient) -> None:
    resp = client.post("/kuber/ask", json={"payment_id": "PAY-DOES-NOT-EXIST", "question": "Explain this payment"})
    assert resp.status_code == 404


def test_kuber_ask_returns_answer_evidence_and_confidence(client: TestClient) -> None:
    resp = client.post("/kuber/ask", json={"payment_id": PAYMENT_ID, "question": "Any sanctions exposure?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["routed_to"] == "risk"
    assert body["answer"] == "No sanctions exposure found."
    assert body["evidence_used"] == ["RISK"]
    assert body["confidence"] == 0.88  # the LLM's own reported value, passed through unchanged
    assert body["disclaimer"] == "No external action was executed. Human approval remains required."
