import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
import os
from app.audit.models import SAMLAttributes
from app.ccda.models.datatypes import CD
import app.main
from app.audit.audit import AuditFailureException
from app.audit.models import AuditOutcome
from app.audit.store import insert_audit_event
from app.audit.build import build_audit_event


@pytest.fixture
def client():
    with patch("app.main.verify_api_key", return_value=True):

        async def dummy_dispatch(request, call_next):
            return await call_next(request)

        with patch("app.middleware.mtls.MTLSMiddleware.dispatch", side_effect=dummy_dispatch):
            yield TestClient(app.main.app)


def create_iti39_request(doc_id="test-doc-123", reply_to="https://test.nhs.uk"):
    return f"""--MIMEBoundary
Content-Type: application/xop+xml; charset=UTF-8; type="application/soap+xml"

<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:a="http://www.w3.org/2005/08/addressing"><s:Header><MessageID>urn:uuid:test-message-id</MessageID><ReplyTo><Address>{reply_to}</Address></ReplyTo></s:Header><s:Body><RetrieveDocumentSetRequest><DocumentRequest><DocumentUniqueId>{doc_id}</DocumentUniqueId></DocumentRequest></RetrieveDocumentSetRequest></s:Body></s:Envelope>
--MIMEBoundary--"""


@pytest.mark.asyncio
@patch("app.soap.soap.attempt_audit", new_callable=AsyncMock)
@patch("app.soap.soap.client.get")
@patch("app.soap.soap.extract_trusted_saml_assertion")
async def test_iti39_direct_and_callback_blocked_on_audit_failure(
    mock_extract_saml, mock_redis_get, mock_attempt_audit, client
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

    def redis_get_side_effect(key):
        if key == "test-doc-123":
            return b"<CCDA></CCDA>"
        if key == "doc_patient:test-doc-123":
            return b"1234567890"
        return None

    mock_redis_get.side_effect = redis_get_side_effect
    mock_attempt_audit.side_effect = AuditFailureException("Audit failed")

    # Direct response (anonymous replyTo)
    response_sync = client.post(
        "/SOAP/iti39",
        content=create_iti39_request(reply_to="http://www.w3.org/2005/08/addressing/anonymous"),
        headers={"Content-Type": "application/soap+xml", "X-ARR-ClientCert": "ZHVtbXk="},
    )
    assert response_sync.status_code == 502

    # Callback response
    response_async = client.post(
        "/SOAP/iti39",
        content=create_iti39_request(reply_to="https://test.nhs.uk"),
        headers={"Content-Type": "application/soap+xml", "X-ARR-ClientCert": "ZHVtbXk="},
    )
    assert response_async.status_code == 502


@pytest.mark.asyncio
async def test_unknown_subject_builder_and_store():
    # Test through real builder and store
    from app.audit.models import SAMLAttributes
    from app.ccda.models.datatypes import CD

    saml = SAMLAttributes(
        subject_id="user1",
        organization="org1",
        organization_id="orgid1",
        role=CD(code="code"),
    )

    request = MagicMock()
    request.headers = {}
    request.client.host = "127.0.0.1"

    session = AsyncMock()
    mock_execute_result = MagicMock()
    mock_execute_result.mappings.return_value.one.return_value = {"seq": 1}
    session.execute.return_value = mock_execute_result

    # Missing NHS number + FAIL -> allowed if has message_id
    evt = await build_audit_event(
        request=request,
        session=session,
        saml=saml,
        nhs_number=None,
        action="test_action",
        outcome=AuditOutcome.fail,
        message_id="msg-1",
    )
    assert evt.subject_ref is None
    # This should not raise ValueError
    await insert_audit_event(session, evt)

    # Missing NHS number + OK -> raises ValueError
    evt_ok = await build_audit_event(
        request=request,
        session=session,
        saml=saml,
        nhs_number=None,
        action="test_action",
        outcome=AuditOutcome.ok,
        message_id="msg-1",
    )
    with pytest.raises(ValueError, match="AuditEvent.subject_ref is None for a successful event"):
        await insert_audit_event(session, evt_ok)


@pytest.mark.asyncio
@patch("app.soap.soap.attempt_audit", new_callable=AsyncMock)
@patch("app.soap.soap.extract_trusted_saml_assertion")
async def test_iti39_destination_denial(mock_extract_saml, mock_attempt_audit, client):
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
    with patch(
        "app.soap.soap.client.get",
        side_effect=lambda k: b"data" if k == "test-doc-123" or k == "doc_patient:test-doc-123" else None,
    ):
        response = client.post(
            "/SOAP/iti39",
            content=create_iti39_request(reply_to="https://malicious.com"),
            headers={"Content-Type": "application/soap+xml", "X-ARR-ClientCert": "ZHVtbXk="},
        )
        assert response.status_code == 403

        mock_attempt_audit.assert_called_once()
        kwargs = mock_attempt_audit.call_args.kwargs
        assert kwargs["outcome"] == AuditOutcome.deny
        assert kwargs["detail"]["error"] == "ReplyTo domain not allowed"


@pytest.mark.asyncio
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_restricted_patient_denial(mock_lookup, mock_attempt_audit):
    mock_lookup.return_value = {"meta": {"security": [{"code": "R"}]}}
    from app.gpconnect import _fetch_gpconnect_record

    response = await _fetch_gpconnect_record(9690937278, MagicMock(), request=MagicMock())
    assert response.status_code == 403
    assert mock_attempt_audit.call_count == 1
    assert mock_attempt_audit.call_args.kwargs["outcome"] == AuditOutcome.deny


@pytest.mark.asyncio
@patch("app.gpconnect.create_nhs_ssl_context")
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.redis_client.pipeline")
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
async def test_redis_publication_audit_failure_interaction(mock_lookup, mock_pipeline, mock_attempt_audit, mock_ssl):
    # Setup happy path until attempt_audit fails on save
    mock_lookup.return_value = {
        "meta": {"security": [{"code": "U"}]},
        "generalPractitioner": [{"identifier": {"value": "ods1"}}],
    }

    with patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as mock_sds:
        mock_sds.side_effect = [
            {
                "entry": [
                    {
                        "resource": {
                            "identifier": [
                                {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "123"},
                                {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "123"},
                            ]
                        }
                    }
                ]
            },
            {"entry": [{"resource": {"address": "test-url"}}]},
        ]
        with patch("app.gpconnect.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.return_value = MagicMock(
                status_code=200, text='{"resourceType": "Bundle", "type": "searchset", "entry": []}'
            )
            with patch("app.gpconnect.convert_bundle", new_callable=AsyncMock) as mock_convert:
                mock_convert.return_value = {"ClinicalDocument": "test"}
                with patch("app.gpconnect.base64_xml") as mock_base64:
                    mock_base64.return_value = "base64"

                    # Make audit fail on store_ccda
                    def audit_side_effect(**kwargs):
                        if kwargs.get("action") == "store_ccda":
                            raise AuditFailureException("Audit failed")

                    mock_attempt_audit.side_effect = audit_side_effect

                    from app.gpconnect import _fetch_gpconnect_record
                    from app.audit.models import SAMLAttributes
                    from app.ccda.models.datatypes import CD

                    saml = SAMLAttributes(
                        subject_id="user1", organization="org1", organization_id="orgid1", role=CD(code="code")
                    )

                    try:
                        response = await _fetch_gpconnect_record(9690937278, saml, request=MagicMock())
                        assert False, (
                            f"Expected AuditFailureException, but got response: {response.body.decode() if hasattr(response, 'body') else response}"
                        )
                    except AuditFailureException:
                        pass

                    # Verify pipeline was NOT executed (document was not cached)
                    mock_pipeline.return_value.execute.assert_not_called()


@pytest.mark.asyncio
@patch("app.soap.responses.iti_38.attempt_audit", new_callable=AsyncMock)
@patch("app.soap.responses.iti_38.gpconnect", new_callable=AsyncMock)
@patch("app.soap.responses.iti_38.redis_client.get")
async def test_iti38_cache_miss_audit_failure_propagation(mock_redis_get, mock_gpconnect, mock_attempt_audit):
    # Setup cache miss
    mock_redis_get.return_value = None

    # gpconnect raises AuditFailureException
    mock_gpconnect.side_effect = AuditFailureException("Failed to persist audit event")

    # Test iti_38_response
    from app.soap.responses.iti_38 import iti_38_response
    from app.audit.models import SAMLAttributes
    from app.ccda.models.datatypes import CD

    saml = SAMLAttributes(subject_id="user1", organization="org1", organization_id="orgid1", role=CD(code="code"))
    request = MagicMock()

    with pytest.raises(AuditFailureException):
        await iti_38_response(request, 1234567890, "ceid", "queryid", saml)

    # Verify no second audit attempt was made in iti_38_response
    mock_attempt_audit.assert_not_called()


@pytest.mark.asyncio
@patch("app.soap.responses.iti_38.gpconnect", new_callable=AsyncMock)
@patch("app.soap.responses.iti_38.redis_client.get")
@patch("app.soap.soap.extract_trusted_saml_assertion")
async def test_iti38_endpoint_audit_failure(mock_extract, mock_redis_get, mock_gpconnect, client):
    mock_extract.return_value = {
        "AttributeStatement": {
            "Attribute": [
                {"@Name": "urn:oasis:names:tc:xspa:1.0:subject:subject-id", "AttributeValue": "user1"},
                {"@Name": "urn:oasis:names:tc:xspa:1.0:subject:organization", "AttributeValue": "org1"},
                {"@Name": "urn:oasis:names:tc:xspa:1.0:subject:organization-id", "AttributeValue": "orgid1"},
                {"@Name": "urn:oasis:names:tc:xacml:2.0:subject:role", "AttributeValue": {"Role": {"code": "code"}}},
            ]
        }
    }
    mock_redis_get.return_value = None
    mock_gpconnect.side_effect = AuditFailureException("Failed to persist audit event")

    # Use a valid NHS number so validation passes
    request_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header><MessageID>123</MessageID></s:Header><s:Body><AdhocQueryRequest><AdhocQuery><Slot name="$XDSDocumentEntryPatientId"><ValueList><Value>9690937278</Value></ValueList></Slot></AdhocQuery></AdhocQueryRequest></s:Body></s:Envelope>"""

    response = client.post("/SOAP/iti38", content=request_xml, headers={"Content-Type": "application/soap+xml"})
    assert response.status_code == 502
    assert "Failed to persist audit event" not in response.text
    assert "SQL" not in response.text


@pytest.mark.asyncio
@patch("app.audit.build.build_audit_event", new_callable=AsyncMock)
@patch("app.audit.store.insert_audit_event", new_callable=AsyncMock)
async def test_attempt_audit_preserves_none(mock_insert, mock_build):
    from app.audit.audit import attempt_audit
    from app.audit.models import SAMLAttributes
    from app.ccda.models.datatypes import CD

    request = MagicMock()
    request.app.state.SessionLocal = MagicMock()
    session_mock = AsyncMock()
    request.app.state.SessionLocal.return_value.__aenter__.return_value = session_mock

    saml = SAMLAttributes(subject_id="user1", organization="org1", organization_id="orgid1", role=CD(code="code"))

    mock_build.return_value = MagicMock()

    await attempt_audit(
        request=request,
        nhs_number=None,
        saml=saml,
        action="test",
        outcome=AuditOutcome.fail,
    )

    # Assert build_audit_event was called with nhs_number=None (not "None")
    mock_build.assert_called_once()
    assert mock_build.call_args.kwargs["nhs_number"] is None


@pytest.mark.asyncio
@patch("app.audit.audit.insert_audit_event", new_callable=AsyncMock)
async def test_audit_boundary_failure_closure(mock_insert):
    request = MagicMock()
    request.app.state.SessionLocal = MagicMock()
    session_mock = AsyncMock()
    request.app.state.SessionLocal.return_value.__aenter__.return_value = session_mock

    saml = SAMLAttributes(subject_id="user1", organization="org1", organization_id="orgid1", role=CD(code="code"))

    mock_insert.side_effect = Exception("DB failure")

    from app.audit.audit import attempt_audit

    with pytest.raises(AuditFailureException, match="Failed to insert audit event: DB failure"):
        await attempt_audit(
            request=request,
            nhs_number="9690937278",
            saml=saml,
            action="test",
            outcome=AuditOutcome.fail,
        )

    mock_insert.assert_called_once()
    session_mock.commit.assert_not_called()


@pytest.mark.asyncio
async def test_audit_missing_pseudonymisation_key():
    from app.audit.audit import attempt_audit

    request = MagicMock()
    request.app.state.SessionLocal = MagicMock()
    session_mock = AsyncMock()
    request.app.state.SessionLocal.return_value.__aenter__.return_value = session_mock

    saml = SAMLAttributes(subject_id="user1", organization="org1", organization_id="orgid1", role=CD(code="code"))

    # Run without API_KEY
    with patch.dict(os.environ, clear=True):
        with pytest.raises(AuditFailureException, match="Pseudonymisation key missing"):
            await attempt_audit(
                request=request,
                nhs_number="9690937278",
                saml=saml,
                action="test",
                outcome=AuditOutcome.ok,
            )
