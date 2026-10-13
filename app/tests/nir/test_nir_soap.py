import base64
from email import policy
from email.parser import BytesParser

import pytest
from defusedxml import ElementTree as ET
from defusedxml.common import EntitiesForbidden

from app.soap.nir_models import (
    XDS_FAILURE_STATUS,
    XDS_SUCCESS_STATUS,
    NIRDocumentReference,
    NIRImagingSeriesRequest,
    NIRImagingStudyRequest,
)
from app.soap.nir_queries import (
    ITI38_ACTION,
    ITI39_ACTION,
    QUERY_NS,
    RAD75_ACTION,
    RIM_NS,
    SOAP_NS,
    WSA_NS,
    XDS_NS,
    XDSI_NS,
    build_iti38_request,
    build_iti39_request,
    build_rad75_request,
    wrap_mtom_request,
)
from app.soap.nir_responses import RS_NS, parse_iti38_response, parse_retrieve_response


@pytest.fixture
def reference():
    return NIRDocumentReference(
        home_community_id="urn:oid:1.2.3",
        repository_unique_id="1.2.3.4",
        document_unique_id="1.2.3.4.5",
        mime_type="application/dicom",
    )


def soap(body):
    return f'<s:Envelope xmlns:s="{SOAP_NS}"><s:Body>{body}</s:Body></s:Envelope>'.encode()


def retrieve_envelope(document_content, status=XDS_SUCCESS_STATUS, errors=""):
    return soap(
        f'<xds:RetrieveDocumentSetResponse xmlns:xds="{XDS_NS}" xmlns:rs="{RS_NS}">'
        f'<rs:RegistryResponse status="{status}">{errors}</rs:RegistryResponse>'
        "<xds:DocumentResponse><xds:HomeCommunityId>urn:oid:1.2.3</xds:HomeCommunityId>"
        "<xds:RepositoryUniqueId>1.2.3.4</xds:RepositoryUniqueId>"
        "<xds:DocumentUniqueId>1.2.3.4.5</xds:DocumentUniqueId>"
        "<xds:mimeType>application/dicom</xds:mimeType>"
        f"<xds:Document>{document_content}</xds:Document></xds:DocumentResponse>"
        "</xds:RetrieveDocumentSetResponse>"
    )


def test_iti38_uses_nhs_cx_find_documents_and_leaf_metadata():
    endpoint = "https://sandbox.example/xca/iti-38"
    envelope = ET.fromstring(build_iti38_request("9694116422", endpoint))
    header = envelope.find(f"{{{SOAP_NS}}}Header")
    assert header.findtext(f"{{{WSA_NS}}}Action") == ITI38_ACTION
    assert header.findtext(f"{{{WSA_NS}}}To") == endpoint
    assert header.findtext(f"{{{WSA_NS}}}MessageID").startswith("urn:uuid:")
    assert header.findtext(f"{{{WSA_NS}}}ReplyTo/{{{WSA_NS}}}Address") == f"{WSA_NS}/anonymous"
    request = envelope.find(f"{{{SOAP_NS}}}Body/{{{QUERY_NS}}}AdhocQueryRequest")
    assert request.find(f"{{{QUERY_NS}}}ResponseOption").get("returnType") == "LeafClass"
    query = request.find(f"{{{RIM_NS}}}AdhocQuery")
    assert query.get("id") == "urn:uuid:14d4debf-8f97-4251-9a74-a90016b0af0d"
    assert query.get("home") is None
    slots = {slot.get("name"): slot.findtext(f"{{{RIM_NS}}}ValueList/{{{RIM_NS}}}Value") for slot in query}
    assert slots["$XDSDocumentEntryPatientId"] == "'9694116422^^^&2.16.840.1.113883.2.1.4.1&ISO'"
    assert slots["$XDSDocumentEntryStatus"] == "('urn:oasis:names:tc:ebxml-regrep:StatusType:Approved')"
    assert b"Security" not in ET.tostring(envelope)


@pytest.mark.parametrize("nhs_number", ["123", "123456789a", "'123456789", "１２３４５６７８９０"])
def test_iti38_rejects_invalid_nhs_identifiers(nhs_number):
    with pytest.raises(ValueError, match="10 digits"):
        build_iti38_request(nhs_number, "https://sandbox.example/xca/iti-38")


def test_iti38_rejects_invalid_nhs_check_digit():
    with pytest.raises(ValueError, match="check digit"):
        build_iti38_request("9694116423", "https://sandbox.example/xca/iti-38")


