"""In-process Event Publisher \u2014 the shared notification point ingestion/ticker write to.

Sync pub/sub only. Phase 4's WebSocket registry subscribes to this same publisher;
nothing about this module changes when consumers come and go.
"""
from __future__ import annotations

from typing import Callable


class EventPublisher:
    def __init__(self) -> None:
        self._subscribers: list[Callable[[dict], None]] = []

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[dict], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def notify(self, event: dict) -> None:
        for callback in list(self._subscribers):
            callback(event)


# Module-level singleton \u2014 one publisher per running app instance.
publisher = EventPublisher()
