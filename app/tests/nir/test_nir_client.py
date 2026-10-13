"""NIR requests use their own configuration and transport, independently of GP Connect."""

import base64
import os
from email import policy
from email.parser import BytesParser
from unittest.mock import patch
from xml.etree import ElementTree as ET

import httpx
import pytest

from app.nir.client import NIRClient
from app.nir.config import NIRConfig
from app.soap.nir_models import (
    NIRDocumentReference,
    NIRImagingSeriesRequest,
    NIRImagingStudyRequest,
)

SOAP_ENVELOPE = b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body/></s:Envelope>'
TRANSACTIONS = {
    "iti38": "/xca/iti-38",
    "iti39": "/xca/iti-39",
    "rad75": "/xcai/rad-75",
}
QUERY_RESPONSE = b"""
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
    xmlns:query="urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0"
    xmlns:rim="urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0">
  <s:Body>
    <query:AdhocQueryResponse status="urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success">
      <rim:RegistryObjectList>
        <rim:ExtrinsicObject id="synthetic-document" home="urn:oid:1.2.3" mimeType="application/dicom">
          <rim:Slot name="repositoryUniqueId"><rim:ValueList><rim:Value>repository-1</rim:Value></rim:ValueList></rim:Slot>
          <rim:ExternalIdentifier identificationScheme="urn:uuid:2e82c1f6-a085-4c72-9da3-8640a32e42ab" value="1.2.3.4"/>
        </rim:ExtrinsicObject>
      </rim:RegistryObjectList>
    </query:AdhocQueryResponse>
  </s:Body>
</s:Envelope>
"""


@pytest.fixture
def document_reference():
    return NIRDocumentReference(
        home_community_id="urn:oid:1.2.3",
        repository_unique_id="repository-1",
        document_unique_id="1.2.3.4",
        mime_type="application/dicom",
    )


@pytest.fixture(autouse=True)
def isolate_nir_environment(monkeypatch):
    for name in os.environ:
        if name.startswith("NIR_"):
            monkeypatch.delenv(name)


def test_nir_defaults_ignore_gpconnect_environment_and_credentials(monkeypatch):
    monkeypatch.setenv("ENV", "int")
    monkeypatch.setenv("API_KEY", "gpconnect-api-key")
    monkeypatch.setenv("JWTKEY", "gpconnect-signing-key")
    monkeypatch.setenv("TLS_CERT_PATH", "/gpconnect/cert.pem")
    monkeypatch.setenv("TLS_KEY_PATH", "/gpconnect/key.pem")
    monkeypatch.setenv("CERT_PATH", "/gpconnect/cert.pem")
    monkeypatch.setenv("KEY_PATH", "/gpconnect/key.pem")

    config = NIRConfig.from_env()

    assert config.environment == "sandbox"
    assert config.base_url == "https://sandbox.nir.national.nhs.uk"
    assert config.timeout == 30.0
    assert config.tls_cert_path is None
    assert config.tls_key_path is None
    assert config.ca_bundle_path is None


def test_nir_environment_and_overrides_are_separate(monkeypatch):
    monkeypatch.setenv("ENV", "int")
    monkeypatch.setenv("NIR_ENV", "dev")
    monkeypatch.setenv("NIR_BASE_URL", "https://nir-sandbox.example.test")
    monkeypatch.setenv("NIR_TIMEOUT", "12.5")
    monkeypatch.setenv("NIR_TLS_CERT_PATH", "/nir/cert.pem")
    monkeypatch.setenv("NIR_TLS_KEY_PATH", "/nir/key.pem")
    monkeypatch.setenv("NIR_CA_BUNDLE_PATH", "/nir/ca.pem")

    config = NIRConfig.from_env()

    assert config.environment == "sandbox"
    assert config.base_url == "https://nir-sandbox.example.test"
    assert config.timeout == 12.5
    assert config.tls_cert_path == "/nir/cert.pem"
    assert config.tls_key_path == "/nir/key.pem"
    assert config.ca_bundle_path == "/nir/ca.pem"


