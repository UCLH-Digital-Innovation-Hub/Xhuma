"""Request-level tests built from synthetic IHE SOAP messages.

The XML below mirrors the structure of the supplied examples and the IHE
schemas, but every identifier and demographic value is fabricated.  Keeping
the messages inline also makes it clear that the proprietary examples are not
test fixtures and are never read by this suite.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.soap import soap

NHS_NUMBER = "9999999999"
NHS_NUMBER_ROOT = "2.16.840.1.113883.2.1.4.1"
CARE_EVERYWHERE_ROOT = "1.2.840.114350.1.13.525.3.7.3.688884.100"
CARE_EVERYWHERE_ID = "SYNTHETIC-CEID-001"
DOCUMENT_ID = "1.2.826.0.1.3680043.10.999.1"
ANONYMOUS_REPLY_TO = "http://www.w3.org/2005/08/addressing/anonymous"


def _request(body: str, content_type: str = "application/soap+xml") -> Request:
    """Create a Starlette request without starting the complete FastAPI app."""

    body_bytes = body.encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/SOAP/test",
        "raw_path": b"/SOAP/test",
        "query_string": b"",
        "headers": [(b"content-type", content_type.encode("ascii"))],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }
    return Request(scope, receive)


def _soap_envelope(body: str, *, reply_to: str = ANONYMOUS_REPLY_TO) -> str:
    """Wrap a transaction body in a minimal trusted synthetic SOAP envelope."""

    return f"""\
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:wsa="http://www.w3.org/2005/08/addressing"
            xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
            xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion">
  <s:Header>
    <wsa:Action s:mustUnderstand="1">urn:synthetic:ihe-action</wsa:Action>
    <wsa:MessageID>urn:uuid:11111111-1111-4111-8111-111111111111</wsa:MessageID>
    <wsa:ReplyTo><wsa:Address>{reply_to}</wsa:Address></wsa:ReplyTo>
    <wsse:Security s:mustUnderstand="1">
      <saml2:Assertion ID="synthetic-assertion">
        <saml2:Issuer>urn:nhs:names:services:spine</saml2:Issuer>
        <saml2:AttributeStatement />
      </saml2:Assertion>
    </wsse:Security>
  </s:Header>
  <s:Body>{body}</s:Body>
</s:Envelope>
"""


def _iti55_body(*, include_nhs_number: bool = True, include_ceid: bool = False) -> str:
    identifiers = [
        '<livingSubjectId><value root="1.2.826.0.1.3680043.10.999.2" '
        'extension="SYNTHETIC-LOCAL-ID"/></livingSubjectId>'
    ]
    if include_nhs_number:
        identifiers.append(
            f'<livingSubjectId><value root="{NHS_NUMBER_ROOT}" '
            f'extension="{NHS_NUMBER}"/></livingSubjectId>'
        )
    if include_ceid:
        identifiers.append(
            f'<livingSubjectId><value root="{CARE_EVERYWHERE_ROOT}" '
            f'extension="{CARE_EVERYWHERE_ID}"/></livingSubjectId>'
        )

    return f"""\
<PRPA_IN201305UV02 xmlns="urn:hl7-org:v3" ITSVersion="XML_1.0">
  <id root="22222222-2222-4222-8222-222222222222"/>
  <creationTime value="20240102030405"/>
  <interactionId root="2.16.840.1.113883.1.18" extension="PRPA_IN201305UV02"/>
  <processingCode code="P"/>
  <processingModeCode code="T"/>
  <acceptAckCode code="AL"/>
  <controlActProcess classCode="CACT" moodCode="EVN">
    <queryByParameter>
      <queryId root="33333333-3333-4333-8333-333333333333"/>
      <statusCode code="new"/>
      <responseModalityCode code="R"/>
      <responsePriorityCode code="I"/>
      <parameterList>{"".join(identifiers)}</parameterList>
    </queryByParameter>
  </controlActProcess>
</PRPA_IN201305UV02>
"""


def _iti38_body(patient_id: str) -> str:
    return f"""\
<query:AdhocQueryRequest
    xmlns:query="urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0"
    xmlns:rim="urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0">
  <query:ResponseOption returnComposedObjects="true" returnType="LeafClass"/>
  <rim:AdhocQuery id="urn:uuid:44444444-4444-4444-8444-444444444444">
    <rim:Slot name="$XDSDocumentEntryStatus">
      <rim:ValueList><rim:Value>('urn:synthetic:approved')</rim:Value></rim:ValueList>
    </rim:Slot>
    <rim:Slot name="$XDSDocumentEntryPatientId">
      <rim:ValueList><rim:Value>{patient_id}</rim:Value></rim:ValueList>
    </rim:Slot>
  </rim:AdhocQuery>
