"""Build the outbound SOAP XML for NIR's three IHE transactions.

The generated envelopes support sandbox calls without WS-Security assertions.
INT and production callers supply their own signed envelope through NIRClient;
wrapping that envelope in MTOM does not change the supplied XML bytes.
"""

from __future__ import annotations

import re
from uuid import uuid4
from xml.etree import ElementTree as ET

from app.soap.nir_models import NIRDocumentReference, NIRImagingStudyRequest

# SOAP carries the message and WS-Addressing identifies the transaction and its
# destination. The registry query/RIM namespaces describe discovery metadata;
# XDS describes document retrieval, and XDS-I adds the imaging study hierarchy.
SOAP_NS = "http://www.w3.org/2003/05/soap-envelope"
WSA_NS = "http://www.w3.org/2005/08/addressing"
QUERY_NS = "urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0"
RIM_NS = "urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0"
XDS_NS = "urn:ihe:iti:xds-b:2007"
XDSI_NS = "urn:ihe:rad:xdsi-b:2009"
NHS_NUMBER_ROOT = "2.16.840.1.113883.2.1.4.1"
ITI38_ACTION = "urn:ihe:iti:2007:CrossGatewayQuery"
ITI39_ACTION = "urn:ihe:iti:2007:CrossGatewayRetrieve"
RAD75_ACTION = "urn:ihe:rad:2011:CrossGatewayRetrieveImagingDocumentSet"

# Prefixes make generated XML easy to compare with the NHS examples. The
# namespace URI, rather than the chosen prefix, determines each element's meaning.
for prefix, namespace in (
    ("s", SOAP_NS),
    ("a", WSA_NS),
    ("query", QUERY_NS),
    ("rim", RIM_NS),
    ("xds", XDS_NS),
    ("xdsi", XDSI_NS),
):
    ET.register_namespace(prefix, namespace)


def _element(parent: ET.Element, namespace: str, local_name: str, text: str | None = None, **attributes) -> ET.Element:
    """Add a namespaced element; ElementTree escapes its text and attributes."""

    element = ET.SubElement(parent, f"{{{namespace}}}{local_name}", attributes)
    element.text = text
    return element


def _envelope(action: str, endpoint: str) -> tuple[ET.Element, ET.Element]:
    """Create the common SOAP header and return the body ready for its payload."""

    envelope = ET.Element(f"{{{SOAP_NS}}}Envelope")
    header = _element(envelope, SOAP_NS, "Header")
    # Action selects ITI-38, ITI-39 or RAD-75; To names the actual NIR endpoint.
    # Each HTTP request gets a fresh MessageID for response correlation. This is
    # separate from the fixed stored-query ID used to select FindDocuments below.
    _element(header, WSA_NS, "Action", action, **{f"{{{SOAP_NS}}}mustUnderstand": "true"})
    _element(header, WSA_NS, "MessageID", f"urn:uuid:{uuid4()}")
    # The anonymous ReplyTo requests a response on this connection, rather than
    # asking NIR to call an application callback URL.
    reply_to = _element(header, WSA_NS, "ReplyTo")
    _element(reply_to, WSA_NS, "Address", f"{WSA_NS}/anonymous")
    _element(header, WSA_NS, "To", endpoint, **{f"{{{SOAP_NS}}}mustUnderstand": "true"})
    return envelope, _element(envelope, SOAP_NS, "Body")


def _serialize(envelope: ET.Element) -> bytes:
    """Serialize generated XML as UTF-8 bytes with a matching XML declaration."""

    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


def _document_request(parent: ET.Element, document: NIRDocumentReference) -> None:
    """Write the common routing identifiers used by ITI-39 and RAD-75."""

    request = _element(parent, XDS_NS, "DocumentRequest")
    _element(request, XDS_NS, "HomeCommunityId", document.home_community_id)
    _element(request, XDS_NS, "RepositoryUniqueId", document.repository_unique_id)
    _element(request, XDS_NS, "DocumentUniqueId", document.document_unique_id)


