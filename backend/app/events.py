"""Event bus for real-time updates.

In-process asyncio fan-out for the API process; when REDIS_URL is set the bus
also publishes to Redis Pub/Sub so worker/scheduler containers reach browser
WebSocket clients connected to the API container. The scheduler itself never
depends on this bus — it is observability only.
"""
import asyncio
import contextlib
import json
import logging
from typing import Any

from .config import get_settings
from .models import utcnow

log = logging.getLogger("chronosgrid.events")

CHANNEL = "chronosgrid:events"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._redis = None
        self._listener_task: asyncio.Task | None = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _fanout(self, event: dict) -> None:
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    async def emit(self, event_type: str, data: dict[str, Any],
                   project_id: str | None = None) -> None:
        event = {"type": event_type, "data": data, "project_id": project_id,
                 "at": utcnow().isoformat() + "Z"}
        self._fanout(event)
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.publish(CHANNEL, json.dumps(event, default=str))

    async def start(self) -> None:
        url = get_settings().redis_url
        if not url:
            return
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(url)
            await self._redis.ping()
            self._listener_task = asyncio.create_task(self._listen())
            log.info("event bus connected to redis")
        except Exception as exc:  # Redis is optional by design
            log.warning("redis unavailable, in-process events only: %s", exc)
            self._redis = None

    async def _listen(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        async for message in pubsub.listen():
            if message.get("type") == "message":
                with contextlib.suppress(Exception):
                    self._fanout(json.loads(message["data"]))

    async def stop(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()


bus = EventBus()
