"""Opt-in ITI-38 discovery and ITI-39 retrieval through GP Connect and NIR.

The route supplies deployment config explicitly. Request context is constructed
here from verified incoming SAML, before any cache or provider access. Keeping
the two source calls here makes their ordering, failure policy and audit boundary
visible without introducing a provider framework.
"""

import asyncio
import base64
import hashlib
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4
from xml.etree.ElementTree import ParseError

import httpx
import xmltodict
from defusedxml.common import DefusedXmlException
from fastapi import Request, Response
from lxml import etree as ET

from app.audit.audit import attempt_audit
from app.audit.models import AuditOutcome
from app.gpconnect_stages.patient import validate_patient
from app.gpconnect_stages.types import DiagnosticStep
from app.nir.client import NIRClient
from app.nir.config import NIRDiscoveryConfig
from app.nir.security import NS, DiscoveryContext, parse_xml, sign_outgoing, verify_incoming
from app.redis_connect import redis_client

from .faults import SoapFault
from .models import (
    XDS_ERROR_SEVERITY,
    XDS_FAILURE_STATUS,
    XDS_SUCCESS_STATUS,
    AdhocQueryResponse,
    ITI38ResponseBody,
    ITI39DocumentResponse,
    ITI39RegistryErrorList,
    ITI39RegistryResponse,
    ITI39Request,
    ITI39ResponseBody,
    RegistryError,
    RegistryErrorList,
    RegistryObjectList,
    ResponseHeader,
    RetrieveDocumentSetResponse,
    SoapEnvelope,
    TextElement,
    XmlModel,
    XOPDocument,
    XOPInclude,
)
from .nir_models import NIRDocumentReference, NIRRetrievedDocument
from .nir_queries import (
    ITI38_ACTION,
    ITI39_ACTION,
    NHS_NUMBER_ROOT,
    RIM_NS,
    XDS_NS,
    build_iti38_request,
    build_iti39_request,
)
from .nir_responses import DOCUMENT_UNIQUE_ID_SCHEME, RS_NS, _unpack_mtom, parse_retrieve_response
from .responses.constants import COMMUNITY_ID, REGISTRY_ID
from .responses.iti_38 import query_gp_documents
from .responses.iti_39 import deliver_iti39_response

PARTIAL = "urn:ihe:iti:2007:ResponseStatusType:PartialSuccess"


@dataclass
class SourceResult:
    """Full registry XML, not just the reduced IDs needed by the NIR client."""

    status: str
    entries: list[ET._Element] = field(default_factory=list)
    errors: list[ET._Element] = field(default_factory=list)
    references: list[NIRDocumentReference] = field(default_factory=list)


def registry_error(message: str, location: str, code: str = "XDSRegistryError") -> ET._Element:
    error = RegistryError(error_code=code, code_context=message, location=location, severity=XDS_ERROR_SEVERITY)
    # Source results retain upstream errors as XML. Use the same representation
    # for locally generated errors, without parsing/reducing external metadata.
    return parse_xml(
        xmltodict.unparse({"rs:RegistryError": {"@xmlns:rs": RS_NS, **error.to_xml_dict()}}, pretty=False).encode(
            "utf-8"
        )
    )


def response_envelope(action: str, message_id: str, body: XmlModel) -> ET._Element:
    """Serialize our typed response; external XML can then be attached intact."""
    envelope = SoapEnvelope.create(
        ResponseHeader(action=TextElement(text=action), relates_to=TextElement(text=message_id)),
        body,
    )
    return parse_xml(xmltodict.unparse(envelope.to_xml_dict(), pretty=False).encode("utf-8"))