def test_iti39_preserves_each_document_reference_and_xml_escapes_values(reference):
    second = reference.model_copy(update={"document_unique_id": "document<&>"})
    envelope = ET.fromstring(build_iti39_request([reference, second], "https://sandbox.example/xca/iti-39"))
    requests = envelope.findall(f"{{{SOAP_NS}}}Body/{{{XDS_NS}}}RetrieveDocumentSetRequest/{{{XDS_NS}}}DocumentRequest")
    assert len(requests) == 2
    assert requests[0].findtext(f"{{{XDS_NS}}}HomeCommunityId") == reference.home_community_id
    assert requests[0].findtext(f"{{{XDS_NS}}}RepositoryUniqueId") == reference.repository_unique_id
    assert requests[1].findtext(f"{{{XDS_NS}}}DocumentUniqueId") == "document<&>"
    assert envelope.findtext(f"{{{SOAP_NS}}}Header/{{{WSA_NS}}}Action") == ITI39_ACTION


def test_rad75_groups_documents_in_studies_and_series(reference):
    studies = [
        NIRImagingStudyRequest(
            study_instance_uid="1.2.840.study",
            series=[NIRImagingSeriesRequest(series_instance_uid="1.2.840.series", documents=[reference])],
        )
    ]
    envelope = ET.fromstring(build_rad75_request(studies, "https://sandbox.example/xcai/rad-75"))
    request = envelope.find(f"{{{SOAP_NS}}}Body/{{{XDSI_NS}}}RetrieveImagingDocumentSetRequest")
    study = request.find(f"{{{XDSI_NS}}}StudyRequest")
    series = study.find(f"{{{XDSI_NS}}}SeriesRequest")
    assert study.get("studyInstanceUID") == studies[0].study_instance_uid
    assert series.get("seriesInstanceUID") == studies[0].series[0].series_instance_uid
    assert series.findtext(f"{{{XDS_NS}}}DocumentRequest/{{{XDS_NS}}}DocumentUniqueId") == reference.document_unique_id
    assert (
        request.findtext(f"{{{XDSI_NS}}}TransferSyntaxUIDList/{{{XDSI_NS}}}TransferSyntaxUID") == "1.2.840.10008.1.2.1"
    )
    assert envelope.findtext(f"{{{SOAP_NS}}}Header/{{{WSA_NS}}}Action") == RAD75_ACTION


def test_mtom_request_headers_and_root_are_consistent(reference):
    envelope = build_iti39_request([reference], "https://sandbox.example/xca/iti-39")
    body, content_type = wrap_mtom_request(envelope, ITI39_ACTION)
    message = BytesParser(policy=policy.default).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    assert message.get_content_type() == "multipart/related"
    assert message.get_param("type") == "application/xop+xml"
    assert message.get_param("start-info") == f'application/soap+xml; action="{ITI39_ACTION}"'
    parts = list(message.iter_parts())
    assert len(parts) == 1
    assert parts[0]["Content-ID"] == message.get_param("start")
    assert parts[0].get_content_type() == "application/xop+xml"
    assert parts[0].get_payload(decode=True) == envelope


def test_query_response_uses_document_unique_id_instead_of_registry_uuid():
    response = soap(
        f'<query:AdhocQueryResponse xmlns:query="{QUERY_NS}" xmlns:rim="{RIM_NS}" status="{XDS_SUCCESS_STATUS}">'
        '<rim:RegistryObjectList><rim:ExtrinsicObject id="urn:uuid:registry-entry" home="urn:oid:1.2.3" mimeType="application/pdf">'
        '<rim:Slot name="repositoryUniqueId"><rim:ValueList><rim:Value>1.2.3.4</rim:Value></rim:ValueList></rim:Slot>'
        '<rim:ExternalIdentifier identificationScheme="urn:uuid:58a6f841-87b3-4a3e-92fd-a8ffeff98427" value="patient-id"/>'
        '<rim:ExternalIdentifier identificationScheme="urn:uuid:2e82c1f6-a085-4c72-9da3-8640a32e42ab" value="1.2.3.4.5"/>'
        "</rim:ExtrinsicObject></rim:RegistryObjectList></query:AdhocQueryResponse>"
    )
    parsed = parse_iti38_response(response)
    assert parsed.success
    assert parsed.documents == [
        NIRDocumentReference(
            home_community_id="urn:oid:1.2.3",
            repository_unique_id="1.2.3.4",
            document_unique_id="1.2.3.4.5",
            mime_type="application/pdf",
        )
    ]


def test_retrieve_inline_base64_preserves_bytes():
    binary = b"\x00\xff\xfeDICM\r\n\x00"
    response = parse_retrieve_response(retrieve_envelope(base64.b64encode(binary).decode()), "application/soap+xml")
    assert response.success
    assert response.documents[0].content == binary
    assert response.documents[0].mime_type == "application/dicom"
    assert response.documents[0].document_unique_id == "1.2.3.4.5"


