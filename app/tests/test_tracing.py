"""
Test module for verifying tracing functionality across ITI transactions.

This module tests the tracing and correlation ID propagation through
the complete ITI transaction flow (ITI-47 -> ITI-38 -> ITI-39).
"""

import json
import uuid
import pytest
from fastapi.testclient import TestClient
import psycopg2
from psycopg2.extras import DictCursor

from ..main import app
from ..config import DB_DSN

client = TestClient(app)

def get_logs_for_correlation_id(correlation_id: str):
    """
    Retrieve all logs for a given correlation ID from the database.
    
    :param correlation_id: The correlation ID to query for
    :type correlation_id: str
    :return: List of log entries
    :rtype: list
    """
    with psycopg2.connect(DB_DSN) as conn:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("""
                SELECT timestamp, correlation_id, nhs_number, request_type, 
                       message::jsonb->>'message' as message, level
                FROM application_logs 
                WHERE correlation_id = %s 
                ORDER BY timestamp ASC
            """, (correlation_id,))
            return [dict(row) for row in cur.fetchall()]

def test_iti_transaction_tracing():
    """Test tracing through a complete ITI transaction flow."""
    
    # Generate a test correlation ID
    correlation_id = str(uuid.uuid4())
    nhs_number = "9690937278"
    
    # Make ITI-47 request
    response = client.post(
        "/iti/47",
        json={"nhs_number": nhs_number},
        headers={"X-Correlation-ID": correlation_id}
    )
    assert response.status_code == 200
    
    # Make ITI-38 request
    response = client.post(
        "/iti/38",
        json={"nhs_number": nhs_number},
        headers={"X-Correlation-ID": correlation_id}
    )
    assert response.status_code == 200
    iti38_response = response.json()
    document_id = iti38_response["document_id"]
    
    # Make ITI-39 request
    response = client.post(
        "/iti/39",
        json={"document_id": document_id},
        headers={"X-Correlation-ID": correlation_id}
    )
    assert response.status_code == 200
    
    # Get all logs for this correlation ID
    logs = get_logs_for_correlation_id(correlation_id)
    
    # Verify the complete trace
    assert len(logs) >= 3  # At least one log per request
    
    # Verify ITI-47 trace
    iti47_logs = [log for log in logs if log["request_type"] == "ITI-47"]
    assert len(iti47_logs) > 0
    assert iti47_logs[0]["nhs_number"] == nhs_number
    
    # Verify ITI-38 trace
    iti38_logs = [log for log in logs if log["request_type"] == "ITI-38"]
    assert len(iti38_logs) > 0
    assert iti38_logs[0]["nhs_number"] == nhs_number
    
    # Verify ITI-39 trace
    iti39_logs = [log for log in logs if log["request_type"] == "ITI-39"]
    assert len(iti39_logs) > 0
    assert iti39_logs[0]["nhs_number"] == nhs_number
    
    # Verify chronological order
    iti47_time = iti47_logs[0]["timestamp"]
    iti38_time = iti38_logs[0]["timestamp"]
    iti39_time = iti39_logs[0]["timestamp"]
    assert iti47_time <= iti38_time <= iti39_time

def test_correlation_id_propagation():
    """Test that correlation IDs are properly propagated and reused."""
    
    nhs_number = "9690937278"
    
    # Make first request without correlation ID
    response = client.post(
        "/iti/47",
        json={"nhs_number": nhs_number}
    )
    assert response.status_code == 200
    correlation_id = response.headers.get("X-Correlation-ID")
    assert correlation_id is not None
    
    # Make second request with same NHS number
    response = client.post(
        "/iti/47",
        json={"nhs_number": nhs_number}
    )
    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == correlation_id
    
    # Verify logs show correlation ID reuse
    logs = get_logs_for_correlation_id(correlation_id)
    assert len(logs) >= 2
    assert all(log["correlation_id"] == correlation_id for log in logs)
    assert all(log["nhs_number"] == nhs_number for log in logs)

def test_trace_context_propagation():
    """Test OpenTelemetry trace context propagation."""
    
    correlation_id = str(uuid.uuid4())
    nhs_number = "9690937278"
    
    response = client.post(
        "/iti/47",
        json={"nhs_number": nhs_number},
        headers={"X-Correlation-ID": correlation_id}
    )
    assert response.status_code == 200
    
    # Get logs and verify trace context
    logs = get_logs_for_correlation_id(correlation_id)
    assert len(logs) > 0
    
    # Verify trace context in log message
    first_log = logs[0]
    message = json.loads(first_log["message"])
    assert "otelTraceID" in message
    assert "otelSpanID" in message
