"""WebSocket broadcast registry \u2014 bridges the sync EventPublisher to async WS sends.

Broadcast to every connection, client-side filtering decides relevance (no
per-payment/per-UETR server-side rooms \u2014 same design PayTrace360 uses).
"""
from __future__ import annotations

import asyncio

from fastapi import WebSocket


class ConnectionRegistry:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """The main event loop, for other sync code (e.g. the simulation ticker)
        to schedule coroutines onto via run_coroutine_threadsafe."""
        return self._loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    def broadcast_sync(self, event: dict) -> None:
        """Called synchronously from EventPublisher.notify(); schedules the actual
        async sends on the main event loop from whatever thread notify() runs in."""
        if self._loop is None:
            return
        for websocket in list(self.connections):
            asyncio.run_coroutine_threadsafe(self._safe_send(websocket, event), self._loop)

    async def _safe_send(self, websocket: WebSocket, event: dict) -> None:
        try:
            await websocket.send_json(event)
        except Exception:
            self.disconnect(websocket)


# Module-level singleton \u2014 mirrors the publisher pattern above.
registry = ConnectionRegistry()
