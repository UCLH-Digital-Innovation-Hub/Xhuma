"""Read NIR metadata and document content without discarding protocol failures.

HTTP status checks belong to the client. These parsers inspect SOAP faults and
XDS registry statuses, then extract retrieval identifiers or document bytes.
"""

from __future__ import annotations

import base64
import binascii
from email import policy
from email.parser import BytesParser
from urllib.parse import unquote
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET

from app.soap.nir_models import (
    XDS_FAILURE_STATUS,
    XDS_SUCCESS_STATUS,
    NIRDocumentReference,
    NIRQueryResponse,
    NIRRetrievedDocument,
    NIRRetrieveResponse,
)
from app.soap.nir_queries import QUERY_NS, RIM_NS, SOAP_NS, XDS_NS

# Registry Services carries statuses/errors, while XOP links XML Document
# elements to MIME attachments. Namespace URIs allow any equivalent XML prefix.
RS_NS = "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0"
XOP_NS = "http://www.w3.org/2004/08/xop/include"
DOCUMENT_UNIQUE_ID_SCHEME = "urn:uuid:2e82c1f6-a085-4c72-9da3-8640a32e42ab"


def _body(content: bytes) -> Element:
    """Parse a SOAP envelope with entity expansion and external entities blocked."""

    # defusedxml protects the parser from entity expansion and external entity
    # access. Require a real SOAP body before interpreting application metadata.
    envelope = ET.fromstring(content)
    namespace = envelope.tag.removesuffix("}Envelope").removeprefix("{")
    if namespace not in (SOAP_NS, "http://schemas.xmlsoap.org/soap/envelope/"):
        raise ValueError("Expected a SOAP envelope")
    body = envelope.find(f"{{{namespace}}}Body")
    if body is None:
        raise ValueError("SOAP envelope is missing its body")
    return body


def _fault(body: Element) -> str | None:
    """Extract the readable reason from a SOAP 1.2 or SOAP 1.1 fault."""

    # A server can put a SOAP Fault in an HTTP 200 response. Read it before
    # looking for a normal registry payload so it cannot become an empty success.
    for child in body:
        if child.tag.rsplit("}", 1)[-1] == "Fault":
            reasons = [
                element.text.strip()
                for element in child.iter()
                if element.tag.rsplit("}", 1)[-1] in ("Text", "faultstring") and element.text
            ]
            return "; ".join(reasons) or "NIR returned a SOAP fault"
    return None


def _status(response: Element) -> tuple[str, list[str]]:
    """Keep the original XDS status and every reported registry error or warning."""

    # Some federated responses report Success alongside warnings for a failed
    # responding gateway. Preserve those messages so the model's success property
    # can distinguish complete results from results with reported gaps.
    errors = [
        ": ".join(value for value in (error.get("errorCode"), error.get("codeContext")) if value)
        or "NIR returned an unspecified registry error"
        for error in response.iter(f"{{{RS_NS}}}RegistryError")
    ]
    status = response.get("status", XDS_FAILURE_STATUS)
    if status != XDS_SUCCESS_STATUS and not errors:
        errors.append(f"NIR registry response status: {status}")
    return status, errors


def _slot_value(document: Element, name: str) -> str:
    """Read the first value of a named metadata slot used for retrieval."""

    for slot in document.findall(f"{{{RIM_NS}}}Slot"):
        if slot.get("name") == name:
            return (slot.findtext(f"{{{RIM_NS}}}ValueList/{{{RIM_NS}}}Value") or "").strip()
    return ""


def parse_iti38_response(content: bytes) -> NIRQueryResponse:
    """Convert LeafClass discovery metadata into references for ITI-39 retrieval."""

    body = _body(content)
    if fault := _fault(body):
        return NIRQueryResponse(status=XDS_FAILURE_STATUS, errors=[fault])
    response = body.find(f"{{{QUERY_NS}}}AdhocQueryResponse")
    if response is None:
        raise ValueError("SOAP response is missing AdhocQueryResponse")
    status, errors = _status(response)
    documents = []
    for document in response.iter(f"{{{RIM_NS}}}ExtrinsicObject"):
        # ExtrinsicObject.id is the registry entry's UUID. Retrieval needs the
        # uniqueId ExternalIdentifier instead; its identificationScheme selects
        # it without relying on display labels or the order of identifiers.
        unique_id = next(
            (
                identifier.get("value", "")
                for identifier in document.findall(f"{{{RIM_NS}}}ExternalIdentifier")
                if identifier.get("identificationScheme") == DOCUMENT_UNIQUE_ID_SCHEME
            ),
            "",
        )
        home = document.get("home", "")
        repository = _slot_value(document, "repositoryUniqueId")
        # Keep valid sibling documents but record missing routing data explicitly.
        # Silently dropping an incomplete entry would misrepresent discovery as
        # complete, and substituting its registry UUID would request the wrong ID.
        if not (home and repository and unique_id):
            errors.append(f"Document {document.get('id', '(unknown)')} is missing retrieval identifiers")
            continue
        documents.append(
            NIRDocumentReference(
                home_community_id=home,
                repository_unique_id=repository,
                document_unique_id=unique_id,
                mime_type=document.get("mimeType"),
            )
        )
    return NIRQueryResponse(status=status, documents=documents, errors=errors)


