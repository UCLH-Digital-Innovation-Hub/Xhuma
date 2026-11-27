"""Build the outbound SOAP XML for NIR's three IHE transactions.

The generated envelopes support sandbox calls without WS-Security assertions.
INT and production callers supply their own signed envelope through NIRClient;
wrapping that envelope in MTOM does not change the supplied XML bytes.
"""

from __future__ import annotations

import re
from uuid import uuid4

import xmltodict

from app.soap.models import SoapEnvelope, TextElement, XmlModel
from app.soap.nir_models import NIRDocumentReference, NIRImagingStudyRequest
from app.soap.nir_request_models import (
    AdhocQueryRequest,
    DocumentRequest,
    FindDocumentsQuery,
    ImagingBody,
    OutgoingHeader,
    QueryBody,
    QuerySlot,
    QueryValues,
    RetrievalBody,
    RetrieveDocumentSetRequest,
    RetrieveImagingDocumentSetRequest,
    SeriesRequest,
    StudyRequest,
    TransferSyntaxUIDList,
)

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


def _serialize_request(action: str, endpoint: str, body: XmlModel) -> bytes:
    """Build fresh addressing and serialize once, before any signature is added."""
    header = OutgoingHeader(
        action=TextElement(must_understand=1, text=action),
        destination=TextElement(must_understand=1, text=endpoint),
    )
    envelope = SoapEnvelope.create(header, body)
    return xmltodict.unparse(envelope.to_xml_dict(), pretty=False).encode("utf-8")


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
    # LeafClass includes full metadata; Approved excludes deprecated entries.
    # An untargeted FindDocuments query leaves the home community unset.
    # xmltodict escapes the ampersands in the NHS assigning-authority CX value.
    body = QueryBody(
        request=AdhocQueryRequest(
            query=FindDocumentsQuery(
                slots=[
                    QuerySlot(
                        name="$XDSDocumentEntryStatus",
                        values=QueryValues(values=["('urn:oasis:names:tc:ebxml-regrep:StatusType:Approved')"]),
                    ),
                    QuerySlot(
                        name="$XDSDocumentEntryPatientId",
                        values=QueryValues(values=[f"'{nhs_number}^^^&{NHS_NUMBER_ROOT}&ISO'"]),
                    ),
                ]
            )
        )
    )
    return _serialize_request(ITI38_ACTION, endpoint, body)


def build_iti39_request(documents: list[NIRDocumentReference], endpoint: str) -> bytes:
    """Build document retrieval XML from the references returned by ITI-38."""

    if not documents:
        raise ValueError("At least one document is required")
    # Read just the model's three address fields. A reference may be a retrieved
    # document subclass; its MIME type and binary content do not belong here.
    body = RetrievalBody(
        request=RetrieveDocumentSetRequest(
            documents=[DocumentRequest.model_validate(document, from_attributes=True) for document in documents]
        )
    )
    return _serialize_request(ITI39_ACTION, endpoint, body)


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
    # Preserve study -> series -> object hierarchy and each complete address.
    body = ImagingBody(
        request=RetrieveImagingDocumentSetRequest(
            studies=[
                StudyRequest(
                    study_instance_uid=study.study_instance_uid,
                    series=[
                        SeriesRequest(
                            series_instance_uid=series.series_instance_uid,
                            documents=[
                                DocumentRequest.model_validate(document, from_attributes=True)
                                for document in series.documents
                            ],
                        )
                        for series in study.series
                    ],
                )
                for study in studies
            ],
            transfer_syntaxes=TransferSyntaxUIDList(uids=syntax_uids),
        )
    )
    return _serialize_request(RAD75_ACTION, endpoint, body)


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
