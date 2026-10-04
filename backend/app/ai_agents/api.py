"""Kuber Agentic Operations REST API \u2014 POST /kuber/ask, the sole entrypoint into the
LangGraph. Async (unlike the rest of this app's sync routes) because the underlying
work \u2014 LLM calls, MCP tool calls \u2014 is genuinely async I/O, not a SQLite read."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastmcp import Client
from pydantic import BaseModel, Field, field_validator
import sqlite3

from app.ai_agents.graph.agents import SPECIALISTS
from app.ai_agents.graph.graph import run_kuber_agent
from app.ai_agents.mcp_servers import kuber_mcp
from app.db.connection import get_db_connection
from app.payments.detail import get_payment_detail

router = APIRouter()


class KuberAskRequest(BaseModel):
    payment_id: str
    question: str = Field(min_length=1)
    provider: Optional[str] = None
    agent_key: Optional[str] = None

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be blank")
        return v

    @field_validator("agent_key")
    @classmethod
    def _agent_key_must_be_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in SPECIALISTS:
            raise ValueError(f"Unknown agent_key {v!r}; choose one of {sorted(SPECIALISTS)}")
        return v


@router.get("/kuber/agents")
def kuber_agents() -> list[dict]:
    return [{"agentKey": key, "label": cfg["label"], "role": cfg["role"]} for key, cfg in SPECIALISTS.items()]


@router.post("/kuber/ask")
async def kuber_ask(body: KuberAskRequest, conn: sqlite3.Connection = Depends(get_db_connection)) -> dict:
    if get_payment_detail(conn, body.payment_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown payment_id {body.payment_id!r}")

    async with Client(kuber_mcp.mcp) as client:
        return await run_kuber_agent(
            body.payment_id, body.question, client, provider=body.provider, agent_key=body.agent_key
        )
