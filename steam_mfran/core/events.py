"""Fan-out of server events to every connected UI websocket."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


class EventHub:
    def __init__(self, max_queue: int = 1000):
        self._clients: set[asyncio.Queue] = set()
        self._max = max_queue

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(self._max)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    def emit(self, type_: str, data: Any = None) -> None:
        event = {"type": type_, "data": data}
        for q in list(self._clients):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("Dropping slow UI client")
                self._clients.discard(q)
