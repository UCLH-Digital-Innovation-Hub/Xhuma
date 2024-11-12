"""
Test module for verifying tracing functionality through middleware.

This module tests the correlation ID propagation and logging through
the middleware stack, focusing on request tracing rather than the
full ITI transaction flow.
"""

import json
import uuid
import pytest
from fastapi.testclient import TestClient
import psycopg2
from psycopg2.extras import DictCursor

from ..main import app
from ..config import DB_DSN, REQUEST_TYPES

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

def test_correlation_id_propagation():
    """Test that correlation IDs are properly propagated through middleware."""
    correlation_id = str(uuid.uuid4())
    
    # Make request with correlation ID
    response = client.get(
        "/",
        headers={"X-Correlation-ID": correlation_id}
    )
    assert response.status_code == 200
    
    # Get logs and verify correlation ID propagation
    logs = get_logs_for_correlation_id(correlation_id)
    assert len(logs) > 0
    assert all(log["correlation_id"] == correlation_id for log in logs)
    
    # Verify response header contains correlation ID
    assert response.headers.get("X-Correlation-ID") == correlation_id

def test_metrics_endpoint():
    """Test that metrics endpoint returns Prometheus metrics."""
    correlation_id = str(uuid.uuid4())
    
    response = client.get(
        "/metrics",
        headers={"X-Correlation-ID": correlation_id}
    )
    assert response.status_code == 200
    assert "http_requests_total" in response.text
    
    # Verify correlation ID propagation
    logs = get_logs_for_correlation_id(correlation_id)
    assert len(logs) > 0
    assert all(log["correlation_id"] == correlation_id for log in logs)

def test_request_type_detection():
    """Test that request types are properly detected and logged."""
    correlation_id = str(uuid.uuid4())
    
    # Make ITI-47 request
    soap_envelope = """<?xml version="1.0" encoding="UTF-8"?>
    <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope">
        <SOAP-ENV:Header>
            <MessageID xmlns="http://www.w3.org/2005/08/addressing">urn:uuid:6d296e90-e5dc-43d0-b455-7c1f3eb35d83</MessageID>
        </SOAP-ENV:Header>
        <SOAP-ENV:Body>
            <PRPA_IN201305UV02 xmlns="urn:hl7-org:v3"/>
        </SOAP-ENV:Body>
    </SOAP-ENV:Envelope>"""
    
    response = client.post(
        "/SOAP/iti47",
        data=soap_envelope,
        headers={
            "X-Correlation-ID": correlation_id,
            "Content-Type": "application/soap+xml"
        }
    )
    
    # Get logs and verify request type
    logs = get_logs_for_correlation_id(correlation_id)
    assert len(logs) > 0
    
    # Verify ITI-47 request type was logged
    iti47_logs = [log for log in logs if log["request_type"] == "ITI-47"]
    assert len(iti47_logs) > 0