</query:AdhocQueryRequest>
"""


def _iti39_body(*, include_second_document: bool = False) -> str:
    second_document = ""
    if include_second_document:
        second_document = """
        <xds:DocumentRequest>
          <xds:HomeCommunityId>urn:oid:1.2.826.0.1.3680043.10.999.20</xds:HomeCommunityId>
          <xds:RepositoryUniqueId>1.2.826.0.1.3680043.10.999.21</xds:RepositoryUniqueId>
          <xds:DocumentUniqueId>1.2.826.0.1.3680043.10.999.22</xds:DocumentUniqueId>
        </xds:DocumentRequest>
        """

    return f"""\
<xds:RetrieveDocumentSetRequest xmlns:xds="urn:ihe:iti:xds-b:2007">
  <xds:DocumentRequest>
    <xds:HomeCommunityId>urn:oid:1.2.826.0.1.3680043.10.999.10</xds:HomeCommunityId>
    <xds:RepositoryUniqueId>1.2.826.0.1.3680043.10.999.11</xds:RepositoryUniqueId>
    <xds:DocumentUniqueId>{DOCUMENT_ID}</xds:DocumentUniqueId>
  </xds:DocumentRequest>
  {second_document}
</xds:RetrieveDocumentSetRequest>
"""


def _iti39_mime_message(body: str, *, reply_to: str = ANONYMOUS_REPLY_TO) -> str:
    # The production extractor expects the whole SOAP envelope on one MIME line,
    # matching the supplied transport example rather than a plain XML body.
    envelope = " ".join(
        line.strip() for line in _soap_envelope(body, reply_to=reply_to).splitlines()
    )
    return (
        "--synthetic-boundary\r\n"
        'Content-Type: application/xop+xml; type="application/soap+xml"\r\n'
        "Content-ID: <synthetic-soap-part>\r\n\r\n"
        f"{envelope}\r\n"
        "--synthetic-boundary--\r\n"
    )


@pytest.fixture
def complete_saml_context(monkeypatch):
    attributes = SimpleNamespace(
        subject_id="synthetic-user",
        organization="Synthetic NHS Organisation",
        organization_id="urn:oid:1.2.826.0.1.3680043.10.999.30",
        role="synthetic-clinician-role",
    )
    monkeypatch.setattr(soap, "process_saml_attributes", lambda _: attributes)
    return attributes


@pytest.mark.asyncio
async def test_iti55_uses_nhs_identifier_from_a_full_synthetic_query(
    monkeypatch, complete_saml_context
):
    patient = {
        "id": NHS_NUMBER,
        "meta": {"security": [{"code": "U"}]},
        "name": [
            {"use": "old", "given": ["Former"], "family": "Name"},
            {"use": "usual", "given": ["Alex"], "family": "Example"},
        ],
        "gender": "other",
        "birthDate": "1985-06-07",
    }
    lookup_patient = AsyncMock(return_value=patient)
    response_builder = AsyncMock(return_value="<synthetic-iti55-response/>")
    monkeypatch.setattr(soap, "lookup_patient", lookup_patient)
    monkeypatch.setattr(soap, "iti_55_response", response_builder)

    request = _request(_soap_envelope(_iti55_body()))
    response = await soap.iti55(request)

    assert response.status_code == 200
    assert response.body == b"<synthetic-iti55-response/>"
    lookup_patient.assert_awaited_once_with(NHS_NUMBER, request=request)
    message_id, actual_patient, query = response_builder.await_args.args
    assert message_id == "urn:uuid:11111111-1111-4111-8111-111111111111"
    assert actual_patient is patient
    assert query["queryId"]["@root"] == "33333333-3333-4333-8333-333333333333"


@pytest.mark.asyncio
async def test_iti55_returns_profile_error_when_nhs_identifier_is_missing(
    monkeypatch, complete_saml_context
):
    lookup_patient = AsyncMock()
    error_builder = AsyncMock(return_value="<synthetic-iti55-error/>")
    monkeypatch.setattr(soap, "lookup_patient", lookup_patient)
    monkeypatch.setattr(soap, "iti_55_error", error_builder)

    response = await soap.iti55(
        _request(_soap_envelope(_iti55_body(include_nhs_number=False)))
    )

    assert response.status_code == 200
    assert response.body == b"<synthetic-iti55-error/>"
    lookup_patient.assert_not_awaited()
    assert error_builder.await_args.kwargs["error_text"] == (
        "No NHS number found in request"
    )


@pytest.mark.asyncio
async def test_iti47_extracts_repeating_identifiers_and_caches_the_mapping(
    monkeypatch, complete_saml_context
):
    redis_client = MagicMock()
    patient = {"id": NHS_NUMBER, "name": [{"use": "usual", "family": "Example"}]}
    lookup_patient = AsyncMock(return_value=patient)
    response_builder = AsyncMock(return_value="<synthetic-iti47-response/>")
    monkeypatch.setattr(soap, "client", redis_client)
    monkeypatch.setattr(soap, "lookup_patient", lookup_patient)
    monkeypatch.setattr(soap, "iti_47_response", response_builder)
    monkeypatch.setattr(
        "app.audit.models._subject_ref_from_nhs_number",
        lambda nhs_number, secret: f"synthetic-hash:{nhs_number}:{secret}",
    )
    monkeypatch.setenv("API_KEY", "synthetic-test-secret")

    request = _request(
        _soap_envelope(_iti55_body(include_nhs_number=True, include_ceid=True))
    )
    response = await soap.iti47(request)

    assert response.status_code == 200
    redis_client.setex.assert_called_once_with(
        CARE_EVERYWHERE_ID,
        3600,
        f"synthetic-hash:{NHS_NUMBER}:synthetic-test-secret",
    )
    lookup_patient.assert_awaited_once_with(NHS_NUMBER, request=request)
    assert response_builder.await_args.args[:3] == (
        "urn:uuid:11111111-1111-4111-8111-111111111111",
        patient,
        CARE_EVERYWHERE_ID,
    )


@pytest.mark.asyncio
async def test_iti47_rejects_query_without_care_everywhere_identifier(
    complete_saml_context,
):
    with pytest.raises(HTTPException) as exc_info:
        await soap.iti47(_request(_soap_envelope(_iti55_body())))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid request, no care everywhere id found"


@pytest.mark.asyncio
async def test_iti38_normalizes_xds_patient_identifier_and_uses_query_id(
    monkeypatch, complete_saml_context
):
    response_builder = AsyncMock(return_value="<synthetic-iti38-response/>")
    monkeypatch.setattr(soap, "iti_38_response", response_builder)
    xds_patient_id = f"'{NHS_NUMBER}^^^&amp;{NHS_NUMBER_ROOT}&amp;ISO'"
    request = _request(_soap_envelope(_iti38_body(xds_patient_id)))

    response = await soap.iti38(request)

    assert response.status_code == 200
    assert response.body == b"<synthetic-iti38-response/>"
    response_builder.assert_awaited_once_with(
        request,
        NHS_NUMBER,
        "NOCEID",
        "urn:uuid:44444444-4444-4444-8444-444444444444",
        complete_saml_context,
    )


@pytest.mark.asyncio
async def test_iti38_rejects_patient_identifier_without_a_valid_nhs_number(
    complete_saml_context,
):
    request = _request(
        _soap_envelope(_iti38_body("'SYNTHETIC-NON-NHS-ID^^^&amp;1.2.3&amp;ISO'"))
    )

    with pytest.raises(HTTPException) as exc_info:
        await soap.iti38(request)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid NHS number format in request"


@pytest.mark.asyncio
async def test_iti39_extracts_mime_wrapped_request_and_uses_first_document(
    monkeypatch, complete_saml_context
):
    redis_client = MagicMock()
    redis_client.get.return_value = b"<ClinicalDocument>synthetic</ClinicalDocument>"
    response_builder = AsyncMock(return_value="<synthetic-iti39-response/>")
    monkeypatch.setattr(soap, "client", redis_client)
    monkeypatch.setattr(soap, "iti_39_response", response_builder)
    request = _request(_iti39_mime_message(_iti39_body(include_second_document=True)))

    response = await soap.iti39(request)

    assert response.status_code == 200
    assert response.body == b"<synthetic-iti39-response/>"
    redis_client.get.assert_called_once_with(DOCUMENT_ID)
    response_builder.assert_awaited_once_with(
        "urn:uuid:11111111-1111-4111-8111-111111111111",
        DOCUMENT_ID,
        b"<ClinicalDocument>synthetic</ClinicalDocument>",
    )


@pytest.mark.asyncio
async def test_iti39_returns_registry_error_when_document_is_not_cached(
    monkeypatch, complete_saml_context
):
    redis_client = MagicMock()
    redis_client.get.return_value = None
    error_builder = AsyncMock(return_value="<synthetic-iti39-error/>")
    monkeypatch.setattr(soap, "client", redis_client)
    monkeypatch.setattr(soap, "iti_39_error", error_builder)

    response = await soap.iti39(_request(_iti39_mime_message(_iti39_body())))

    assert response.status_code == 200
    assert response.body == b"<synthetic-iti39-error/>"
    error_builder.assert_awaited_once_with(
        "urn:uuid:11111111-1111-4111-8111-111111111111", DOCUMENT_ID
    )


@pytest.mark.asyncio
async def test_iti39_rejects_non_https_reply_to(monkeypatch, complete_saml_context):
    redis_client = MagicMock()
    redis_client.get.return_value = b"<ClinicalDocument>synthetic</ClinicalDocument>"
    monkeypatch.setattr(soap, "client", redis_client)
    monkeypatch.setattr(
        soap,
        "iti_39_response",
        AsyncMock(return_value="<synthetic-iti39-response/>"),
    )
    request = _request(
        _iti39_mime_message(
            _iti39_body(), reply_to="http://synthetic.internal.example/callback"
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await soap.iti39(request)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "ReplyTo must use https"


@pytest.mark.parametrize("handler", [soap.iti55, soap.iti47, soap.iti38, soap.iti39])
@pytest.mark.asyncio
async def test_soap_handlers_reject_unsupported_content_type(handler):
    with pytest.raises(HTTPException) as exc_info:
        await handler(_request("not soap", content_type="application/json"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Content type application/json not supported"
