"""Kuber LangGraph \u2014 evidence scoring is tested as pure Python (no LLM
needed); the graph's routing/tool-call/summarize flow is tested end-to-end against
the real in-memory kuber_mcp server with a small scripted fake chat model standing in
for whichever real LLM provider would normally be used."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from fastmcp import Client
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.ai_agents.graph import graph as graph_module
from app.ai_agents.graph.confidence import compute_evidence
from app.ai_agents.graph.schemas import RouterOutput, SummarizerOutput
from app.ai_agents.mcp_servers import kuber_mcp
from app.db.connection import get_connection
from app.db.migrate import run_migration
from app.db.seed_reference_data import seed_reference_data
from app.payments.detail import get_payment_detail
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


def test_evidence_for_canonical_payment(db_path: Path) -> None:
    conn = get_connection(db_path)
    detail = get_payment_detail(conn, PAYMENT_ID)
    conn.close()

    evidence = compute_evidence(detail["payment"], detail["hops"])
    assert evidence["IDENTITY"] is True  # has paymentId + uetr (CBCC)
    assert evidence["LINEAGE"] is True  # correspondent + intermediary hops


def test_summarizer_output_normalizes_and_drops_unknown_evidence_categories() -> None:
    out = SummarizerOutput(answer="x", evidence_used=["risk", " State ", "RISK", "NOT_A_CATEGORY"], confidence=0.5)
    assert out.evidence_used == ["RISK", "STATE"]


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_summarizer_output_rejects_out_of_range_confidence(bad: float) -> None:
    with pytest.raises(ValidationError):
        SummarizerOutput(answer="x", evidence_used=[], confidence=bad)


class _FakeToolCaller:
    """Scripted: first ainvoke() requests a tool call, the second returns a final answer."""

    def __init__(self, tool_name: str, tool_args: dict) -> None:
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._called = False

    async def ainvoke(self, messages):
        if not self._called:
            self._called = True
            return AIMessage(content="", tool_calls=[{"name": self._tool_name, "args": self._tool_args, "id": "call_1"}])
        return AIMessage(content="Screening is CLEARED, risk score is low.")


class _FakeStructured:
    def __init__(self, value) -> None:
        self._value = value

    async def ainvoke(self, messages):
        return self._value


class _FakeChatModel:
    def __init__(self, router_output: RouterOutput, summarizer_output: SummarizerOutput, tool_name: str, tool_args: dict) -> None:
        self._router_output = router_output
        self._summarizer_output = summarizer_output
        self._tool_caller = _FakeToolCaller(tool_name, tool_args)

    def with_structured_output(self, schema):
        return _FakeStructured(self._router_output if schema is RouterOutput else self._summarizer_output)

    def bind_tools(self, tools):
        return self._tool_caller


@pytest.mark.asyncio
async def test_graph_routes_calls_tool_and_summarizes(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeChatModel(
        router_output=RouterOutput(routing_key="risk", reasoning="Question is about screening."),
        summarizer_output=SummarizerOutput(answer="No sanctions exposure found.", evidence_used=["RISK"], confidence=0.81),
        tool_name="get_risk_screening",
        tool_args={"payment_id": PAYMENT_ID},
    )
    monkeypatch.setattr(graph_module, "get_llm", lambda provider=None: fake)

    async with Client(kuber_mcp.mcp) as client:
        result = await graph_module.run_kuber_agent(
            PAYMENT_ID, "Any sanctions exposure?", client, db_path=str(db_path)
        )

    assert result["routed_to"] == "risk"
    assert result["answer"] == "No sanctions exposure found."
    assert result["evidence_used"] == ["RISK"]
    assert result["confidence"] == 0.81  # LLM-reported, not computed from payment fields
    assert result["evidence"]["IDENTITY"] is True
    assert result["disclaimer"] == "No external action was executed. Human approval remains required."


class _AlwaysToolCaller:
    """Never stops requesting tool calls \u2014 used to prove the max-tool-loop guard
    actually terminates the ReAct loop rather than relying on the model to behave."""

    def __init__(self, tool_name: str, tool_args: dict) -> None:
        self._tool_name = tool_name
        self._tool_args = tool_args
        self.call_count = 0

    async def ainvoke(self, messages):
        self.call_count += 1
        return AIMessage(content="", tool_calls=[{"name": self._tool_name, "args": self._tool_args, "id": f"call_{self.call_count}"}])


class _FakeChatModelAlwaysCallingTools:
    def __init__(self, router_output: RouterOutput, summarizer_output: SummarizerOutput, tool_name: str, tool_args: dict) -> None:
        self._router_output = router_output
        self._summarizer_output = summarizer_output
        self.tool_caller = _AlwaysToolCaller(tool_name, tool_args)

    def with_structured_output(self, schema):
        return _FakeStructured(self._router_output if schema is RouterOutput else self._summarizer_output)

    def bind_tools(self, tools):
        return self.tool_caller


@pytest.mark.asyncio
async def test_max_tool_loops_terminates_an_infinitely_tool_calling_model(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeChatModelAlwaysCallingTools(
        router_output=RouterOutput(routing_key="risk", reasoning="Screening question."),
        summarizer_output=SummarizerOutput(answer="Reached the loop cap.", evidence_used=["RISK"], confidence=0.81),
        tool_name="get_risk_screening",
        tool_args={"payment_id": PAYMENT_ID},
    )
    monkeypatch.setattr(graph_module, "get_llm", lambda provider=None: fake)

    async with Client(kuber_mcp.mcp) as client:
        result = await graph_module.run_kuber_agent(
            PAYMENT_ID, "Any sanctions exposure?", client, db_path=str(db_path), max_tool_loops=3,
        )

    assert result["answer"] == "Reached the loop cap."
    assert fake.tool_caller.call_count == 4  # 1 initial specialist call + 3 capped loop iterations


@pytest.mark.asyncio
async def test_max_tool_loops_override_terminates_sooner(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeChatModelAlwaysCallingTools(
        router_output=RouterOutput(routing_key="risk", reasoning="Screening question."),
        summarizer_output=SummarizerOutput(answer="Stopped after one loop.", evidence_used=["RISK"], confidence=0.81),
        tool_name="get_risk_screening",
        tool_args={"payment_id": PAYMENT_ID},
    )
    monkeypatch.setattr(graph_module, "get_llm", lambda provider=None: fake)

    async with Client(kuber_mcp.mcp) as client:
        result = await graph_module.run_kuber_agent(
            PAYMENT_ID, "Any sanctions exposure?", client, db_path=str(db_path), max_tool_loops=1,
        )

    assert result["answer"] == "Stopped after one loop."
    assert fake.tool_caller.call_count == 2  # 1 initial specialist call + 1 capped loop iteration


class _RouterTrackingFakeChatModel(_FakeChatModel):
    """Same as _FakeChatModel, but records every time the router's structured-output
    model is actually invoked \u2014 lets a test prove the router was (or wasn't) run."""

    def __init__(self, router_calls: list, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._router_calls = router_calls

    def with_structured_output(self, schema):
        structured = super().with_structured_output(schema)
        if schema is RouterOutput:
            original_ainvoke = structured.ainvoke

            async def tracked(messages):
                self._router_calls.append(True)
                return await original_ainvoke(messages)

            structured.ainvoke = tracked
        return structured


@pytest.mark.asyncio
async def test_forced_agent_key_bypasses_router(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    router_calls: list = []
    fake = _RouterTrackingFakeChatModel(
        router_calls,
        router_output=RouterOutput(routing_key="risk", reasoning="unused \u2014 router should never run"),
        summarizer_output=SummarizerOutput(answer="Direct risk answer.", evidence_used=["RISK"], confidence=0.81),
        tool_name="get_risk_screening",
        tool_args={"payment_id": PAYMENT_ID},
    )
    monkeypatch.setattr(graph_module, "get_llm", lambda provider=None: fake)

    async with Client(kuber_mcp.mcp) as client:
        result = await graph_module.run_kuber_agent(
            PAYMENT_ID, "Any sanctions exposure?", client, db_path=str(db_path), agent_key="risk",
        )

    assert router_calls == []
    assert result["routed_to"] == "risk"
    assert result["routing_reasoning"] == "Directly selected by the user."
    assert result["answer"] == "Direct risk answer."


@pytest.mark.asyncio
async def test_agent_key_orchestrator_still_runs_router(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    router_calls: list = []
    fake = _RouterTrackingFakeChatModel(
        router_calls,
        router_output=RouterOutput(routing_key="investigator", reasoning="Routed by orchestrator."),
        summarizer_output=SummarizerOutput(answer="Routed answer.", evidence_used=["STATE"], confidence=0.81),
        tool_name="get_payment_state",
        tool_args={"payment_id": PAYMENT_ID},
    )
    monkeypatch.setattr(graph_module, "get_llm", lambda provider=None: fake)

    async with Client(kuber_mcp.mcp) as client:
        result = await graph_module.run_kuber_agent(
            PAYMENT_ID, "What's going on?", client, db_path=str(db_path), agent_key="orchestrator",
        )

    assert router_calls == [True]
    assert result["routed_to"] == "investigator"
    assert result["answer"] == "Routed answer."


class _RecordingToolCaller:
    """Requests one tool call, then answers \u2014 and keeps the messages it was handed on
    the second call, so a test can inspect the ToolMessage the graph produced."""

    def __init__(self, tool_name: str, tool_args: dict) -> None:
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._called = False
        self.second_call_messages: list = []

    async def ainvoke(self, messages):
        if not self._called:
            self._called = True
            return AIMessage(content="", tool_calls=[{"name": self._tool_name, "args": self._tool_args, "id": "call_1"}])
        self.second_call_messages = list(messages)
        return AIMessage(content="done")


class _RecordingFakeChatModel(_FakeChatModel):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._tool_caller = _RecordingToolCaller(args[2] if len(args) > 2 else kwargs["tool_name"],
                                                 args[3] if len(args) > 3 else kwargs["tool_args"])

    @property
    def recorder(self) -> _RecordingToolCaller:
        return self._tool_caller


async def _run_with_tool_call(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, agent_key: str, tool_name: str, payment_id: str = PAYMENT_ID
) -> list:
    fake = _RecordingFakeChatModel(
        router_output=RouterOutput(routing_key="risk", reasoning="unused"),
        summarizer_output=SummarizerOutput(answer="ok", evidence_used=[], confidence=0.7),
        tool_name=tool_name,
        tool_args={"payment_id": payment_id},
    )
    monkeypatch.setattr(graph_module, "get_llm", lambda provider=None: fake)
    async with Client(kuber_mcp.mcp) as client:
        await graph_module.run_kuber_agent(payment_id, "q", client, db_path=str(db_path), agent_key=agent_key)
    return fake.recorder.second_call_messages


@pytest.mark.asyncio
async def test_out_of_scope_tool_call_is_refused_not_executed(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `risk` is only scoped to get_payment_identity / get_risk_screening; a hallucinated call
    # to get_payment_events (a real tool, but not risk's) must be refused at execution time.
    messages = await _run_with_tool_call(db_path, monkeypatch, "risk", "get_payment_events")
    tool_msg = messages[-1]
    assert tool_msg.status == "error"
    assert "not available" in tool_msg.content
    assert "events" not in tool_msg.content.replace("get_payment_events", "")  # no real event data leaked


@pytest.mark.asyncio
async def test_in_scope_tool_call_still_executes(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    messages = await _run_with_tool_call(db_path, monkeypatch, "risk", "get_risk_screening")
    tool_msg = messages[-1]
    assert tool_msg.status != "error"
    assert "screening" in tool_msg.content


TIGHT_PAYMENT_ID = "PAY-FEDWIRE-000001"  # seeded with liquidity_state TIGHT on the Fed master account


@pytest.mark.asyncio
async def test_router_can_route_to_liquidity_and_its_tool_returns_real_data(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    router_calls: list = []
    fake = _RouterTrackingFakeChatModel(
        router_calls,
        router_output=RouterOutput(routing_key="liquidity", reasoning="Question is about the nostro position."),
        summarizer_output=SummarizerOutput(answer="Liquidity is TIGHT on the Fed master account.", evidence_used=["LIQUIDITY"], confidence=0.9),
        tool_name="get_liquidity_position",
        tool_args={"payment_id": TIGHT_PAYMENT_ID},
    )
    monkeypatch.setattr(graph_module, "get_llm", lambda provider=None: fake)

    async with Client(kuber_mcp.mcp) as client:
        result = await graph_module.run_kuber_agent(TIGHT_PAYMENT_ID, "What is the nostro position?", client, db_path=str(db_path))

    assert router_calls == [True]
    assert result["routed_to"] == "liquidity"
    assert result["evidence_used"] == ["LIQUIDITY"]


@pytest.mark.asyncio
async def test_liquidity_tool_returns_tight_state_for_the_seeded_tight_payment(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    messages = await _run_with_tool_call(db_path, monkeypatch, "liquidity", "get_liquidity_position", TIGHT_PAYMENT_ID)
    tool_msg = messages[-1]
    assert tool_msg.status != "error"
    assert "TIGHT" in tool_msg.content
    assert "FED MASTER" in tool_msg.content


@pytest.mark.asyncio
async def test_forced_liquidity_agent_bypasses_router(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    router_calls: list = []
    fake = _RouterTrackingFakeChatModel(
        router_calls,
        router_output=RouterOutput(routing_key="risk", reasoning="unused \u2014 router should never run"),
        summarizer_output=SummarizerOutput(answer="Direct liquidity answer.", evidence_used=["LIQUIDITY"], confidence=0.8),
        tool_name="get_liquidity_position",
        tool_args={"payment_id": PAYMENT_ID},
    )
    monkeypatch.setattr(graph_module, "get_llm", lambda provider=None: fake)

    async with Client(kuber_mcp.mcp) as client:
        result = await graph_module.run_kuber_agent(PAYMENT_ID, "Liquidity impact?", client, db_path=str(db_path), agent_key="liquidity")

    assert router_calls == []
    assert result["routed_to"] == "liquidity"


@pytest.mark.asyncio
async def test_liquidity_agent_cannot_call_another_agents_tool(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    messages = await _run_with_tool_call(db_path, monkeypatch, "liquidity", "get_risk_screening")
    tool_msg = messages[-1]
    assert tool_msg.status == "error"
    assert "not available" in tool_msg.content


def test_router_accepts_every_roster_key_and_rejects_unknown_ones() -> None:
    from app.ai_agents.graph.agents import SPECIALISTS

    for key in SPECIALISTS:
        assert RouterOutput(routing_key=key, reasoning="x").routing_key == key
    with pytest.raises(ValidationError):
        RouterOutput(routing_key="not-an-agent", reasoning="x")


def _unanswered_tool_call_ids(messages: list) -> set[str]:
    requested = {c["id"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    return requested - answered


def test_without_unanswered_tool_calls_drops_trailing_request_but_keeps_its_text() -> None:
    call = {"name": "get_risk_screening", "args": {}, "id": "c1"}
    base = [HumanMessage(content="q")]

    cleaned, dropped = graph_module._without_unanswered_tool_calls(base + [AIMessage(content="", tool_calls=[call])])
    assert dropped and cleaned == base

    # Anthropic-style: tool_use blocks live inside content as well as in tool_calls
    blocks = [{"type": "text", "text": "Checking screening."}, {"type": "tool_use", "id": "c1", "name": "x", "input": {}}]
    cleaned, dropped = graph_module._without_unanswered_tool_calls(base + [AIMessage(content=blocks, tool_calls=[call])])
    assert dropped and len(cleaned) == 2
    assert cleaned[-1].content == "Checking screening." and not cleaned[-1].tool_calls

    plain = base + [AIMessage(content="final text")]
    cleaned, dropped = graph_module._without_unanswered_tool_calls(plain)
    assert not dropped and cleaned == plain


class _RecordingStructured:
    def __init__(self, value) -> None:
        self._value = value
        self.seen: list = []

    async def ainvoke(self, messages):
        self.seen = list(messages)
        return self._value


class _CapHitFakeChatModel(_FakeChatModelAlwaysCallingTools):
    """Like the always-tool-calling model, but records what the summarizer is sent."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.summarizer = _RecordingStructured(self._summarizer_output)

    def with_structured_output(self, schema):
        return self.summarizer if schema is SummarizerOutput else _FakeStructured(self._router_output)


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [0, 1, 3])
async def test_summarizer_never_receives_an_unanswered_tool_request(db_path: Path, monkeypatch: pytest.MonkeyPatch, cap: int) -> None:
    fake = _CapHitFakeChatModel(
        router_output=RouterOutput(routing_key="risk", reasoning="x"),
        summarizer_output=SummarizerOutput(answer="capped", evidence_used=[], confidence=0.2),
        tool_name="get_risk_screening",
        tool_args={"payment_id": PAYMENT_ID},
    )
    monkeypatch.setattr(graph_module, "get_llm", lambda provider=None: fake)

    async with Client(kuber_mcp.mcp) as client:
        result = await graph_module.run_kuber_agent(PAYMENT_ID, "q", client, db_path=str(db_path), max_tool_loops=cap, agent_key="risk")

    assert result["answer"] == "capped"
    assert _unanswered_tool_call_ids(fake.summarizer.seen) == set()
    assert "tool-call limit was reached" in fake.summarizer.seen[-1].content
