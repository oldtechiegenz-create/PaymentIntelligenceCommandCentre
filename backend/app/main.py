"""FastAPI app entrypoint. Run with: uv run uvicorn app.main:app --reload"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.realtime.publisher import publisher
from app.realtime.registry import registry
from app.dashboard.api import router as dashboard_router
from app.drilldown.api import router as drilldown_router
from app.payments.api import router as payments_router
from app.simulation.api import router as simulation_router
from app.ai_agents.api import router as ai_agents_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry.set_loop(asyncio.get_running_loop())
    publisher.subscribe(registry.broadcast_sync)
    yield


app = FastAPI(title="Payment Command Center", lifespan=lifespan)

# Dev-only: allow the Vite dev server origin to call the REST API directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5180"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(simulation_router)
app.include_router(dashboard_router)
app.include_router(drilldown_router)
app.include_router(payments_router)
app.include_router(ai_agents_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await registry.connect(websocket)
    try:
        while True:
            # Client doesn't need to send anything; this just keeps the
            # connection open and detects disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        registry.disconnect(websocket)