def check_nir_security(response: httpx.Response, context: DiscoveryContext) -> None:
    """Security rejection stops the whole request, regardless of HTTP status.

    A WS-Security Fault can arrive with HTTP 200, 400 or 500, and retrieval can
    wrap its SOAP root in MIME. Other malformed responses are handled by the
    source parser as availability/protocol failures, not as empty successes.
    """
    if response.status_code in (401, 403):
        raise SoapFault("NIR denied access", subcode="wsse:FailedAuthentication", message_id=context.message_id)
    try:
        content = response.content
        if response.headers.get("content-type", "").lower().startswith("multipart/"):
            content, _ = _unpack_mtom(content, response.headers["content-type"])
        root = parse_xml(content)
    except (ValueError, ET.XMLSyntaxError):
        return
    codes = root.xpath("s:Body/s:Fault/s:Code//s:Value/text()", namespaces=NS)
    if any("Security" in code or "Authentication" in code or "Token" in code for code in codes):
        raise SoapFault(
            "NIR rejected the security context", subcode="wsse:FailedAuthentication", message_id=context.message_id
        )


async def check_patient_access(request: Request, context: DiscoveryContext) -> dict:
    """A cache hit never bypasses patient restrictions or replay protection.

    The replay key is scoped to issuer and MessageID, allowing the same
    still-valid assertion on distinct transactions, as the Q23 journey does.
    Redis errors propagate: lack of replay/access state cannot become success.
    """
    replay = hashlib.sha256(
        (context.assertion.findtext("saml:Issuer", namespaces=NS) + "\0" + context.message_id).encode()
    ).hexdigest()
    ttl = max(1, math.ceil((context.expires_at - datetime.now(UTC)).total_seconds()))
    accepted = await redis_client.set("nir:message:" + replay, "1", nx=True, ex=ttl)
    if not accepted:
        raise SoapFault(
            "MessageID has already been used", subcode="wsse:FailedAuthentication", message_id=context.message_id
        )
    patient = await validate_patient(
        context.nhs_number,
        context.saml,
        request=request,
        log_dir=None,
        step=DiagnosticStep("validate_nhs_number.validate"),
    )
    if isinstance(patient, Response):
        # Shared preflight failures stop BOTH sources, including cached GP data.
        # Its own stages have already recorded their audit outcomes.
        raise SoapFault(
            "Patient access could not be established",
            code="Sender" if patient.status_code < 500 else "Receiver",
            message_id=context.message_id,
        )
    return patient


def retrieval_key(context: DiscoveryContext, document: NIRDocumentReference) -> str:
    """Bind an advertised address to the verified caller and patient, with TTL.

    Hash the structured tuple so identifiers do not appear in Redis key names.
    The cached value is routing state only; every retrieval still runs preflight.
    """
    identity = [
        context.assertion.findtext("saml:Subject/saml:NameID", namespaces=NS),
        context.saml.organization_id,
        context.nhs_number,
    ]
    address = [document.home_community_id, document.repository_unique_id, document.document_unique_id]
    return "nir:document:" + hashlib.sha256(json.dumps(identity + address).encode()).hexdigest()


async def query_gp(context: DiscoveryContext, request: Request, patient: dict, timeout: float) -> SourceResult:
    try:
        async with asyncio.timeout(timeout):
            result = await query_gp_documents(
                request, context.nhs_number, "NOCEID", context.saml, context.message_id, patient=patient
            )
    except TimeoutError:
        return SourceResult(
            XDS_FAILURE_STATUS, errors=[registry_error("GP source timed out", f"urn:oid:{COMMUNITY_ID}")]
        )
    # The existing GP metadata model stays in use. Convert only its completed
    # registry result to namespace-aware XML at this combination boundary.
    root = parse_xml(xmltodict.unparse({"AdhocQueryResponse": result.to_xml_dict()}).encode())
    registry = root.find(f"{{{RIM_NS}}}RegistryObjectList")
    entries = list(registry) if registry is not None else []
    for entry in entries:
        entry.set("home", f"urn:oid:{COMMUNITY_ID}")
    return SourceResult(result.status, entries, list(root.iter(f"{{{RS_NS}}}RegistryError")))


