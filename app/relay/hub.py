import asyncio
import json
import logging
import uuid

from fastapi import HTTPException
from starlette.websockets import WebSocket

logger = logging.getLogger("relay")

# Server-side allow-list for the diagnostics a relay client attaches to a
# response (`relay_logs`). Re-sanitise here rather than trust the client, so a
# buggy/compromised client can't make Xhuma log anything unexpected — only these
# known, non-PHI metadata fields are ever logged. Defence in depth.
_RELAY_LOG_FIELDS = (
    "ts", "level", "event", "request_id", "target_host", "method",
    "http_status", "duration_ms", "outcome", "reason_code", "client_version",
)


def _log_relay_diagnostics(req_id, entries) -> None:
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        safe = {k: entry[k] for k in _RELAY_LOG_FIELDS if entry.get(k) is not None}
        level = getattr(logging, str(safe.get("level", "INFO")).upper(), logging.INFO)
        logger.log(level, "relay diagnostics request_id=%s %s", req_id, safe)


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
        # Pull off the sanitised diagnostics and STRIP them before the response
        # is handed to the GP Connect path (which only reads status_code + body).
        relay_logs = response.pop("relay_logs", None)
        if relay_logs:
            _log_relay_diagnostics(req_id, relay_logs)

        status_code = response.get("status_code", "unknown")
        logger.info("relay response request_id=%s status=%s", req_id, status_code)

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
        except TimeoutError:
            self.pending.pop(req_id, None)
            raise HTTPException(504, "Agent timeout")