@pytest.mark.parametrize("environment", ["int", "production"])
def test_non_sandbox_requires_nir_certificates_even_with_gpconnect_credentials(monkeypatch, environment):
    monkeypatch.setenv("NIR_ENV", environment)
    monkeypatch.setenv("TLS_CERT_PATH", "/gpconnect/cert.pem")
    monkeypatch.setenv("TLS_KEY_PATH", "/gpconnect/key.pem")

    with pytest.raises(ValueError):
        NIRConfig.from_env()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NIR_ENV", "unsupported"),
        ("NIR_TIMEOUT", "invalid"),
        ("NIR_TIMEOUT", "0"),
        ("NIR_TIMEOUT", "-1"),
        ("NIR_TIMEOUT", "nan"),
        ("NIR_TIMEOUT", "inf"),
        ("NIR_TLS_CERT_PATH", "/nir/cert.pem"),
        ("NIR_TLS_KEY_PATH", "/nir/key.pem"),
    ],
)
def test_invalid_nir_configuration_is_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        NIRConfig.from_env()


@pytest.mark.asyncio
async def test_client_calls_all_documented_endpoint_paths():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "UP"})
        return httpx.Response(200, text="<response/>")

    async with NIRClient(transport=httpx.MockTransport(respond)) as client:
        assert await client.health() == {"status": "UP"}
        for transaction in TRANSACTIONS:
            assert await client.get_wsdl(transaction) == "<response/>"
            assert await client.get_schema(transaction, "ebRS/rs.xsd") == "<response/>"
            response = await client.send_soap(transaction, SOAP_ENVELOPE)
            assert response.content == b"<response/>"

    expected = [("GET", "/health")]
    for path in TRANSACTIONS.values():
        expected.extend([("GET", f"{path}/service.wsdl"), ("GET", f"{path}/schema/ebRS/rs.xsd"), ("POST", path)])
    assert [(request.method, request.url.path) for request in requests] == expected
    assert all(request.url.host == "sandbox.nir.national.nhs.uk" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("transaction", TRANSACTIONS)
async def test_soap_headers_and_payload_do_not_include_gpconnect_credentials(monkeypatch, transaction):
    monkeypatch.setenv("ENV", "int")
    monkeypatch.setenv("API_KEY", "gpconnect-api-key")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=SOAP_ENVELOPE)

    async with NIRClient(transport=httpx.MockTransport(respond)) as client:
        await client.send_soap(transaction, SOAP_ENVELOPE.decode())

    request = requests[0]
    content_type = request.headers["content-type"]
    if transaction == "iti38":
        assert request.content == SOAP_ENVELOPE
        assert "application/soap+xml" in content_type
    else:
        assert "multipart/related" in content_type
        mime = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + request.content
        )
        parts = list(mime.iter_parts())
        assert len(parts) == 1
        assert parts[0].get_content_type() == "application/xop+xml"
        assert parts[0].get_payload(decode=True) == SOAP_ENVELOPE
    assert "authorization" not in request.headers
    assert "x-api-key" not in request.headers
    assert "gpconnect-api-key" not in str(request.headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema_file",
    [
        "../rs.xsd",
        "ebRS/../rs.xsd",
        "/rs.xsd",
        "https://example.test/rs.xsd",
        "rs.xsd?x=1",
        "rs.xsd#fragment",
        r"ebRS\rs.xsd",
        "%2e%2e/rs.xsd",
    ],
)
async def test_schema_paths_cannot_escape_transaction_endpoint(schema_file):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, text="<schema/>")

    async with NIRClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError):
            await client.get_schema("iti38", schema_file)

    assert requests == []


@pytest.mark.asyncio
async def test_unknown_transaction_is_rejected_before_network_io():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200)

    async with NIRClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError):
            await client.get_wsdl("iti55")
        with pytest.raises(ValueError):
            await client.get_schema("iti55", "rs.xsd")
        with pytest.raises(ValueError):
            await client.send_soap("iti55", SOAP_ENVELOPE)

    assert requests == []


@pytest.mark.asyncio
async def test_http_failure_is_exposed_to_caller():
    def respond(request):
        return httpx.Response(503, text="unavailable")

    async with NIRClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError) as error:
            await client.health()

    assert error.value.response.status_code == 503


