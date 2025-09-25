import asyncio
import json
import uuid

from fastapi import HTTPException
from starlette.websockets import WebSocket


class WebSocketHub:
    def __init__(self) -> None:
        self.ws: WebSocket | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        async with self.lock:
            # If another agent connects, drop the old one
            if self.ws and self.ws is not ws:
                try:
                    await self.ws.close(code=1001)
                except Exception:
                    pass
            self.ws = ws

    async def unregister(self, ws: WebSocket) -> None:
        async with self.lock:
            if self.ws is ws:
                self.ws = None

    def fulfill(self, response: dict) -> None:
        req_id = response.get("request_id")
        fut = self.pending.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result(response)

    async def send(self, relay_req: dict, timeout: int = 75) -> dict:
        ws = self.ws
        if not ws:
            raise HTTPException(404, "Agent not connected")

        relay_req.setdefault("request_id", str(uuid.uuid4()))
        req_id = relay_req["request_id"]

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending[req_id] = fut

        try:
            await ws.send_text(json.dumps(relay_req))
        except Exception:
            self.pending.pop(req_id, None)
            raise HTTPException(502, "Failed to send to agent")

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self.pending.pop(req_id, None)
            raise HTTPException(504, "Agent timeout")