async def query_nir(context: DiscoveryContext, config: NIRDiscoveryConfig, query: ET._Element) -> SourceResult:
    try:
        async with asyncio.timeout(config.client.timeout):
            async with NIRClient(config.client) as client:
                envelope = parse_xml(build_iti38_request(context.nhs_number, client.endpoint("iti38")))
                body = envelope.find("s:Body", NS)
                body.remove(body[0])
                body.append(deepcopy(query))
                signed = sign_outgoing(ET.tostring(envelope), context, config)
                raw = await client.send_soap("iti38", signed)
                check_nir_security(raw, context)
                root = parse_xml(raw.content)
        if root.find("s:Body/s:Fault", NS) is not None:
            raise ValueError("NIR SOAP fault")
        result = root.find("s:Body/query:AdhocQueryResponse", NS)
        if result is None or result.get("status") not in {XDS_SUCCESS_STATUS, XDS_FAILURE_STATUS, PARTIAL}:
            raise ValueError("Invalid NIR registry response")
        registry = result.find(f"{{{RIM_NS}}}RegistryObjectList")
        entries = list(registry) if registry is not None else []
        errors = list(result.iter(f"{{{RS_NS}}}RegistryError"))
        entry_ids = [node.get("id") for entry in entries for node in entry.iter() if node.get("id")]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("Duplicated NIR registry IDs")
        references = []
        for entry in entries:
            if entry.tag != f"{{{RIM_NS}}}ExtrinsicObject":
                continue  # Preserve associations/packages as full registry XML.
            unique = entry.xpath(
                "rim:ExternalIdentifier[@identificationScheme=$scheme]/@value",
                namespaces=NS,
                scheme=DOCUMENT_UNIQUE_ID_SCHEME,
            )
            repositories = entry.xpath(
                "rim:Slot[@name='repositoryUniqueId']/rim:ValueList/rim:Value/text()", namespaces=NS
            )
            if len(unique) != 1 or len(repositories) != 1:
                raise ValueError("Ambiguous NIR retrieval address")
            if entry.get("home") == f"urn:oid:{COMMUNITY_ID}" and repositories[0] == REGISTRY_ID:
                # Remote metadata must never advertise our local cache address:
                # that would make the later retrieval choose the wrong source.
                raise ValueError("NIR advertised Xhuma's local repository")
            references.append(
                NIRDocumentReference(
                    home_community_id=entry.get("home", ""),
                    repository_unique_id=repositories[0],
                    document_unique_id=unique[0],
                    mime_type=entry.get("mimeType"),
                )
            )
        if result.get("status") == XDS_FAILURE_STATUS:
            entries, references = [], []  # Failed source data must not be released.
        if result.get("status") != XDS_SUCCESS_STATUS and not errors:
            errors.append(registry_error("NIR returned incomplete results", config.client.base_url))
        return SourceResult(result.get("status"), entries, errors, references)
    except httpx.HTTPStatusError as exc:
        check_nir_security(exc.response, context)
        return SourceResult(
            XDS_FAILURE_STATUS, errors=[registry_error("NIR HTTP request failed", config.client.base_url)]
        )
    except (httpx.RequestError, TimeoutError, ValueError, ET.XMLSyntaxError):
        return SourceResult(
            XDS_FAILURE_STATUS, errors=[registry_error("NIR discovery unavailable or invalid", config.client.base_url)]
        )


