import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.relay.hub import (
    _MAX_DIAGNOSTIC_ENTRIES,
    _MAX_STRING_LENGTH,
    _RELAY_LOG_FIELDS,
    WebSocketHub,
    _log_relay_diagnostics,
)


def test_fulfill_strips_relay_logs_before_gpconnect():
    hub = WebSocketHub()
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        hub.pending["r1"] = fut
        hub.fulfill(
            {
                "request_id": "r1",
                "status_code": 200,
                "body": '{"resourceType":"Bundle"}',
                "relay_logs": [
                    {
                        "event": "request",
                        "outcome": "ok",
                        "target_host": "relay.example.invalid",
                    }
                ],
            }
        )
        result = loop.run_until_complete(fut)
    finally:
        loop.close()

    # relay_logs must be stripped before the response reaches the GP Connect path
    assert "relay_logs" not in result
    assert result["status_code"] == 200
    assert result["body"] == '{"resourceType":"Bundle"}'


def test_approved_metadata_is_logged(caplog):
    sample_entry = {
        "ts": "2026-10-14T15:00:00Z",
        "level": "INFO",
        "event": "request",
        "request_id": "inner-id",
        "target_host": "relay.example.invalid",
        "method": "POST",
        "http_status": 200,
        "duration_ms": 142.5,
        "outcome": "ok",
        "reason_code": "SUCCESS",
        "client_version": "1.2.0",
    }
    with caplog.at_level(logging.INFO, logger="relay"):
        _log_relay_diagnostics("auth-req-123", [sample_entry])

    diag_records = [r for r in caplog.records if r.getMessage().startswith("relay_diag ")]
    assert len(diag_records) == 1
    raw_json = diag_records[0].getMessage()[len("relay_diag ") :]
    parsed = json.loads(raw_json)

    for field in _RELAY_LOG_FIELDS:
        assert field in parsed

    assert parsed["request_id"] == "auth-req-123"
    assert parsed["target_host"] == "relay.example.invalid"
    assert parsed["http_status"] == 200
    assert parsed["duration_ms"] == 142.5
    assert parsed["outcome"] == "ok"


def test_log_diagnostics_drops_unknown_fields(caplog):
    with caplog.at_level(logging.INFO, logger="relay"):
        _log_relay_diagnostics(
            "r1",
            [
                {
                    "event": "request",
                    "outcome": "ok",
                    "target_host": "relay.example.invalid",
                    "body": "PHI-9999999999",  # unexpected -> must be dropped
                    "authorization": "Bearer SECRET-TOKEN",  # unexpected -> must be dropped
                    "headers": {"X-Custom": "val"},  # unexpected -> must be dropped
                    "nhs_number": "9999999999",  # unexpected -> must be dropped
                }
            ],
        )

    diag_records = [r for r in caplog.records if r.getMessage().startswith("relay_diag ")]
    assert len(diag_records) == 1
    raw_json = diag_records[0].getMessage()[len("relay_diag ") :]
    parsed = json.loads(raw_json)
    assert parsed["target_host"] == "relay.example.invalid"

    text = " ".join(r.getMessage() for r in caplog.records)
    assert "PHI-9999999999" not in text
    assert "SECRET-TOKEN" not in text
    assert "X-Custom" not in text
    assert "nhs_number" not in text


def test_log_diagnostics_handles_non_list_safely():
    # Must never raise, whatever a client sends.
    _log_relay_diagnostics("r1", None)
    _log_relay_diagnostics("r1", "not-a-list")
    _log_relay_diagnostics("r1", 12345)
    _log_relay_diagnostics("r1", [None, "x", 123])


def test_inner_request_id_cannot_spoof_outer_id(caplog):
    with caplog.at_level(logging.INFO, logger="relay"):
        _log_relay_diagnostics(
            "authoritative-outer-id",
            [
                {
                    "event": "request",
                    "request_id": "spoofed-inner-id",
                    "outcome": "ok",
                }
            ],
        )

    diag_records = [r for r in caplog.records if r.getMessage().startswith("relay_diag ")]
    assert len(diag_records) == 1
    parsed = json.loads(diag_records[0].getMessage()[len("relay_diag ") :])
    assert parsed["request_id"] == "authoritative-outer-id"
    assert "spoofed-inner-id" not in diag_records[0].getMessage()


