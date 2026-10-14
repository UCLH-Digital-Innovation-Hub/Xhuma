import asyncio
import logging

from app.relay.hub import WebSocketHub, _log_relay_diagnostics

ALLOWED_KEYS = {
    "ts", "level", "event", "request_id", "target_host", "method",
    "http_status", "duration_ms", "outcome", "reason_code", "client_version",
}


def test_fulfill_strips_relay_logs_before_gpconnect():
    hub = WebSocketHub()
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        hub.pending["r1"] = fut
        hub.fulfill({
            "request_id": "r1",
            "status_code": 200,
            "body": '{"resourceType":"Bundle"}',
            "relay_logs": [{
                "event": "request", "outcome": "ok",
                "target_host": "proxy.national.ncrs.nhs.uk",
            }],
        })
        result = loop.run_until_complete(fut)
    finally:
        loop.close()

    # relay_logs must be stripped before the response reaches the GP Connect path
    assert "relay_logs" not in result
    assert result["status_code"] == 200
    assert result["body"] == '{"resourceType":"Bundle"}'


def test_log_diagnostics_drops_unknown_fields(caplog):
    with caplog.at_level(logging.INFO, logger="relay"):
        _log_relay_diagnostics("r1", [{
            "event": "request", "outcome": "ok",
            "target_host": "proxy.national.ncrs.nhs.uk",
            "body": "PHI-9999999999",                # unexpected -> must be dropped
            "authorization": "Bearer SECRET-TOKEN",   # unexpected -> must be dropped
        }])
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "proxy.national.ncrs.nhs.uk" in text      # allow-listed field is logged
    assert "PHI-9999999999" not in text              # dropped
    assert "SECRET-TOKEN" not in text                # dropped


def test_log_diagnostics_handles_non_list_safely():
    # Must never raise, whatever a client sends.
    _log_relay_diagnostics("r1", None)
    _log_relay_diagnostics("r1", "not-a-list")
    _log_relay_diagnostics("r1", [None, "x", 123])