async def discover_documents(request: Request, content: bytes, config: NIRDiscoveryConfig) -> Response:
    context = verify_incoming(content, config, ITI38_ACTION)
    # Start with the existing, explicitly supported FindDocuments contract.
    # Reject unsupported filters instead of silently broadening the GP query.
    # The accepted query is copied intact to NIR; only addressing/security change.
    bodies = context.envelope.find("s:Body", NS)
    query = bodies.find("query:AdhocQueryRequest", NS)
    if len(bodies) != 1 or query is None:
        raise SoapFault("Expected one AdhocQueryRequest", message_id=context.message_id)
    adhoc = query.find("rim:AdhocQuery", NS)
    option = query.find("query:ResponseOption", NS)
    if (
        len(query) != 2
        or len(query.findall("rim:AdhocQuery", NS)) != 1
        or len(query.findall("query:ResponseOption", NS)) != 1
        or adhoc is None
        or adhoc.get("id") != "urn:uuid:14d4debf-8f97-4251-9a74-a90016b0af0d"
        or option is None
        or option.get("returnType") != "LeafClass"
        or adhoc.get("home")
    ):
        raise SoapFault(
            "NIR integration supports untargeted FindDocuments with LeafClass", message_id=context.message_id
        )
    slots = adhoc.findall("rim:Slot", NS)
    values = {slot.get("name"): slot.xpath("rim:ValueList/rim:Value/text()", namespaces=NS) for slot in slots}
    expected_patient = f"'{context.nhs_number}^^^&{NHS_NUMBER_ROOT}&ISO'"
    if (
        len(adhoc) != len(slots)
        or len(values) != len(slots)
        or set(values) != {"$XDSDocumentEntryPatientId", "$XDSDocumentEntryStatus"}
        or values["$XDSDocumentEntryPatientId"] != [expected_patient]
        or values["$XDSDocumentEntryStatus"] != ["('urn:oasis:names:tc:ebxml-regrep:StatusType:Approved')"]
    ):
        raise SoapFault(
            "Unsupported query filters or patient does not match signed resource-id", message_id=context.message_id
        )
    patient = await check_patient_access(request, context)

    # Both branches start after shared access checks, including on GP cache hits.
    # gather propagates the original exception (especially audit/security faults).
    # Always cancel AND await siblings so failed requests leave no detached work.
    tasks = [
        asyncio.create_task(query_gp(context, request, patient, config.client.timeout)),
        asyncio.create_task(query_nir(context, config, query)),
    ]
    try:
        gp, nir = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    errors = gp.errors + nir.errors
    status = (
        XDS_SUCCESS_STATUS
        if not errors
        else (XDS_FAILURE_STATUS if gp.status == nir.status == XDS_FAILURE_STATUS else PARTIAL)
    )
    response = AdhocQueryResponse(
        status=status,
        registry_error_list=RegistryErrorList(highest_severity=XDS_ERROR_SEVERITY, error=[]) if errors else None,
        registry_object_list=RegistryObjectList(),
    )
    root = response_envelope(ITI38_ACTION + "Response", context.message_id, ITI38ResponseBody(response=response))
    # The model builds our envelope and registry containers. Attach full source
    # XML afterwards so unfamiliar metadata, namespaces and ordering survive.
    if errors:
        root.find(f".//{{{RS_NS}}}RegistryErrorList").extend(deepcopy(errors))
    registry = root.find(f".//{{{RIM_NS}}}RegistryObjectList")
    # Registry XML IDs identify metadata nodes, not retrieved documents. If a
    # remote node collides with a local one, rename its XML ID AND references
    # together. The home/repository/document unique IDs remain untouched.
    gp_entries, nir_entries = deepcopy(gp.entries), deepcopy(nir.entries)
    local_ids = {node.get("id") for entry in gp_entries for node in entry.iter() if node.get("id")}
    remap = {
        node.get("id"): "urn:uuid:" + str(uuid4())
        for entry in nir_entries
        for node in entry.iter()
        if node.get("id") in local_ids
    }
    for entry in nir_entries:
        for node in entry.iter():
            for name in ("id", "classifiedObject", "registryObject", "sourceObject", "targetObject"):
                if node.get(name) in remap:
                    node.set(name, remap[node.get(name)])
    registry.extend(gp_entries + nir_entries)

    # Persist only references we actually advertise. Losing Redis is a request
    # failure: returning entries that cannot subsequently be routed is misleading.
    # No assertion/token or private key is persisted with these addresses.
    if nir.references:
        async with redis_client.pipeline(transaction=True) as pipe:
            for reference in nir.references:
                pipe.set(retrieval_key(context, reference), context.nhs_number, ex=config.retrieval_ttl)
            await pipe.execute()
    await attempt_audit(
        request=request,
        nhs_number=context.nhs_number,
        saml=context.saml,
        action="iti38_document_query",
        outcome=AuditOutcome.ok if status == XDS_SUCCESS_STATUS else AuditOutcome.fail,
        error_code=None if status == XDS_SUCCESS_STATUS else "DISCOVERY_INCOMPLETE",
        message_id=context.message_id,
        detail={"gp_status": gp.status, "nir_status": nir.status, "nir_documents": len(nir.references)},
    )
    return Response(ET.tostring(root, xml_declaration=True, encoding="UTF-8"), media_type="application/soap+xml")


