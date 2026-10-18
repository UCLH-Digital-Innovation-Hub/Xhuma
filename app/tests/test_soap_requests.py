"""Request-level tests built from synthetic IHE SOAP messages.

The XML below mirrors the structure of the supplied examples and the IHE
schemas, but every identifier and demographic value is fabricated.  Keeping
the messages inline also makes it clear that the proprietary examples are not
test fixtures and are never read by this suite.
"""

from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from xml.etree import ElementTree as ET

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.requests import Request

from app.soap import saml_helper, soap
from app.soap.responses import iti_39

NHS_NUMBER = "9999999999"
NHS_NUMBER_ROOT = "2.16.840.1.113883.2.1.4.1"
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


def _iti55_body(*, include_nhs_number: bool = True) -> str:
    identifiers = [
        '<livingSubjectId><value root="1.2.826.0.1.3680043.10.999.2" extension="SYNTHETIC-LOCAL-ID"/></livingSubjectId>'
    ]
    if include_nhs_number:
        identifiers.append(
            f'<livingSubjectId><value root="{NHS_NUMBER_ROOT}" extension="{NHS_NUMBER}"/></livingSubjectId>'
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
    envelope = " ".join(line.strip() for line in _soap_envelope(body, reply_to=reply_to).splitlines())
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
    monkeypatch.setattr(saml_helper, "process_saml_attributes", lambda _: attributes)
    monkeypatch.setattr(soap, "attempt_audit", AsyncMock())
    monkeypatch.setattr(iti_39, "attempt_audit", AsyncMock())
    return attributes


@pytest.mark.asyncio
async def test_iti55_uses_nhs_identifier_from_a_full_synthetic_query(monkeypatch, complete_saml_context):
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
    lookup_patient.assert_awaited_once_with(NHS_NUMBER, request=request, saml=complete_saml_context)
    message_id, actual_patient, query = response_builder.await_args.args
    assert message_id == "urn:uuid:11111111-1111-4111-8111-111111111111"
    assert actual_patient is patient
    assert query["queryId"]["@root"] == "33333333-3333-4333-8333-333333333333"


@pytest.mark.asyncio
async def test_iti55_returns_profile_error_when_nhs_identifier_is_missing(monkeypatch, complete_saml_context):
    lookup_patient = AsyncMock()
    error_builder = AsyncMock(return_value="<synthetic-iti55-error/>")
    monkeypatch.setattr(soap, "lookup_patient", lookup_patient)
    monkeypatch.setattr(soap, "iti_55_error", error_builder)

    response = await soap.iti55(_request(_soap_envelope(_iti55_body(include_nhs_number=False))))

    assert response.status_code == 200
    assert response.body == b"<synthetic-iti55-error/>"
    lookup_patient.assert_not_awaited()
    assert error_builder.await_args.kwargs["error_text"] == ("No NHS number found in request")


@pytest.mark.asyncio
async def test_iti38_normalizes_xds_patient_identifier_and_uses_query_id(monkeypatch, complete_saml_context):
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
        message_id="urn:uuid:11111111-1111-4111-8111-111111111111",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("patient_id", ["'SYNTHETIC-NON-NHS-ID^^^&amp;1.2.3&amp;ISO'", "1234567890"])
async def test_iti38_returns_registry_error_for_invalid_nhs_number(
    monkeypatch,
    complete_saml_context,
    patient_id,
):
    response_builder = AsyncMock()
    monkeypatch.setattr(soap, "iti_38_response", response_builder)
    request = _request(_soap_envelope(_iti38_body(patient_id)))

    response = await soap.iti38(request)

    assert response.status_code == 200
    assert response.media_type == "application/soap+xml"
    response_builder.assert_not_awaited()
    root = ET.fromstring(response.body)
    namespaces = {
        "s": "http://www.w3.org/2003/05/soap-envelope",
        "wsa": "http://www.w3.org/2005/08/addressing",
        "query": "urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0",
        "rs": "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0",
    }
    assert root.findtext("s:Header/wsa:Action", namespaces=namespaces) == ("urn:ihe:iti:2007:CrossGatewayQueryResponse")
    assert root.findtext("s:Header/wsa:RelatesTo", namespaces=namespaces) == (
        "urn:uuid:11111111-1111-4111-8111-111111111111"
    )
    query_response = root.find("s:Body/query:AdhocQueryResponse", namespaces)
    assert query_response is not None
    assert query_response.attrib["status"] == "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Failure"
    error = query_response.find("rs:RegistryErrorList/rs:RegistryError", namespaces)
    assert error is not None
    assert error.attrib["errorCode"] == "XDSRegistryError"
    assert error.attrib["codeContext"] == "Invalid NHS number format in request"
    assert error.attrib["severity"] == "urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error"


@pytest.mark.asyncio
async def test_iti39_extracts_mime_wrapped_request_and_uses_first_document(monkeypatch, complete_saml_context):
    redis_client = AsyncMock()
    redis_client.hmget.return_value = [b"<ClinicalDocument>synthetic</ClinicalDocument>", NHS_NUMBER.encode()]
    response_builder = AsyncMock(return_value="<synthetic-iti39-response/>")
    monkeypatch.setattr(soap, "client", redis_client)
    monkeypatch.setattr(soap, "iti_39_response", response_builder)
    request = _request(_iti39_mime_message(_iti39_body(include_second_document=True)))

    response = await soap.iti39(request)

    assert response.status_code == 200
    assert response.body == b"<synthetic-iti39-response/>"
    redis_client.hmget.assert_awaited_once_with(f"document:{DOCUMENT_ID}", "content", "nhs_number")
    redis_client.get.assert_not_awaited()
    assert iti_39.attempt_audit.await_args.kwargs["nhs_number"] == NHS_NUMBER
    assert iti_39.attempt_audit.await_args.kwargs["detail"] == {"delivery_mode": "sync"}
    response_builder.assert_awaited_once_with(
        "urn:uuid:11111111-1111-4111-8111-111111111111",
        DOCUMENT_ID,
        b"<ClinicalDocument>synthetic</ClinicalDocument>",
    )


@pytest.mark.asyncio
async def test_iti39_returns_registry_error_when_document_is_not_cached(monkeypatch, complete_saml_context):
    redis_client = AsyncMock()
    redis_client.hmget.return_value = [None, None]
    error_builder = AsyncMock(return_value="<synthetic-iti39-error/>")
    monkeypatch.setattr(soap, "client", redis_client)
    monkeypatch.setattr(soap, "iti_39_error", error_builder)

    response = await soap.iti39(_request(_iti39_mime_message(_iti39_body())))

    assert response.status_code == 200
    assert response.body == b"<synthetic-iti39-error/>"
    assert iti_39.attempt_audit.await_args.kwargs["nhs_number"] is None
    redis_client.hmget.assert_awaited_once_with(f"document:{DOCUMENT_ID}", "content", "nhs_number")
    error_builder.assert_awaited_once_with(
        "urn:uuid:11111111-1111-4111-8111-111111111111",
        DOCUMENT_ID,
        error_code="XDSDocumentUniqueIdError",
        error_text=None,
    )


@pytest.mark.asyncio
async def test_iti39_rejects_non_https_reply_to(monkeypatch, complete_saml_context):
    redis_client = AsyncMock()
    redis_client.hmget.return_value = [b"<ClinicalDocument>synthetic</ClinicalDocument>", NHS_NUMBER.encode()]
    monkeypatch.setattr(soap, "client", redis_client)
    monkeypatch.setattr(
        soap,
        "iti_39_response",
        AsyncMock(return_value="<synthetic-iti39-response/>"),
    )
    request = _request(_iti39_mime_message(_iti39_body(), reply_to="http://synthetic.internal.example/callback"))

    with pytest.raises(HTTPException) as exc_info:
        await soap.iti39(request)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "ReplyTo must use https"
    assert iti_39.attempt_audit.await_args.kwargs["nhs_number"] == NHS_NUMBER


@pytest.mark.parametrize("handler", [soap.iti55, soap.iti38, soap.iti39])
@pytest.mark.asyncio
async def test_soap_handlers_reject_unsupported_content_type(handler):
    with pytest.raises(HTTPException) as exc_info:
        await handler(_request("not soap", content_type="application/json"))

    assert exc_info.value.status_code == 415
    assert exc_info.value.detail == "Content type application/json not supported"


@pytest.fixture
def soap_client(complete_saml_context):
    app = FastAPI()
    soap.register_handlers(app)
    app.include_router(soap.router)
    return TestClient(app)


FAULT_NS = {
    "env": "http://www.w3.org/2003/05/soap-envelope",
    "wsa": "http://www.w3.org/2005/08/addressing",
}


def _sender_fault(response):
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/soap+xml")
    root = ET.fromstring(response.content)
    assert root.findtext("env:Body/env:Fault/env:Code/env:Value", namespaces=FAULT_NS) == "env:Sender"
    return root


@pytest.mark.parametrize("endpoint", ["iti38", "iti39", "iti55"])
@pytest.mark.parametrize("body", ["<broken", "<not-a-soap-envelope/>", "<s:Envelope/>"])
def test_malformed_soap_returns_sender_fault(soap_client, endpoint, body):
    response = soap_client.post(f"/SOAP/{endpoint}", content=body, headers={"Content-Type": "application/soap+xml"})
    _sender_fault(response)


@pytest.mark.parametrize("endpoint", ["iti38", "iti39", "iti55"])
def test_invalid_transaction_structure_returns_sender_fault(soap_client, endpoint):
    response = soap_client.post(
        f"/SOAP/{endpoint}",
        content=_soap_envelope("<UnexpectedRequest/>"),
        headers={"Content-Type": "application/soap+xml"},
    )
    root = _sender_fault(response)
    assert root.findtext("env:Header/wsa:RelatesTo", namespaces=FAULT_NS) == (
        "urn:uuid:11111111-1111-4111-8111-111111111111"
    )


def test_iti38_missing_patient_parameter_returns_specific_registry_error(soap_client, monkeypatch):
    response_builder = AsyncMock()
    monkeypatch.setattr(soap, "iti_38_response", response_builder)
    response = soap_client.post(
        "/SOAP/iti38",
        content=_soap_envelope(_iti38_body("")),
        headers={"Content-Type": "application/soap+xml"},
    )
    assert response.status_code == 200
    root = ET.fromstring(response.content)
    error = root.find(".//{urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0}RegistryError")
    assert error is not None
    assert error.attrib["errorCode"] == "XDSStoredQueryMissingParam"
    assert "$XDSDocumentEntryPatientId" in error.attrib["codeContext"]
    response_builder.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        "<RetrieveDocumentSetRequest><DocumentRequest/></RetrieveDocumentSetRequest>",
        "<RetrieveDocumentSetRequest>invalid structure</RetrieveDocumentSetRequest>",
    ],
)
def test_iti39_missing_id_or_malformed_model_returns_sender_fault(soap_client, monkeypatch, body):
    cache = AsyncMock()
    monkeypatch.setattr(soap, "client", cache)
    response = soap_client.post(
        "/SOAP/iti39",
        content=_soap_envelope(body),
        headers={"Content-Type": "application/soap+xml"},
    )
    _sender_fault(response)
    cache.hmget.assert_not_awaited()


