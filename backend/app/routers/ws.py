"""WebSocket endpoint for live updates. Auth via ?token= (browsers cannot set
headers on WS). Clients receive job/worker/queue events; the frontend falls
back to polling if the socket drops."""
import asyncio
import contextlib
import json

import jwt as pyjwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ..events import bus
from ..security import decode_token

router = APIRouter()


@router.websocket("/api/v1/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(...)):
    try:
        decode_token(token)
    except pyjwt.PyJWTError:
        await ws.close(code=4401)
        return
    await ws.accept()
    queue = bus.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
                await ws.send_text(json.dumps(event, default=str))
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "ping"}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await ws.close()