def test_retrieve_mtom_preserves_binary_and_uses_start_even_when_root_is_second():
    binary = b"\x00\xff\xfeDICM\r\n\x00\r\n"
    envelope = retrieve_envelope(
        '<xop:Include xmlns:xop="http://www.w3.org/2004/08/xop/include" href="cid:dicom%40document"/>'
    )
    content_type = 'multipart/related; type="application/xop+xml"; boundary="nir-test"; start="<root@nir>"'
    content = (
        b"--nir-test\r\nContent-Type: application/dicom\r\nContent-Transfer-Encoding: binary\r\n"
        b"Content-ID: <dicom@document>\r\n\r\n" + binary + b"\r\n"
        b'--nir-test\r\nContent-Type: application/xop+xml; type="application/soap+xml"\r\n'
        b"Content-ID: <root@nir>\r\n\r\n" + envelope + b"\r\n--nir-test--\r\n"
    )
    response = parse_retrieve_response(content, content_type)
    assert response.success
    assert response.documents[0].content == binary


@pytest.mark.parametrize(
    "parser", [parse_iti38_response, lambda value: parse_retrieve_response(value, "application/soap+xml")]
)
def test_soap_fault_is_not_success(parser):
    response = parser(
        soap(
            '<s:Fault><s:Code><s:Value>s:Receiver</s:Value></s:Code><s:Reason><s:Text xml:lang="en">Gateway unavailable</s:Text></s:Reason></s:Fault>'
        )
    )
    assert not response.success
    assert response.status == XDS_FAILURE_STATUS
    assert response.errors == ["Gateway unavailable"]


def test_registry_error_is_not_success_even_with_success_status():
    errors = '<rs:RegistryErrorList><rs:RegistryError errorCode="XDSRepositoryError" codeContext="Failed to retrieve"/></rs:RegistryErrorList>'
    response = parse_retrieve_response(retrieve_envelope("", errors=errors), "application/soap+xml")
    assert not response.success
    assert response.errors == ["XDSRepositoryError: Failed to retrieve"]


def test_missing_mtom_attachment_is_not_success():
    envelope = retrieve_envelope('<xop:Include xmlns:xop="http://www.w3.org/2004/08/xop/include" href="cid:missing"/>')
    response = parse_retrieve_response(envelope, "application/soap+xml")
    assert not response.success
    assert not response.documents
    assert response.errors == ["Document 1.2.3.4.5 refers to a missing MTOM attachment"]


def test_unexpected_document_xml_is_not_empty_successful_content():
    response = parse_retrieve_response(retrieve_envelope("<unexpected>Invalid</unexpected>"), "application/soap+xml")
    assert not response.success
    assert not response.documents
    assert response.errors == ["Document 1.2.3.4.5 contains unexpected XML content"]


def test_mtom_missing_closing_boundary_rejects_truncated_attachment():
    envelope = retrieve_envelope(
        '<xop:Include xmlns:xop="http://www.w3.org/2004/08/xop/include" href="cid:dicom@document"/>'
    )
    content_type = 'multipart/related; boundary="nir-test"; start="<root@nir>"'
    content = (
        b"--nir-test\r\nContent-Type: application/xop+xml\r\nContent-ID: <root@nir>\r\n\r\n"
        + envelope
        + b"\r\n--nir-test\r\nContent-Type: application/dicom\r\nContent-ID: <dicom@document>\r\n\r\n"
        + b"DICOM_TRUNCATED"
    )
    with pytest.raises(ValueError, match="Invalid MTOM multipart response"):
        parse_retrieve_response(content, content_type)


def test_mtom_invalid_base64_attachment_is_rejected():
    envelope = retrieve_envelope(
        '<xop:Include xmlns:xop="http://www.w3.org/2004/08/xop/include" href="cid:dicom@document"/>'
    )
    content_type = 'multipart/related; boundary="nir-test"; start="<root@nir>"'
    content = (
        b"--nir-test\r\nContent-Type: application/xop+xml\r\nContent-ID: <root@nir>\r\n\r\n"
        + envelope
        + b"\r\n--nir-test\r\nContent-Type: application/dicom\r\nContent-Transfer-Encoding: base64\r\n"
        b"Content-ID: <dicom@document>\r\n\r\n%%%SGVsbG8=\r\n--nir-test--\r\n"
    )
    with pytest.raises(ValueError, match="Invalid MTOM part payload"):
        parse_retrieve_response(content, content_type)


def test_external_entities_are_rejected():
    content = b'<!DOCTYPE x [<!ENTITY data SYSTEM "file:///etc/passwd">]>' + soap("&data;")
    with pytest.raises(EntitiesForbidden):
        parse_iti38_response(content)