def build_iti38_request(nhs_number: str, endpoint: str) -> bytes:
    """Find approved document entries for one NHS number across NIR communities."""

    # Validate locally before sending a patient query. NHS numbers use ten ASCII
    # digits; the last checks the first nine with descending weights and modulus
    # 11. A calculated 11 becomes zero, while 10 cannot be a valid check digit.
    if not re.fullmatch(r"[0-9]{10}", nhs_number):
        raise ValueError("NHS number must contain exactly 10 digits")
    weighted_sum = sum(int(digit) * (10 - index) for index, digit in enumerate(nhs_number[:9]))
    check_digit = 11 - weighted_sum % 11
    if check_digit == 11:
        check_digit = 0
    if check_digit != int(nhs_number[-1]):
        raise ValueError("NHS number has an invalid check digit")
    envelope, body = _envelope(ITI38_ACTION, endpoint)
    request = _element(body, QUERY_NS, "AdhocQueryRequest")
    # LeafClass returns the full metadata needed for retrieval. ObjectRef would
    # only return registry references. Approved excludes deprecated entries.
    _element(request, QUERY_NS, "ResponseOption", returnType="LeafClass", returnComposedObjects="true")
    # This UUID selects the standard FindDocuments stored query and stays fixed
    # between requests. Omit its home attribute for discovery across communities,
    # matching the NHS sandbox example rather than restricting to one gateway.
    query = _element(request, RIM_NS, "AdhocQuery", id="urn:uuid:14d4debf-8f97-4251-9a74-a90016b0af0d")
    # The patient slot uses the HL7 CX form: identifier plus assigning-authority
    # OID and ISO type. Quotes belong to the stored-query value; ElementTree
    # handles the ampersand escaping when it serializes this text into XML.
    for name, value in (
        ("$XDSDocumentEntryStatus", "('urn:oasis:names:tc:ebxml-regrep:StatusType:Approved')"),
        ("$XDSDocumentEntryPatientId", f"'{nhs_number}^^^&{NHS_NUMBER_ROOT}&ISO'"),
    ):
        slot = _element(query, RIM_NS, "Slot", name=name)
        values = _element(slot, RIM_NS, "ValueList")
        _element(values, RIM_NS, "Value", value)
    return _serialize(envelope)


def build_iti39_request(documents: list[NIRDocumentReference], endpoint: str) -> bytes:
    """Build document retrieval XML from the references returned by ITI-38."""

    if not documents:
        raise ValueError("At least one document is required")
    envelope, body = _envelope(ITI39_ACTION, endpoint)
    request = _element(body, XDS_NS, "RetrieveDocumentSetRequest")
    for document in documents:
        _document_request(request, document)
    return _serialize(envelope)


def build_rad75_request(
    studies: list[NIRImagingStudyRequest], endpoint: str, transfer_syntax_uids: list[str] | None = None
) -> bytes:
    """Build image retrieval XML using study, series and SOP Instance UIDs.

    These image identifiers normally come from a retrieved DICOM manifest. An
    ITI-38 reference to the manifest itself does not identify every image in it.
    """

    if not studies:
        raise ValueError("At least one imaging study is required")
    # Explicit VR Little Endian is the default DICOM transfer syntax in the NHS
    # example. Callers can provide a list of other acceptable encoding UIDs.
    syntax_uids = ["1.2.840.10008.1.2.1"] if transfer_syntax_uids is None else transfer_syntax_uids
    if not syntax_uids or any(not uid for uid in syntax_uids):
        raise ValueError("At least one transfer syntax UID is required")
    envelope, body = _envelope(RAD75_ACTION, endpoint)
    request = _element(body, XDSI_NS, "RetrieveImagingDocumentSetRequest")
    # Preserve the study -> series -> object hierarchy. Each DocumentUniqueId
    # names one DICOM SOP instance, with its gateway and repository identifiers.
    for study in studies:
        study_request = _element(request, XDSI_NS, "StudyRequest", studyInstanceUID=study.study_instance_uid)
        for series in study.series:
            series_request = _element(
                study_request, XDSI_NS, "SeriesRequest", seriesInstanceUID=series.series_instance_uid
            )
            for document in series.documents:
                _document_request(series_request, document)
    syntax_list = _element(request, XDSI_NS, "TransferSyntaxUIDList")
    for uid in syntax_uids:
        _element(syntax_list, XDSI_NS, "TransferSyntaxUID", uid)
    return _serialize(envelope)


def wrap_mtom_request(envelope: bytes, action: str) -> tuple[bytes, str]:
    """Return MTOM request bytes and their matching HTTP Content-Type header.

    ITI-39 and RAD-75 use multipart/related even though these request envelopes have
    no document attachments. The response can then carry binary documents as parts.
    """

    # Action is inserted into a quoted MIME parameter. Reject header delimiters
    # and quote characters before constructing that header.
    if any(character in action for character in ("\r", "\n", '"', "\\")):
        raise ValueError("Invalid SOAP action")
    boundary = f"uuid:{uuid4()}"
    content_id = f"root.{uuid4()}@nir"
    # MIME headers and delimiters use ASCII and CRLF. Append the envelope as its
    # original bytes so wrapping a caller's signed XML cannot alter its content.
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Type: application/xop+xml; charset=UTF-8; type="application/soap+xml"\r\n'
            "Content-Transfer-Encoding: binary\r\n"
            f"Content-ID: <{content_id}>\r\n\r\n"
        ).encode("ascii")
        + envelope
        + f"\r\n--{boundary}--\r\n".encode("ascii")
    )
    # Boundary matches the body delimiters, and start identifies its SOAP root
    # Content-ID. start-info identifies SOAP 1.2 and includes the transaction's
    # action; nested quotes must be escaped inside this HTTP header parameter.
    content_type = (
        'multipart/related; type="application/xop+xml"; '
        f'boundary="{boundary}"; start="<{content_id}>"; '
        f'start-info="application/soap+xml; action=\\"{action}\\""'
    )
    return body, content_type
