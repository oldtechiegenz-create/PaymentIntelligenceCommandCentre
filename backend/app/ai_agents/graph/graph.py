"""Kuber Agentic Operations \u2014 minimal LangGraph: router (LLM-based) -> specialist
(bound to a curated, per-agent read-only MCP tool subset) -> tool-call loop ->
summarizer. See agents.py for the (smaller, v1) specialist roster. Evidence completeness
is computed deterministically (confidence.py); the answer's confidence is reported by the
LLM in the summarizer's structured output."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Optional

from fastmcp import Client
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from app.ai_agents.graph.agents import SPECIALISTS
from app.ai_agents.graph.confidence import compute_evidence
from app.ai_agents.graph.schemas import RouterOutput, SummarizerOutput
from app.ai_agents.graph.skills import load_skill
from app.ai_agents.llm.factory import get_llm
from app.ai_agents.mcp_client import load_mcp_tools
from app.ai_agents.mcp_servers.kuber_mcp import current_db_path
from app.db.connection import get_connection
from app.payments.detail import get_payment_detail

DEFAULT_MAX_TOOL_LOOPS = int(os.environ.get("KUBER_MAX_TOOL_LOOPS", "3"))


class KuberAgentState(TypedDict):
    payment_id: str
    question: str
    messages: Annotated[list[BaseMessage], add_messages]
    routing_key: Optional[str]
    routing_reasoning: Optional[str]
    final_answer: Optional[str]
    evidence_used: Optional[list[str]]
    confidence: Optional[float]
    tool_call_count: int


def _without_unanswered_tool_calls(messages: list[BaseMessage]) -> tuple[list[BaseMessage], bool]:
    """When the tool-loop cap is hit, the specialist's last message can still be a request
    for tool calls that will never run. Providers such as Anthropic reject a conversation
    containing a tool request with no matching result, so that trailing request is removed
    (keeping any plain text the model wrote alongside it). Returns the cleaned messages and
    whether anything was dropped."""
    if messages and isinstance(messages[-1], AIMessage) and messages[-1].tool_calls:
        content = messages[-1].content
        if isinstance(content, list):  # Anthropic keeps tool_use blocks inside content too
            content = "".join(
                b if isinstance(b, str) else b.get("text", "") for b in content
                if isinstance(b, str) or b.get("type") == "text"
            )
        kept = [AIMessage(content=content)] if content.strip() else []
        return list(messages[:-1]) + kept, True
    return list(messages), False


def _router_prompt(question: str) -> str:
    lines = [f"- {key}: {cfg['role']}" for key, cfg in SPECIALISTS.items()]
    return (
        f"You are the Kuber Orchestrator. Pick exactly one specialist to answer this "
        f"question about a payment: {question!r}\n\nSpecialists:\n" + "\n".join(lines)
    )


async def build_kuber_graph(
    mcp_client: Client,
    provider: Optional[str] = None,
    max_tool_loops: int = DEFAULT_MAX_TOOL_LOOPS,
    forced_agent_key: Optional[str] = None,
):
    """Builds and compiles the graph. `mcp_client` must already be connected (async
    context manager entered) — pass an in-memory `Client(kuber_mcp.mcp)` in tests, or
    `Client("http://localhost:7020/mcp")` for the real running server. `max_tool_loops`
    caps how many tool-call round-trips the specialist can make before being forced to
    summarize — a deliberate guard against an LLM looping indefinitely on tool calls,
    independent of LangGraph's own blunter default recursion limit. `forced_agent_key`,
    when set to anything other than `"orchestrator"`, bypasses `router_node` entirely
    (a user's explicit agent selection is authoritative — see Implementation Plan §2);
    the caller must pre-populate `routing_key` in the initial state in that case."""
    all_tool_names = sorted({name for cfg in SPECIALISTS.values() for name in cfg["tool_names"]})
    all_tools = await load_mcp_tools(mcp_client, all_tool_names)
    tools_by_name = {t.name: t for t in all_tools}

    router_llm = get_llm(provider=provider).with_structured_output(RouterOutput)
    summarizer_llm = get_llm(provider=provider).with_structured_output(SummarizerOutput)

    async def router_node(state: KuberAgentState) -> dict:
        result: RouterOutput = await router_llm.ainvoke([HumanMessage(content=_router_prompt(state["question"]))])
        return {"routing_key": result.routing_key, "routing_reasoning": result.reasoning}

    async def specialist_node(state: KuberAgentState) -> dict:
        specialist = SPECIALISTS[state["routing_key"]]
        specialist_tools = [tools_by_name[n] for n in specialist["tool_names"] if n in tools_by_name]
        llm = get_llm(provider=provider).bind_tools(specialist_tools)

        if not state["messages"]:
            system = SystemMessage(content=(
                f"You are the {specialist['label']} ({specialist['role']}). "
                f"Answer questions about payment {state['payment_id']} using only your tools.\n\n"
                + load_skill(state["routing_key"])
            ))
            human = HumanMessage(content=state["question"])
            response = await llm.ainvoke([system, human])
            return {"messages": [system, human, response]}

        response = await llm.ainvoke(state["messages"])
        return {"messages": [response], "tool_call_count": state["tool_call_count"] + 1}

    def after_specialist(state: KuberAgentState) -> str:
        last = state["messages"][-1]
        has_tool_calls = isinstance(last, AIMessage) and bool(last.tool_calls)
        if has_tool_calls and state["tool_call_count"] < max_tool_loops:
            return "tool_call"
        return "summarize"

    async def tool_node(state: KuberAgentState) -> dict:
        """Runs the specialist's requested tool calls, enforcing its tool scope at
        execution time: binding only *advertises* a subset to the model, so a hallucinated
        call to an out-of-scope tool must be refused here rather than silently executed."""
        specialist = SPECIALISTS[state["routing_key"]]
        allowed = list(specialist["tool_names"])
        last = state["messages"][-1]

        async def run_one(call: dict) -> ToolMessage:
            name = call["name"]
            if name not in allowed or name not in tools_by_name:
                error = {"error": f"Tool {name!r} is not available to the {specialist['label']}; available tools: {allowed}"}
                return ToolMessage(content=json.dumps(error), tool_call_id=call["id"], name=name, status="error")
            try:
                return await tools_by_name[name].ainvoke({**call, "type": "tool_call"})
            except Exception as exc:  # e.g. the model passed arguments that fail schema validation
                error = {"error": f"{name} call failed: {exc}"}
                return ToolMessage(content=json.dumps(error), tool_call_id=call["id"], name=name, status="error")

        return {"messages": list(await asyncio.gather(*(run_one(c) for c in last.tool_calls)))}

    async def summarizer_node(state: KuberAgentState) -> dict:
        messages, truncated = _without_unanswered_tool_calls(state["messages"])
        instruction = (
            "Summarize your findings and answer the original question. Report your confidence "
            "(0.0-1.0) honestly: lower it if tool results were missing, returned errors, or only "
            "partly support the answer."
        )
        if truncated:
            instruction += (
                " Note: the tool-call limit was reached, so some lookups you requested were not run — "
                "answer only from the results you actually received and lower your confidence accordingly."
            )
        result: SummarizerOutput = await summarizer_llm.ainvoke(messages + [HumanMessage(content=instruction)])
        return {
            "final_answer": result.answer,
            "evidence_used": result.evidence_used,
            "confidence": round(result.confidence, 2),
        }

    graph = StateGraph(KuberAgentState)
    graph.add_node("router", router_node)
    graph.add_node("specialist", specialist_node)
    graph.add_node("tool_call", tool_node)
    graph.add_node("summarizer", summarizer_node)

    if forced_agent_key and forced_agent_key != "orchestrator":
        graph.add_edge(START, "specialist")
    else:
        graph.add_edge(START, "router")
        graph.add_edge("router", "specialist")
    graph.add_conditional_edges("specialist", after_specialist, {"tool_call": "tool_call", "summarize": "summarizer"})
    graph.add_edge("tool_call", "specialist")
    graph.add_edge("summarizer", END)

    return graph.compile()


async def run_kuber_agent(
    payment_id: str,
    question: str,
    mcp_client: Client,
    provider: Optional[str] = None,
    db_path: Optional[str] = None,
    max_tool_loops: int = DEFAULT_MAX_TOOL_LOOPS,
    agent_key: Optional[str] = None,
) -> dict:
    """Convenience entrypoint: builds the graph, runs it once, and attaches the
    deterministic evidence-completeness map; confidence is the LLM's own self-reported
    value from the summarizer. `db_path` overrides which DB evidence scoring reads from; defaults to whatever
    `kuber_mcp`'s tools are already using (see `kuber_mcp.use_db_path()`), so a test's
    temp DB is picked up automatically without passing this explicitly. `max_tool_loops`
    defaults to the KUBER_MAX_TOOL_LOOPS env var (fallback 3). `agent_key`, when a
    non-orchestrator specialist, is treated as the user's authoritative choice and
    skips the LLM router entirely (see Implementation Plan §2)."""
    forced = agent_key if agent_key and agent_key != "orchestrator" else None
    graph = await build_kuber_graph(mcp_client, provider=provider, max_tool_loops=max_tool_loops, forced_agent_key=forced)
    result = await graph.ainvoke({
        "payment_id": payment_id, "question": question, "messages": [],
        "routing_key": forced,
        "routing_reasoning": "Directly selected by the user." if forced else None,
        "final_answer": None, "evidence_used": None, "confidence": None,
        "tool_call_count": 0,
    })

    conn = get_connection(db_path or current_db_path())
    detail = get_payment_detail(conn, payment_id)
    conn.close()
    evidence = compute_evidence(detail["payment"], detail["hops"]) if detail else {}

    return {
        "answer": result["final_answer"],
        "routed_to": result["routing_key"],
        "routing_reasoning": result["routing_reasoning"],
        "evidence_used": result["evidence_used"],
        "evidence": evidence,
        "confidence": result["confidence"],
        "disclaimer": "No external action was executed. Human approval remains required.",
    }
