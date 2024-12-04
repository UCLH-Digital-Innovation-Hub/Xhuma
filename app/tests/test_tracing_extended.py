"""
Extended test module for comprehensive tracing functionality.

This module extends the basic tracing tests to cover all request types,
error scenarios, and correlation ID reuse cases.
"""

import json
import uuid
import pytest
from fastapi.testclient import TestClient
import psycopg2
from psycopg2.extras import DictCursor

from ..main import app
from ..config import DB_DSN, REQUEST_TYPES
from ..handlers import CorrelationManager

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
                       message::jsonb->>'message' as message, level,
                       request_details, response_details
                FROM application_logs 
                WHERE correlation_id = %s 
                ORDER BY timestamp ASC
            """, (correlation_id,))
            return [dict(row) for row in cur.fetchall()]

def test_iti38_request_tracing():
    """Test tracing for ITI-38 requests including GP Connect interaction."""
    correlation_id = str(uuid.uuid4())
    nhs_number = "9000000009"
    
    # Load ITI-38 request template
    with open("app/tests/test_data/iti38_request.xml") as f:
        soap_envelope = f.read()
    
    response = client.post(
        "/SOAP/iti38",
        data=soap_envelope,
        headers={
            "X-Correlation-ID": correlation_id,
            "Content-Type": "application/soap+xml"
        }
    )
    
    # Get logs and verify complete trace
    logs = get_logs_for_correlation_id(correlation_id)
    assert len(logs) > 0
    
    # Verify request flow logging
    request_types = [log["request_type"] for log in logs]
    assert "ITI-38" in request_types
    assert "GP_CONNECT" in request_types
    
    # Verify GP Connect interaction is logged
    gp_connect_logs = [log for log in logs if log["request_type"] == "GP_CONNECT"]
    assert len(gp_connect_logs) > 0
    assert any("FHIR request" in log["message"] for log in gp_connect_logs)
    
    # Verify CCDA conversion is logged
    assert any("CCDA conversion" in log["message"] for log in logs)

def test_pds_request_tracing():
    """Test tracing for PDS lookup requests."""
    correlation_id = str(uuid.uuid4())
    nhs_number = "9000000009"
    
    # Load ITI-47 request template
    with open("app/tests/test_data/iti47_request.xml") as f:
        soap_envelope = f.read()
    
    response = client.post(
        "/SOAP/iti47",
        data=soap_envelope,
        headers={
            "X-Correlation-ID": correlation_id,
            "Content-Type": "application/soap+xml"
        }
    )
    
    # Get logs and verify PDS interaction
    logs = get_logs_for_correlation_id(correlation_id)
    assert len(logs) > 0
    
    # Verify PDS lookup is logged
    pds_logs = [log for log in logs if "PDS" in log["message"]]
    assert len(pds_logs) > 0
    
    # Verify complete request flow
    assert any("PDS FHIR request" in log["message"] for log in logs)
    assert any("PDS response received" in log["message"] for log in logs)

def test_correlation_id_reuse():
    """Test correlation ID reuse for repeated requests."""
    nhs_number = "9000000009"
    request_type = "ITI-47"
    
    # Create correlation manager
    correlation_mgr = CorrelationManager(DB_DSN)
    
    # First request should create new correlation ID
    correlation_id1, is_new1 = correlation_mgr.get_or_create_correlation_id(
        nhs_number, request_type
    )
    assert is_new1 is True
    
    # Second request should reuse correlation ID
    correlation_id2, is_new2 = correlation_mgr.get_or_create_correlation_id(
        nhs_number, request_type
    )
    assert is_new2 is False
    assert correlation_id1 == correlation_id2
    
    # Verify logs show correlation ID reuse
    logs = get_logs_for_correlation_id(str(correlation_id1))
    assert len(logs) > 0
    assert any("Reusing correlation ID" in log["message"] for log in logs)

def test_error_scenario_tracing():
    """Test tracing for various error scenarios."""
    correlation_id = str(uuid.uuid4())
    
    # Test invalid NHS number
    response = client.post(
        "/SOAP/iti47",
        data="<Invalid XML>",
        headers={
            "X-Correlation-ID": correlation_id,
            "Content-Type": "application/soap+xml"
        }
    )
    
    # Verify error is logged
    logs = get_logs_for_correlation_id(correlation_id)
    assert len(logs) > 0
    error_logs = [log for log in logs if log["level"] == "ERROR"]
    assert len(error_logs) > 0
    
    # Verify error details are captured
    assert any("request_details" in log and log["request_details"] is not None 
              for log in logs)
    assert any("response_details" in log and log["response_details"] is not None 
              for log in logs)

def test_cache_interaction_tracing():
    """Test tracing of cache interactions."""
    correlation_id = str(uuid.uuid4())
    nhs_number = "9000000009"
    
    # Make initial request to populate cache
    with open("app/tests/test_data/iti38_request.xml") as f:
        soap_envelope = f.read()
    
    response1 = client.post(
        "/SOAP/iti38",
        data=soap_envelope,
        headers={
            "X-Correlation-ID": correlation_id,
            "Content-Type": "application/soap+xml"
        }
    )
    
    # Make second request to test cache hit
    correlation_id2 = str(uuid.uuid4())
    response2 = client.post(
        "/SOAP/iti38",
        data=soap_envelope,
        headers={
            "X-Correlation-ID": correlation_id2,
            "Content-Type": "application/soap+xml"
        }
    )
    
    # Verify cache operations are logged
    logs1 = get_logs_for_correlation_id(correlation_id)
    assert any("Cache miss" in log["message"] for log in logs1)
    assert any("Storing in cache" in log["message"] for log in logs1)
    
    logs2 = get_logs_for_correlation_id(correlation_id2)
    assert any("Cache hit" in log["message"] for log in logs2)