async def retrieve_documents(request: Request, content: bytes, config: NIRDiscoveryConfig) -> Response:
    """Route every requested address, preserving binary data in an MTOM reply.

    A fresh incoming assertion is required for this request. We never reuse the
    discovery request's token; its short-lived routing record is not authority.
    """
    if request.headers.get("content-type", "").lower().startswith("multipart/"):
        try:
            content, _ = _unpack_mtom(content, request.headers["content-type"])
        except ValueError:
            raise SoapFault("Malformed MTOM request") from None
    context = verify_incoming(content, config, ITI39_ACTION)
    body = context.envelope.find("s:Body", NS)
    retrieval = body.find(f"{{{XDS_NS}}}RetrieveDocumentSetRequest")
    if len(body) != 1 or retrieval is None:
        raise SoapFault("Expected one RetrieveDocumentSetRequest", message_id=context.message_id)
    documents = []
    for item in retrieval:
        if item.tag != f"{{{XDS_NS}}}DocumentRequest":
            raise SoapFault("Unsupported retrieval element", message_id=context.message_id)
        address = {}
        for field_name, tag in (
            ("home_community_id", "HomeCommunityId"),
            ("repository_unique_id", "RepositoryUniqueId"),
            ("document_unique_id", "DocumentUniqueId"),
        ):
            nodes = item.findall(f"{{{XDS_NS}}}{tag}")
            if len(nodes) != 1 or not (nodes[0].text or "").strip():
                raise SoapFault("Each retrieval requires one complete document address", message_id=context.message_id)
            address[field_name] = nodes[0].text
        documents.append(NIRDocumentReference(**address))
    if not 1 <= len(documents) <= 100 or len(
        {(d.home_community_id, d.repository_unique_id, d.document_unique_id) for d in documents}
    ) != len(documents):
        raise SoapFault("Request 1 to 100 distinct document addresses", message_id=context.message_id)
    await check_patient_access(request, context)

    local, external = [], []
    for document in documents:
        if document.home_community_id == f"urn:oid:{COMMUNITY_ID}" and document.repository_unique_id == REGISTRY_ID:
            local.append(document)
        else:
            associated = await redis_client.get(retrieval_key(context, document))
            if isinstance(associated, bytes):
                associated = associated.decode()
            if associated != context.nhs_number:
                raise SoapFault(
                    "External document was not discovered for this caller and patient, or discovery expired",
                    subcode="wsse:FailedAuthentication",
                    message_id=context.message_id,
                )
            external.append(document)

    results, errors = [], []
    for document in local:
        stored, patient = await redis_client.hmget(f"document:{document.document_unique_id}", "content", "nhs_number")
        if isinstance(patient, bytes):
            patient = patient.decode()
        if stored is not None and patient != context.nhs_number:
            raise SoapFault(
                "Document patient does not match signed resource-id",
                subcode="wsse:FailedAuthentication",
                message_id=context.message_id,
            )
        if stored is None:
            errors.append(
                registry_error("GP document is unavailable", document.home_community_id, "XDSDocumentUniqueIdError")
            )
        else:
            # GP's existing cache stores base64 XML. Decode it once to obtain
            # the exact bytes for an XOP attachment; never decode PDF/DICOM text.
            results.append(
                NIRRetrievedDocument(
                    **document.model_dump(exclude={"mime_type"}),
                    mime_type="text/xml",
                    content=base64.b64decode(stored, validate=True),
                )
            )

    # Group by home community and bound the whole outbound retrieval, not just
    # individual socket reads. Re-sign each envelope immediately before sending.
    groups = {}
    for document in external:
        groups.setdefault(document.home_community_id, []).append(document)
    deadline = asyncio.get_running_loop().time() + config.client.timeout
    if groups:
        async with NIRClient(config.client) as client:
            for home, group in groups.items():
                try:
                    async with asyncio.timeout_at(deadline):
                        envelope = sign_outgoing(build_iti39_request(group, client.endpoint("iti39")), context, config)
                        raw = await client.send_soap("iti39", envelope)
                        check_nir_security(raw, context)
                        response = parse_retrieve_response(raw.content, raw.headers.get("content-type", ""))
                    expected = {(d.home_community_id, d.repository_unique_id, d.document_unique_id) for d in group}
                    received = [
                        (d.home_community_id, d.repository_unique_id, d.document_unique_id) for d in response.documents
                    ]
                    if not set(received) <= expected or len(received) != len(set(received)):
                        raise ValueError("Unexpected or duplicated retrieved document")
                    if response.status not in {XDS_SUCCESS_STATUS, PARTIAL}:
                        raise ValueError("NIR retrieval failed")
                    results.extend(response.documents)
                    if set(received) != expected or response.errors or not response.success:
                        errors.append(registry_error("NIR retrieval incomplete", home, "XDSRepositoryError"))
                except httpx.HTTPStatusError as exc:
                    check_nir_security(exc.response, context)
                    errors.append(registry_error("NIR retrieval HTTP failure", home, "XDSRepositoryError"))
                except (httpx.RequestError, TimeoutError, ValueError, ParseError, DefusedXmlException):
                    errors.append(registry_error("NIR retrieval unavailable or invalid", home, "XDSRepositoryError"))

    status = XDS_SUCCESS_STATUS if not errors else (PARTIAL if results else XDS_FAILURE_STATUS)
    document_responses, attachments = [], []
    for document in results:
        mime = document.mime_type or "application/octet-stream"
        if not re.fullmatch(r"[\w.+-]+/[\w.+-]+", mime):
            raise SoapFault("Invalid upstream document MIME type", code="Receiver", message_id=context.message_id)
        cid = str(uuid4())
        document_responses.append(
            ITI39DocumentResponse(
                home_community_id=TextElement(text=document.home_community_id),
                repository_unique_id=TextElement(text=document.repository_unique_id),
                document_unique_id=TextElement(text=document.document_unique_id),
                mime_type=TextElement(text=mime),
                document=XOPDocument(include=XOPInclude(href="cid:" + cid)),
            )
        )
        attachments.append((cid, mime, document.content))
    response = RetrieveDocumentSetResponse(
        xmlns_rs=RS_NS,
        registry_response=ITI39RegistryResponse(
            status=status,
            registry_error_list=ITI39RegistryErrorList(highest_severity=XDS_ERROR_SEVERITY, error=[])
            if errors
            else None,
        ),
        document_response=document_responses,
    )
    root = response_envelope(ITI39_ACTION + "Response", context.message_id, ITI39ResponseBody(response=response))
    if errors:
        root.find(f".//{{{RS_NS}}}RegistryErrorList").extend(deepcopy(errors))
    boundary, root_id = "nir-" + uuid4().hex, str(uuid4())
    content_type = f'multipart/related; boundary="{boundary}"; type="application/xop+xml"; start="<{root_id}>"; start-info="application/soap+xml"'
    parts = [
        (
            root_id,
            'application/xop+xml; charset=utf-8; type="application/soap+xml"',
            ET.tostring(root, xml_declaration=True, encoding="UTF-8"),
        )
    ] + attachments
    # Only ASCII MIME framing is constructed here. Appending the document bytes
    # directly preserves arbitrary binary content and avoids text transcoding.
    payload = b"".join(
        f"--{boundary}\r\nContent-Type: {mime}\r\nContent-Transfer-Encoding: binary\r\nContent-ID: <{cid}>\r\n\r\n".encode(
            "ascii"
        )
        + data
        + b"\r\n"
        for cid, mime, data in parts
    ) + f"--{boundary}--\r\n".encode("ascii")
    # Reuse the established audited synchronous/asynchronous delivery boundary.
    # Its ReplyTo allowlist applies to the combined response too.
    from app.ccda.helpers import clean_soap

    soap_request = ITI39Request.model_validate(clean_soap(content))
    return await deliver_iti39_response(
        request,
        soap_request,
        context.saml,
        None,
        context.nhs_number,
        payload,
        outcome=AuditOutcome.ok if not errors else AuditOutcome.fail,
        error_code="RETRIEVAL_INCOMPLETE" if errors else None,
        detail={"documents_requested": len(documents), "documents_returned": len(results), "status": status},
        content_type=content_type,
    )