@pytest.mark.parametrize("upstream_status,expected_status", [(404, 200), (502, 500)])
def test_iti55_maps_upstream_errors_to_soap(soap_client, monkeypatch, upstream_status, expected_status):
    monkeypatch.setattr(
        soap, "lookup_patient", AsyncMock(side_effect=HTTPException(upstream_status, "PRIVATE_UPSTREAM_ERROR"))
    )
    response = soap_client.post(
        "/SOAP/iti55",
        content=_soap_envelope(_iti55_body()),
        headers={"Content-Type": "application/soap+xml"},
    )
    assert response.status_code == expected_status
    assert response.headers["content-type"].startswith("application/soap+xml")
    assert "PRIVATE_UPSTREAM_ERROR" not in response.text
    root = ET.fromstring(response.content)
    if expected_status == 500:
        assert root.findtext("env:Body/env:Fault/env:Code/env:Value", namespaces=FAULT_NS) == "env:Receiver"
    else:
        assert root.find(".//{urn:hl7-org:v3}acknowledgement/{urn:hl7-org:v3}typeCode").attrib["code"] == "AE"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("cache_result", ["hit", "miss", "unavailable"])
def test_iti39_delivery_preserves_application_outcome(soap_client, monkeypatch, asynchronous, cache_result):

    cache = AsyncMock()
    cache.hmget.return_value = (
        [b"synthetic-base64-content", NHS_NUMBER.encode()] if cache_result == "hit" else [None, None]
    )
    if cache_result == "unavailable":
        cache.hmget.side_effect = RedisConnectionError("PRIVATE_CACHE_FAILURE")
    monkeypatch.setattr(soap, "client", cache)
    post = MagicMock()
    monkeypatch.setattr(iti_39.httpx, "post", post)
    reply_to = "https://synthetic.nhs.uk/callback" if asynchronous else ANONYMOUS_REPLY_TO
    response = soap_client.post(
        "/SOAP/iti39",
        content=_soap_envelope(_iti39_body(), reply_to=reply_to),
        headers={"Content-Type": "application/soap+xml"},
    )
    if asynchronous:
        assert response.status_code == 202
        assert response.content == b""
        assert "content-type" not in response.headers
        post.assert_called_once()
        assert post.call_args.args == (reply_to,)
        message = BytesParser(policy=policy.default).parsebytes(post.call_args.kwargs["data"])
        root = ET.fromstring(next(message.iter_parts()).get_payload(decode=True))
    else:
        assert response.status_code == 200
        post.assert_not_called()
        root = ET.fromstring(response.content)
    assert root.findtext(
        "{http://www.w3.org/2003/05/soap-envelope}Header/{http://www.w3.org/2005/08/addressing}RelatesTo"
    ) == ("urn:uuid:11111111-1111-4111-8111-111111111111")
    error = root.find(".//{urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0}RegistryError")
    if cache_result == "hit":
        assert error is None
        assert root.find(".//{urn:ihe:iti:xds-b:2007}Document").text == "synthetic-base64-content"
    else:
        expected_code = "XDSRepositoryError" if cache_result == "unavailable" else "XDSDocumentUniqueIdError"
        assert error is not None
        assert error.attrib["errorCode"] == expected_code
        assert "PRIVATE_CACHE_FAILURE" not in error.attrib["codeContext"]
        assert iti_39.attempt_audit.await_args.kwargs["error_code"] == expected_code
    assert iti_39.attempt_audit.await_args.kwargs["outcome"].value == ("ok" if cache_result == "hit" else "fail")


@pytest.mark.parametrize(
    "reply_to", ["http://synthetic.nhs.uk/callback", "https://malicious.example/callback", "https://[invalid"]
)
def test_iti39_rejected_reply_to_returns_addressing_fault(soap_client, monkeypatch, reply_to):
    cache = AsyncMock()
    cache.hmget.return_value = [b"synthetic-document", NHS_NUMBER.encode()]
    monkeypatch.setattr(soap, "client", cache)
    post = MagicMock()
    monkeypatch.setattr(iti_39.httpx, "post", post)
    response = soap_client.post(
        "/SOAP/iti39",
        content=_soap_envelope(_iti39_body(), reply_to=reply_to),
        headers={"Content-Type": "application/soap+xml"},
    )
    root = _sender_fault(response)
    assert (
        root.findtext("env:Body/env:Fault/env:Code/env:Subcode/env:Value", namespaces=FAULT_NS)
        == "wsa:InvalidAddressingHeader"
    )
    assert root.findtext("env:Body/env:Fault/env:Detail/wsa:ProblemHeaderQName", namespaces=FAULT_NS) == "wsa:ReplyTo"
    post.assert_not_called()
    assert iti_39.attempt_audit.await_args.kwargs["outcome"].value == "deny"