@pytest.mark.asyncio
async def test_query_documents_returns_discovered_retrieval_identifiers(document_reference):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=QUERY_RESPONSE, headers={"Content-Type": "application/soap+xml"})

    async with NIRClient(transport=httpx.MockTransport(respond)) as client:
        result = await client.query_documents("9000000009")

    assert result.success
    assert result.errors == []
    assert result.documents == [document_reference]
    assert requests[0].url.path == "/xca/iti-38"
    assert b"9000000009" in requests[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("transaction", ["iti39", "rad75"])
async def test_retrieval_methods_preserve_binary_mtom_documents(transaction, document_reference):
    binary_document = b"\x00\xff\x80DICM\r\n\x00\xfe"
    envelope = b"""
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
    xmlns:xds="urn:ihe:iti:xds-b:2007"
    xmlns:rs="urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0"
    xmlns:xop="http://www.w3.org/2004/08/xop/include">
  <s:Body>
    <xds:RetrieveDocumentSetResponse>
      <rs:RegistryResponse status="urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success"/>
      <xds:DocumentResponse>
        <xds:HomeCommunityId>urn:oid:1.2.3</xds:HomeCommunityId>
        <xds:RepositoryUniqueId>repository-1</xds:RepositoryUniqueId>
        <xds:DocumentUniqueId>1.2.3.4</xds:DocumentUniqueId>
        <xds:mimeType>application/dicom</xds:mimeType>
        <xds:Document><xop:Include href="cid:document@nir"/></xds:Document>
      </xds:DocumentResponse>
    </xds:RetrieveDocumentSetResponse>
  </s:Body>
</s:Envelope>
"""
    content_type = 'multipart/related; boundary="nir-response"; type="application/xop+xml"; start="<root@nir>"'
    response_content = (
        b"--nir-response\r\nContent-Type: application/xop+xml\r\n"
        b"Content-ID: <root@nir>\r\nContent-Transfer-Encoding: binary\r\n\r\n"
        + envelope
        + b"\r\n--nir-response\r\nContent-Type: application/dicom\r\n"
        b"Content-ID: <document@nir>\r\nContent-Transfer-Encoding: binary\r\n\r\n"
        + binary_document
        + b"\r\n--nir-response--\r\n"
    )
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=response_content, headers={"Content-Type": content_type})

    async with NIRClient(transport=httpx.MockTransport(respond)) as client:
        if transaction == "iti39":
            result = await client.retrieve_documents([document_reference])
        else:
            study = NIRImagingStudyRequest(
                study_instance_uid="1.2.3.5",
                series=[NIRImagingSeriesRequest(series_instance_uid="1.2.3.6", documents=[document_reference])],
            )
            result = await client.retrieve_imaging_documents([study], transfer_syntax_uids=["1.2.840.10008.1.2.1"])

    assert requests[0].url.path == TRANSACTIONS[transaction]
    assert "multipart/related" in requests[0].headers["content-type"]
    assert result.success
    assert result.errors == []
    assert len(result.documents) == 1
    retrieved = result.documents[0]
    assert retrieved.document_unique_id == document_reference.document_unique_id
    assert retrieved.mime_type == "application/dicom"
    assert retrieved.content == binary_document


@pytest.mark.asyncio
@pytest.mark.parametrize("second_gateway_fails", [False, True])
async def test_retrieve_documents_groups_communities_and_preserves_partial_results(second_gateway_fails):
    documents = [
        NIRDocumentReference(home_community_id=home, repository_unique_id="repository", document_unique_id=unique_id)
        for home, unique_id in [
            ("urn:oid:1.2.3", "1.2.3.4"),
            ("urn:oid:1.2.5", "1.2.3.4"),
            ("urn:oid:1.2.3", "1.2.3.6"),
        ]
    ]
    payloads = {
        (document.home_community_id, document.document_unique_id): b"\x00\xff" + str(index).encode()
        for index, document in enumerate(documents)
    }
    grouped_requests = []
    xds_namespace = "urn:ihe:iti:xds-b:2007"

    def respond(request):
        assert request.url.path == "/xca/iti-39"
        mime = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content
        )
        envelope = ET.fromstring(list(mime.iter_parts())[0].get_payload(decode=True))
        requested = [
            (
                item.findtext(f"{{{xds_namespace}}}HomeCommunityId"),
                item.findtext(f"{{{xds_namespace}}}DocumentUniqueId"),
            )
            for item in envelope.iter(f"{{{xds_namespace}}}DocumentRequest")
        ]
        grouped_requests.append(requested)
        assert len({home for home, _ in requested}) == 1
        failed = second_gateway_fails and requested[0][0] == "urn:oid:1.2.5"
        status = "Failure" if failed else "Success"
        registry_error = (
            '<rs:RegistryErrorList><rs:RegistryError errorCode="XDSRepositoryError" codeContext="Gateway unavailable"/>'
            "</rs:RegistryErrorList>"
            if failed
            else ""
        )
        retrieved = (
            ""
            if failed
            else "".join(
                f"<xds:DocumentResponse><xds:HomeCommunityId>{home}</xds:HomeCommunityId>"
                "<xds:RepositoryUniqueId>repository</xds:RepositoryUniqueId>"
                f"<xds:DocumentUniqueId>{unique_id}</xds:DocumentUniqueId>"
                "<xds:mimeType>application/dicom</xds:mimeType>"
                f"<xds:Document>{base64.b64encode(payloads[(home, unique_id)]).decode()}</xds:Document>"
                "</xds:DocumentResponse>"
                for home, unique_id in requested
            )
        )
        response = (
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            f'xmlns:xds="{xds_namespace}" xmlns:rs="urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0">'
            "<s:Body><xds:RetrieveDocumentSetResponse>"
            f'<rs:RegistryResponse status="urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:{status}">'
            f"{registry_error}</rs:RegistryResponse>{retrieved}"
            "</xds:RetrieveDocumentSetResponse></s:Body></s:Envelope>"
        )
        return httpx.Response(200, text=response, headers={"Content-Type": "application/soap+xml"})

    async with NIRClient(transport=httpx.MockTransport(respond)) as client:
        result = await client.retrieve_documents(documents)

    assert grouped_requests == [
        [("urn:oid:1.2.3", "1.2.3.4"), ("urn:oid:1.2.3", "1.2.3.6")],
        [("urn:oid:1.2.5", "1.2.3.4")],
    ]
    actual = {(item.home_community_id, item.document_unique_id): item.content for item in result.documents}
    if second_gateway_fails:
        assert result.status == "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:PartialSuccess"
        assert not result.success
        assert result.errors == ["XDSRepositoryError: Gateway unavailable"]
        assert actual == {key: value for key, value in payloads.items() if key[0] == "urn:oid:1.2.3"}
    else:
        assert result.success
        assert result.errors == []
        assert actual == payloads