def test_diagnostic_list_size_is_bounded(caplog):
    entries = [{"event": f"req_{i}", "outcome": "ok"} for i in range(25)]
    with caplog.at_level(logging.INFO, logger="relay"):
        _log_relay_diagnostics("r_bounded", entries)

    diag_records = [r for r in caplog.records if r.getMessage().startswith("relay_diag ")]
    assert len(diag_records) == _MAX_DIAGNOSTIC_ENTRIES
    assert len(diag_records) == 10


def test_excessive_string_length_is_bounded(caplog):
    long_string = "A" * 500
    with caplog.at_level(logging.INFO, logger="relay"):
        _log_relay_diagnostics(
            "r_long",
            [
                {
                    "event": "request",
                    "target_host": long_string,
                    "reason_code": long_string,
                }
            ],
        )

    diag_records = [r for r in caplog.records if r.getMessage().startswith("relay_diag ")]
    assert len(diag_records) == 1
    parsed = json.loads(diag_records[0].getMessage()[len("relay_diag ") :])
    assert len(parsed["target_host"]) == _MAX_STRING_LENGTH
    assert len(parsed["reason_code"]) == _MAX_STRING_LENGTH


def test_permitted_log_levels_constrained(caplog):
    with caplog.at_level(logging.DEBUG, logger="relay"):
        _log_relay_diagnostics(
            "r_level",
            [
                {"event": "e1", "level": "WARNING"},
                {"event": "e2", "level": "ERROR"},
                {"event": "e3", "level": "CRITICAL"},  # not permitted -> defaults to INFO
                {"event": "e4", "level": "DEBUG"},  # not permitted -> defaults to INFO
                {"event": "e5", "level": "GARBAGE"},  # not permitted -> defaults to INFO
            ],
        )

    records = [r for r in caplog.records if r.getMessage().startswith("relay_diag ")]
    assert len(records) == 5
    assert records[0].levelno == logging.WARNING
    assert records[1].levelno == logging.ERROR
    assert records[2].levelno == logging.INFO
    assert records[3].levelno == logging.INFO
    assert records[4].levelno == logging.INFO


def test_websocket_hub_unchanged_when_relay_logs_absent():
    hub = WebSocketHub()
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        hub.pending["r_normal"] = fut
        hub.fulfill(
            {
                "request_id": "r_normal",
                "status_code": 200,
                "body": '{"status":"ok"}',
            }
        )
        result = loop.run_until_complete(fut)
    finally:
        loop.close()

    assert result["request_id"] == "r_normal"
    assert result["status_code"] == 200
    assert result["body"] == '{"status":"ok"}'
    assert "relay_logs" not in result


@pytest.mark.asyncio
async def test_connection_state_observability(caplog):
    hub = WebSocketHub()
    mock_ws = AsyncMock()

    with caplog.at_level(logging.INFO, logger="relay"):
        # 1. register
        await hub.register(mock_ws)
        # 2. register another (preempting)
        mock_ws_2 = AsyncMock()
        await hub.register(mock_ws_2)
        # 3. unregister
        await hub.unregister(mock_ws_2)

    messages = [r.getMessage() for r in caplog.records]
    assert any("relay_connected" in m for m in messages)
    assert any("relay_preempted" in m for m in messages)
    assert any("relay_disconnected" in m for m in messages)


@pytest.mark.asyncio
async def test_send_not_connected_and_orphan_logging(caplog):
    hub = WebSocketHub()

    with caplog.at_level(logging.INFO, logger="relay"):
        # Not connected
        with pytest.raises(HTTPException) as exc:
            await hub.send({"action": "test"})
        assert exc.value.status_code == 404

        # Orphan fulfill
        hub.fulfill({"request_id": "non-existent-id", "status_code": 200})

    messages = [r.getMessage() for r in caplog.records]
    assert any("relay_not_connected" in m for m in messages)
    assert any("relay_orphan_response" in m for m in messages)
