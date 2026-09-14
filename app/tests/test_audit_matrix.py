import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

import app.main
from app.audit.audit import AuditFailureException
from app.audit.models import AuditOutcome


@pytest.fixture
def client():
    # Make sure we don't have any MTLS or API_KEY requirements for these internal tests
    with patch("app.main.verify_api_key", return_value=True):

        async def dummy_dispatch(request, call_next):
            return await call_next(request)

        with patch("app.middleware.mtls.MTLSMiddleware.dispatch", side_effect=dummy_dispatch):
            yield TestClient(app.main.app)


def create_soap_request(nhsno="9690937278", query_id="12345"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope">
    <env:Header>
        <MessageID>urn:uuid:test-message-id</MessageID>
        <ReplyTo><Address>https://test.nhs.uk</Address></ReplyTo>
    </env:Header>
    <env:Body>
        <AdhocQueryRequest>
            <AdhocQuery id="{query_id}">
                <Slot name="$XDSDocumentEntryPatientId">
                    <ValueList>
                        <Value>{nhsno}^^^&amp;2.16.840.1.113883.2.1.4.1&amp;ISO</Value>
                    </ValueList>
                </Slot>
            </AdhocQuery>
        </AdhocQueryRequest>
    </env:Body>
</env:Envelope>"""


def create_iti39_request(doc_id="test-doc-123"):
    return f"""--MIMEBoundary
Content-Type: application/xop+xml; charset=UTF-8; type="application/soap+xml"

<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:a="http://www.w3.org/2005/08/addressing"><s:Header><MessageID>urn:uuid:test-message-id</MessageID><ReplyTo><Address>https://test.nhs.uk</Address></ReplyTo></s:Header><s:Body><RetrieveDocumentSetRequest><DocumentRequest><DocumentUniqueId>{doc_id}</DocumentUniqueId></DocumentRequest></RetrieveDocumentSetRequest></s:Body></s:Envelope>
--MIMEBoundary--"""


@pytest.mark.asyncio
@patch("app.soap.soap.attempt_audit", new_callable=AsyncMock)
@patch("app.soap.soap.client.get")
@patch("app.soap.soap.extract_trusted_saml_assertion")
async def test_iti39_missing_association_uses_none(mock_extract_saml, mock_redis_get, mock_soap_audit, client):
    mock_extract_saml.return_value = {
        "AttributeStatement": {
            "Attribute": [
                {"@Name": "urn:oasis:names:tc:xspa:1.0:subject:subject-id", "AttributeValue": "user1"},
                {"@Name": "urn:oasis:names:tc:xspa:1.0:subject:organization", "AttributeValue": "org1"},
                {"@Name": "urn:oasis:names:tc:xspa:1.0:subject:organization-id", "AttributeValue": "orgid1"},
                {"@Name": "urn:oasis:names:tc:xacml:2.0:subject:role", "AttributeValue": {"Role": {"code": "code"}}},
            ]
        }
    }

    # Simulate missing doc_nhsno
    def redis_get_side_effect(key):
        if key == "test-doc-123":
            return b"some-document-content"
        return None

    mock_redis_get.side_effect = redis_get_side_effect

    response = client.post(
        "/SOAP/iti39",
        content=create_iti39_request(),
        headers={"Content-Type": "application/soap+xml", "X-ARR-ClientCert": "ZHVtbXk="},
    )
    assert response.status_code == 404

    # Assert attempt_audit was called with nhs_number=None
    mock_soap_audit.assert_called_once()
    kwargs = mock_soap_audit.call_args.kwargs
    assert kwargs["nhs_number"] is None
    assert kwargs["action"] == "iti39_document_retrieve"
    assert kwargs["outcome"] == AuditOutcome.fail


@pytest.mark.asyncio
@patch("app.soap.responses.iti_38.redis_client.get")
@patch("app.soap.responses.iti_38.gpconnect", new_callable=AsyncMock)
@patch("app.soap.soap.attempt_audit", new_callable=AsyncMock)
@patch("app.soap.soap.extract_trusted_saml_assertion")
async def test_iti38_audit_failure_blocks_cache_hit(
    mock_extract_saml, mock_attempt_audit, mock_gpconnect, mock_redis_get, client
):
    mock_extract_saml.return_value = {
        "AttributeStatement": {
            "Attribute": [
                {"@Name": "urn:oasis:names:tc:xspa:1.0:subject:subject-id", "AttributeValue": "user1"},
                {"@Name": "urn:oasis:names:tc:xspa:1.0:subject:organization", "AttributeValue": "org1"},
                {"@Name": "urn:oasis:names:tc:xspa:1.0:subject:organization-id", "AttributeValue": "orgid1"},
                {"@Name": "urn:oasis:names:tc:xacml:2.0:subject:role", "AttributeValue": {"Role": {"code": "code"}}},
            ]
        }
    }

    # Simulate cache hit
    mock_redis_get.return_value = b"test-doc-123"

    # Simulate audit failure
    mock_attempt_audit.side_effect = AuditFailureException("Audit failed")

    response = client.post(
        "/SOAP/iti38",
        content=create_soap_request(),
        headers={"Content-Type": "application/soap+xml", "X-ARR-ClientCert": "ZHVtbXk="},
    )

    # Due to our explicit handler, this should return a 502 SOAP Fault
    assert response.status_code == 502
    assert "Internal Server Error" in response.text

    # gpconnect should not be called due to cache hit
    mock_gpconnect.assert_not_called()


@pytest.mark.asyncio
@patch("app.pds.pds.httpx.AsyncClient")
@patch("app.pds.pds.redis_client.exists")
@patch("app.pds.pds.attempt_audit", new_callable=AsyncMock)
async def test_pds_lookup_upstream_failure_audited(mock_attempt_audit, mock_redis_exists, mock_async_client):
    from app.pds.pds import lookup_patient
    from fastapi import HTTPException

    mock_redis_exists.return_value = True

    def redis_get_side_effect(key):
        if key == "access_token":
            return b"token"
        return None

    with patch("app.pds.pds.redis_client.get", side_effect=redis_get_side_effect):
        mock_client_instance = AsyncMock()
        mock_client_instance.get.side_effect = Exception("Connection Refused")
        mock_async_client.return_value.__aenter__.return_value = mock_client_instance

        with pytest.raises(HTTPException) as excinfo:
            await lookup_patient("9690937278", request=MagicMock(), saml=MagicMock())

        assert excinfo.value.status_code == 502

        # Verify it was audited as a failure
        mock_attempt_audit.assert_called_once()
        kwargs = mock_attempt_audit.call_args.kwargs
        assert kwargs["action"] == "pds_lookup"
        assert kwargs["outcome"] == AuditOutcome.fail
        assert "Connection Refused" in kwargs["detail"]["exception"]


@pytest.mark.asyncio
@patch("app.audit.store.insert_audit_event", new_callable=AsyncMock)
async def test_audit_failure_exception_hides_sql(mock_insert, caplog):
    import logging
    from app.audit.audit import attempt_audit

    # Simulate DB error with sensitive SQL param
    mock_insert.side_effect = Exception("SENSITIVE_SQL_PARAM_123")

    mock_request = MagicMock()
    mock_session = AsyncMock()
    mock_request.app.state.SessionLocal.return_value.__aenter__.return_value = mock_session

    with caplog.at_level(logging.ERROR):
        with pytest.raises(AuditFailureException) as exc_info:
            await attempt_audit(
                request=mock_request, nhs_number="123", saml=MagicMock(), action="test", outcome=AuditOutcome.ok
            )

        assert "Failed to persist audit event" in str(exc_info.value)
        # Verify the underlying exception (with sensitive data) is not attached via __cause__
        assert exc_info.value.__cause__ is None

        # Verify logs don't contain the sensitive data
        for record in caplog.records:
            assert "SENSITIVE_SQL_PARAM_123" not in record.message
            # Because we removed 'from e', traceback formatting won't dump the underlying exception automatically