@pytest.mark.asyncio
async def test_empty_document_retrieval_is_rejected_before_network_io():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200)

    async with NIRClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match="At least one document"):
            await client.retrieve_documents([])

    assert requests == []


@pytest.mark.asyncio
async def test_prepared_retrieval_envelope_is_sent_once_for_multiple_communities(document_reference):
    requests = []
    response = b"""
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
    xmlns:xds="urn:ihe:iti:xds-b:2007" xmlns:rs="urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0">
  <s:Body><xds:RetrieveDocumentSetResponse>
    <rs:RegistryResponse status="urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success"/>
  </xds:RetrieveDocumentSetResponse></s:Body>
</s:Envelope>
"""

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=response, headers={"Content-Type": "application/soap+xml"})

    documents = [document_reference, document_reference.model_copy(update={"home_community_id": "urn:oid:1.2.5"})]
    async with NIRClient(transport=httpx.MockTransport(respond)) as client:
        result = await client.retrieve_documents(documents, soap_envelope=SOAP_ENVELOPE)

    assert result.success
    assert len(requests) == 1
    mime = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {requests[0].headers['content-type']}\r\n\r\n".encode() + requests[0].content
    )
    assert list(mime.iter_parts())[0].get_payload(decode=True) == SOAP_ENVELOPE


@pytest.mark.asyncio
async def test_non_sandbox_generated_calls_require_prepared_envelopes():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200)

    config = NIRConfig(
        environment="int",
        base_url="https://int.nir.national.nhs.uk",
        tls_cert_path="/nir/cert.pem",
        tls_key_path="/nir/key.pem",
    )
    with patch("ssl.SSLContext.load_cert_chain"):
        async with NIRClient(config, transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(ValueError, match="caller-supplied"):
                await client.query_documents("9000000009")
            with pytest.raises(ValueError, match="caller-supplied"):
                await client.retrieve_documents([])
            with pytest.raises(ValueError, match="caller-supplied"):
                await client.retrieve_imaging_documents([])

    assert requests == []


@pytest.mark.asyncio
async def test_int_accepts_caller_prepared_envelope_without_replacing_it():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=QUERY_RESPONSE)

    config = NIRConfig(
        environment="int",
        base_url="https://int.nir.national.nhs.uk",
        tls_cert_path="/nir/cert.pem",
        tls_key_path="/nir/key.pem",
    )
    with patch("ssl.SSLContext.load_cert_chain"):
        async with NIRClient(config, transport=httpx.MockTransport(respond)) as client:
            result = await client.query_documents("9000000009", soap_envelope=SOAP_ENVELOPE)

    assert result.success
    assert requests[0].url.host == "int.nir.national.nhs.uk"
    assert requests[0].content == SOAP_ENVELOPE