def _content_id(value: str) -> str:
    """Match MIME Content-ID/start parameters to percent-encoded XOP cid links."""

    # MIME commonly encloses IDs in angle brackets; XOP commonly uses cid: URLs.
    # Normalize their representations only, never the attachment content itself.
    return unquote(value.removeprefix("cid:")).strip().strip("<>")


def _unpack_mtom(content: bytes, content_type: str) -> tuple[bytes, dict[str, bytes]]:
    """Extract the SOAP root and Content-ID-indexed attachments as raw bytes."""

    if "\r" in content_type or "\n" in content_type:
        raise ValueError("Invalid response Content-Type")
    # Supply the HTTP Content-Type as a MIME header so the standard bytes parser
    # can separate boundaries without decoding binary documents into text.
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + content
    )
    # A missing closing boundary can still produce parts in Python's MIME parser.
    # Reject structural defects so a truncated image cannot be returned as valid.
    if not message.is_multipart() or message.defects:
        raise ValueError("Invalid MTOM multipart response")
    parts = list(message.iter_parts())
    root_id = message.get_param("start")
    root_part = None
    attachments = {}
    for part in parts:
        content_id = _content_id(part.get("Content-ID", ""))
        # Decode any MIME transfer encoding, while preserving binary payloads as
        # bytes. Decoding can add defects, such as malformed base64, so check them
        # afterwards rather than accepting a repaired or incomplete attachment.
        payload = part.get_payload(decode=True)
        if payload is None or part.defects:
            raise ValueError("Invalid MTOM part payload")
        if content_id:
            attachments[content_id] = payload
        if root_id and content_id == _content_id(root_id):
            root_part = payload
    # The start parameter selects the SOAP root even when it is not the first
    # part. Only fall back to the first part when the sender omitted start.
    if not root_id and parts:
        root_part = parts[0].get_payload(decode=True)
    if root_part is None:
        raise ValueError("MTOM response is missing its SOAP root part")
    return root_part, attachments


def parse_retrieve_response(content: bytes, content_type: str) -> NIRRetrieveResponse:
    """Parse the shared ITI-39/RAD-75 response, with MTOM or inline base64 content."""

    # MTOM separates SOAP from its binary parts. Plain SOAP can instead embed
    # base64 directly in each Document element; both produce the same byte model.
    if content_type.lower().startswith("multipart/related"):
        envelope, attachments = _unpack_mtom(content, content_type)
    else:
        envelope, attachments = content, {}
    body = _body(envelope)
    if fault := _fault(body):
        return NIRRetrieveResponse(status=XDS_FAILURE_STATUS, errors=[fault])
    response = body.find(f"{{{XDS_NS}}}RetrieveDocumentSetResponse")
    if response is None:
        raise ValueError("SOAP response is missing RetrieveDocumentSetResponse")
    registry_response = response.find(f"{{{RS_NS}}}RegistryResponse")
    if registry_response is None:
        raise ValueError("RetrieveDocumentSetResponse is missing RegistryResponse")
    status, errors = _status(registry_response)
    documents = []
    for document in response.findall(f"{{{XDS_NS}}}DocumentResponse"):
        # Echoed identifiers keep each payload associated with its gateway,
        # repository and requested document, rather than relying on part order.
        home = document.findtext(f"{{{XDS_NS}}}HomeCommunityId", "").strip()
        repository = document.findtext(f"{{{XDS_NS}}}RepositoryUniqueId", "").strip()
        unique_id = document.findtext(f"{{{XDS_NS}}}DocumentUniqueId", "").strip()
        payload = document.find(f"{{{XDS_NS}}}Document")
        # Missing metadata or content must be visible as an error. Retain any
        # successful sibling documents without calling this a complete retrieval.
        if not (home and repository and unique_id) or payload is None:
            errors.append(f"Document {unique_id or '(unknown)'} is missing retrieval identifiers or content")
            continue
        include = payload.find(f"{{{XOP_NS}}}Include")
        # The content is either one XOP Include or base64 text. Unexpected XML
        # children must not accidentally be read as empty successful base64.
        if len(payload) > 1 or (len(payload) == 1 and include is None):
            errors.append(f"Document {unique_id} contains unexpected XML content")
            continue
        if include is not None:
            # Resolve the link by Content-ID, preserving the attachment bytes
            # exactly. A reference to an absent MIME part is a retrieval failure.
            content_id = _content_id(include.get("href", ""))
            if content_id not in attachments:
                errors.append(f"Document {unique_id} refers to a missing MTOM attachment")
                continue
            document_content = attachments[content_id]
        else:
            # Whitespace is permitted around inline base64, but arbitrary text
            # is not. Strict decoding prevents invalid data becoming a payload.
            try:
                document_content = base64.b64decode("".join((payload.text or "").split()), validate=True)
            except (binascii.Error, ValueError):
                errors.append(f"Document {unique_id} contains invalid base64 content")
                continue
        documents.append(
            NIRRetrievedDocument(
                home_community_id=home,
                repository_unique_id=repository,
                document_unique_id=unique_id,
                mime_type=document.findtext(f"{{{XDS_NS}}}mimeType"),
                content=document_content,
            )
        )
    return NIRRetrieveResponse(status=status, documents=documents, errors=errors)
