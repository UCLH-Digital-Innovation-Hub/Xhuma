import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request

from app.audit.models import AuditOutcome
from app.soap.responses.iti_38 import iti_38_response
from app.tests.fixtures.saml_attributes import saml


@pytest.fixture
def mock_request():
    return Request(scope={"type": "http", "method": "POST"})

@pytest.mark.asyncio
@patch("app.soap.responses.iti_38.attempt_audit", new_callable=AsyncMock)
@patch("app.soap.responses.iti_38.gpconnect", new_callable=AsyncMock)
@patch("app.soap.responses.iti_38.redis_client.get", new_callable=AsyncMock)
async def test_iti38_gp_connect_failure_classification(mock_redis_get, mock_gpconnect, mock_attempt_audit, mock_request):
    mock_redis_get.return_value = None
    
    class MockResponse:
        body = json.dumps({"success": False, "error": "GP Connect Down"})
    
    mock_gpconnect.return_value = MockResponse()
    
    await iti_38_response(mock_request, 9690937278, "ceid", "queryid", saml)
    
    audit_calls = mock_attempt_audit.call_args_list
    iti38_call = [c for c in audit_calls if c.kwargs.get("action") == "iti38_document_query"]
    assert iti38_call
    
    audit_args = iti38_call[-1].kwargs
    assert audit_args["outcome"] == AuditOutcome.fail
    assert audit_args["error_code"] == "GP_CONNECT_FAILURE"
    assert "GP Connect Down" not in str(audit_args)

@pytest.mark.asyncio
@patch("app.soap.responses.iti_38.attempt_audit", new_callable=AsyncMock)
@patch("app.soap.responses.iti_38.gpconnect", new_callable=AsyncMock)
@patch("app.soap.responses.iti_38.redis_client.get", new_callable=AsyncMock)
async def test_iti38_internal_failure_classification(mock_redis_get, mock_gpconnect, mock_attempt_audit, mock_request):
    mock_redis_get.return_value = None
    
    # gpconnect raises exception
    mock_gpconnect.side_effect = Exception("Some internal error")
    
    await iti_38_response(mock_request, 9690937278, "ceid", "queryid", saml)
    
    audit_calls = mock_attempt_audit.call_args_list
    iti38_call = [c for c in audit_calls if c.kwargs.get("action") == "iti38_document_query"]
    assert iti38_call
    
    audit_args = iti38_call[-1].kwargs
    assert audit_args["outcome"] == AuditOutcome.fail
    assert audit_args["error_code"] == "DOCUMENT_QUERY_FAILURE"


@pytest.mark.asyncio
@patch("app.soap.responses.iti_38.attempt_audit", new_callable=AsyncMock)
@patch("app.soap.responses.iti_38.redis_client.get", new_callable=AsyncMock)
async def test_iti38_message_id_propagation(mock_redis_get, mock_attempt_audit, mock_request):
    mock_redis_get.return_value = b"cached_doc_id"
    synthetic_message_id = "urn:uuid:00000000-0000-4000-8000-000000000001"
    
    await iti_38_response(mock_request, 9690937278, "ceid", "queryid", saml, message_id=synthetic_message_id)
    
    audit_calls = mock_attempt_audit.call_args_list
    iti38_call = [c for c in audit_calls if c.kwargs.get("action") == "iti38_document_query"]
    assert iti38_call
    
    audit_args = iti38_call[-1].kwargs
    assert audit_args["message_id"] == synthetic_message_id
