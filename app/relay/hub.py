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
    "ts",
    "level",
    "event",
    "request_id",
    "target_host",
    "method",
    "http_status",
    "duration_ms",
    "outcome",
    "reason_code",
    "client_version",
)

_PERMITTED_LOG_LEVELS = {
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

_MAX_DIAGNOSTIC_ENTRIES = 10
_MAX_STRING_LENGTH = 256


def _log_relay_diagnostics(outer_req_id: str | None, entries: object) -> None:
    try:
        if not isinstance(entries, list):
            return

        # Bound number of diagnostic entries accepted per response
        bounded_entries = entries[:_MAX_DIAGNOSTIC_ENTRIES]
        authoritative_req_id = str(outer_req_id)[:_MAX_STRING_LENGTH] if outer_req_id is not None else None

        for entry in bounded_entries:
            if not isinstance(entry, dict):
                continue

            safe: dict[str, str | int | float | None] = {}
            for k in _RELAY_LOG_FIELDS:
                if k not in entry or entry[k] is None:
                    continue
                val = entry[k]
                if isinstance(val, str):
                    safe[k] = val[:_MAX_STRING_LENGTH]
                elif isinstance(val, (int, float)):
                    safe[k] = val
                elif isinstance(val, bool):
                    safe[k] = val
                else:
                    safe[k] = str(val)[:_MAX_STRING_LENGTH]

            # Authoritative outer request_id constraint: inner request_id cannot spoof outer ID
            if authoritative_req_id is not None:
                safe["request_id"] = authoritative_req_id

            # Constrain permitted log levels to INFO/WARNING/ERROR
            raw_level = str(safe.get("level", "INFO")).strip().upper()
            level = _PERMITTED_LOG_LEVELS.get(raw_level, logging.INFO)

            logger.log(
                level,
                "relay_diag %s",
                json.dumps(safe, separators=(",", ":"), sort_keys=True),
            )
    except Exception as e:
        logger.warning(
            "relay_event %s",
            json.dumps({"error": str(e)[:_MAX_STRING_LENGTH], "event": "relay_diag_error"}, sort_keys=True),
        )


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
                logger.info(
                    "relay_event %s",
                    json.dumps({"event": "relay_preempted"}, sort_keys=True),
                )
            self.ws = ws
            logger.info(
                "relay_event %s",
                json.dumps({"event": "relay_connected"}, sort_keys=True),
            )

    async def unregister(self, ws: WebSocket) -> None:
        async with self.lock:
            if self.ws is ws:
                self.ws = None
                logger.info(
                    "relay_event %s",
                    json.dumps({"event": "relay_disconnected"}, sort_keys=True),
                )

    def fulfill(self, response: dict) -> None:
        if not isinstance(response, dict):
            logger.warning(
                "relay_event %s",
                json.dumps({"event": "relay_invalid_response"}, sort_keys=True),
            )
            return

        req_id = response.get("request_id")
        # Pull off the sanitised diagnostics and STRIP them before the response
        # is handed to the GP Connect path (which only reads status_code + body).
        relay_logs = response.pop("relay_logs", None)
        if relay_logs is not None:
            _log_relay_diagnostics(req_id, relay_logs)

        status_code = response.get("status_code", "unknown")
        logger.info("relay response request_id=%s status=%s", req_id, status_code)

        fut = self.pending.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result(response)
        else:
            logger.warning(
                "relay_event %s",
                json.dumps(
                    {"event": "relay_orphan_response", "request_id": str(req_id)},
                    sort_keys=True,
                ),
            )

    async def send(self, relay_req: dict, timeout: int = 75) -> dict:
        ws = self.ws
        if not ws:
            logger.warning(
                "relay_event %s",
                json.dumps({"event": "relay_not_connected"}, sort_keys=True),
            )
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
            logger.error(
                "relay_event %s",
                json.dumps(
                    {"event": "relay_timeout", "request_id": str(req_id), "timeout": timeout},
                    sort_keys=True,
                ),
            )
            raise HTTPException(504, "Agent timeout")
