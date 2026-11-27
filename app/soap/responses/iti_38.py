import json
import logging
import uuid

import xmltodict
from fastapi import Request

from app.telemetry import measure, record_cache

from ...audit.audit import AuditFailureException, attempt_audit
from ...audit.models import AuditOutcome, SAMLAttributes
from ...gpconnect import _fetch_gpconnect_record, gpconnect
from ...redis_connect import redis_client
from ..faults import SoapFault
from ..models import (
    XDS_DEFERRED_CREATION_STATUS,
    XDS_ERROR_SEVERITY,
    XDS_FAILURE_STATUS,
    XDS_ON_DEMAND_DOCUMENT_ENTRY,
    AdhocQueryResponse,
    Classification,
    ExternalIdentifier,
    ExtrinsicObject,
    InternationalString,
    ITI38ResponseBody,
    LocalizedString,
    RegistryError,
    RegistryErrorList,
    RegistryObjectList,
    ResponseHeader,
    Slot,
    SoapEnvelope,
)
from .constants import COMMUNITY_ID, REGISTRY_ID


def _failure_response(code_context: str, error_code: str = "XDSRegistryError") -> AdhocQueryResponse:
    return AdhocQueryResponse(
        status=XDS_FAILURE_STATUS,
        registry_error_list=RegistryErrorList(
            highest_severity=XDS_ERROR_SEVERITY,
            error=RegistryError(
                error_code=error_code,
                code_context=code_context,
                location=f"urn:oid:{COMMUNITY_ID}",
                severity=XDS_ERROR_SEVERITY,
            ),
        ),
        registry_object_list={},
    )


async def iti_38_error(queryid: str, error_text: str, error_code: str = "XDSRegistryError") -> str:
    """Generate an ITI-38 registry error response without querying documents."""
    soap_response = SoapEnvelope.create(
        ResponseHeader.create("urn:ihe:iti:2007:CrossGatewayQueryResponse", queryid),
        ITI38ResponseBody(response=_failure_response(error_text, error_code)),
    )
    return xmltodict.unparse(soap_response.to_xml_dict(), pretty=True)


async def iti_38_response(
    request: Request, nhsno: int, ceid, queryid: str, saml_attrs: SAMLAttributes, message_id: str | None = None
):
    """Legacy GP-only SOAP boundary, also used when NIR is disabled."""
    response = await query_gp_documents(request, nhsno, ceid, saml_attrs, message_id)
    soap_response = SoapEnvelope.create(
        ResponseHeader.create("urn:ihe:iti:2007:CrossGatewayQueryResponse", message_id or queryid),
        ITI38ResponseBody(response=response),
    )
    return xmltodict.unparse(soap_response.to_xml_dict(), pretty=True)


async def query_gp_documents(
    request: Request,
    nhsno: int,
    ceid,
    saml_attrs: SAMLAttributes,
    message_id: str | None = None,
    *,
    patient: dict | None = None,
) -> AdhocQueryResponse:
    """Return GP registry metadata without building the outer SOAP response.

    This small extraction keeps the established cache, pipeline and audit code
    in one place. The integrated coordinator can combine this typed result with
    NIR entries before creating one response to the original caller.
    """
    response = AdhocQueryResponse()

    # check the redis cache if there's an existing ccda
    with measure("document.cache.read") as span:
        docid = await redis_client.get(nhsno)
        span.set_attribute("cache.hit", docid is not None)
    record_cache("document", docid is not None)

    cache_hit = docid is not None
    audit_error_code = None
    if docid is None:
        # no cached ccda
        try:
            if patient is None:
                r = await gpconnect(nhsno, saml_attrs, request=request)
            else:
                r = await _fetch_gpconnect_record(nhsno, saml_attrs, request=request, patient=patient)
                # A security denial must stop both sources, not become a
                # partial success containing documents from the other source.
                if r.status_code in (401, 403):
                    raise SoapFault(
                        "GP source denied access", subcode="wsse:FailedAuthentication", message_id=message_id
                    )

            logging.info("no cached ccda, used internal call for patient")
            r = json.loads(r.body)
            if not r.get("success"):
                audit_error_code = "GP_CONNECT_FAILURE"
        except (AuditFailureException, SoapFault):
            raise
        except Exception as e:
            logging.error(f"Error: {e}")
            r = {
                "success": False,
                "error": f"Internal error retrieving structured record for patient. error: {e}",
            }
            audit_error_code = "DOCUMENT_QUERY_FAILURE"
            response = _failure_response("Unable to locate SCR for patient")

        if not r.get("success"):
            # The GP Connect error message is already logged by the request handler.
            logging.warning("GP Connect document query failed")
            error_code = r.get("xds_error_code", "XDSRegistryError")
            error_text = r.get("error", "Unknown error")
            if error_code == "XDSUnknownPatientId":
                error_text = f"Patient ID {nhsno} is not known by this community"
            response = _failure_response(error_text, error_code)
            if not audit_error_code:
                audit_error_code = "DOCUMENT_QUERY_FAILURE"
        else:
            docid = r["document_id"]

    await attempt_audit(
        request=request,
        nhs_number=str(nhsno),
        saml=saml_attrs,
        action="iti38_gp_document_query" if patient is not None else "iti38_document_query",
        outcome=AuditOutcome.ok if docid else AuditOutcome.fail,
        error_code=audit_error_code,
        message_id=message_id,
        detail={"cache_hit": cache_hit},
        document_id=docid,
    )

    if docid is not None:
        # make sure docid is a string and not bytes
        if isinstance(docid, bytes):
            docid = docid.decode("utf-8")

        object_id = docid

        def create_classification(
            classification_scheme: str,
            noderep: str,
            value,
            localized_string: str,
        ) -> Classification:
            return Classification(
                classification_scheme=classification_scheme,
                classified_object=object_id,
                identifier=f"urn:uuid:{uuid.uuid4()}",
                node_representation=noderep,
                object_type=("urn:oasis:names:tc:ebxml-regrep:ObjectType:RegistryObject:Classification"),
                slot=Slot.create("codingScheme", value),
                name=InternationalString(localized_string=LocalizedString(value=localized_string)),
            )

        # This is an on-demand entry: ITI-38 advertises enough metadata to locate
        # it, while ITI-39 returns the document assembled/cached by GP Connect.
        # No hash is advertised because the final content is not stable yet. The
        # legacy size value of "1" is retained for wire compatibility.
        slots = [
            Slot.create(
                "sourcePatientId",
                f"{nhsno}^^^&2.16.840.1.113883.2.1.4.1&ISO",
            ),
            Slot.create(
                "sourcePatientInfo",
                f"PID-3|{nhsno}^^^&2.16.840.1.113883.2.1.4.1&ISO;{ceid}^^^&1.2.840.114350.1.13.525.3.7.3.688884.100&ISO",
            ),
            Slot.create("languageCode", "en-GB"),
            Slot.create("size", "1"),
            Slot.create("repositoryUniqueId", REGISTRY_ID),
        ]
        classifications = [
            create_classification(
                "urn:uuid:41a5887f-8865-4c09-adf7-e362475b143a",
                "34133-9",
                "2.16.840.1.113883.6.1",
                "XDSDocumentEntry.classCode",
            ),
            create_classification(
                "urn:uuid:a09d5840-386c-46f2-b5ad-9c3699a4309d",
                "",
                "urn:hl7-org:sdwg:ccda-structuredBody:1.1",
                "XDSDocumentEntry.formatCode",
            ),
        ]
        external_identifier_type = "urn:oasis:names:tc:ebxml-regrep:ObjectType:RegistryObject:ExternalIdentifier"
        response.registry_object_list = RegistryObjectList(
            extrinsic_object=ExtrinsicObject(
                identifier=object_id,
                # DeferredCreation plus the On-Demand DocumentEntry UUID tells
                # the consumer that retrieval triggers document materialisation.
                status=XDS_DEFERRED_CREATION_STATUS,
                object_type=XDS_ON_DEMAND_DOCUMENT_ENTRY,
                mime_type="text/xml",
                slots=slots,
                classifications=classifications,
                external_identifiers=[
                    ExternalIdentifier(
                        identification_scheme=("urn:uuid:2e82c1f6-a085-4c72-9da3-8640a32e42ab"),
                        value=docid,
                        identifier=docid,
                        registry_object=object_id,
                        object_type=external_identifier_type,
                        name=InternationalString(localized_string=LocalizedString(value="XDSDocumentEntry.uniqueId")),
                    ),
                    ExternalIdentifier(
                        identification_scheme=("urn:uuid:58a6f841-87b3-4a3e-92fd-a8ffeff98427"),
                        value=f"{nhsno}^^^&2.16.840.1.113883.2.1.4.99.1&ISO",
                        identifier=f"PID-{nhsno}",
                        registry_object=object_id,
                        object_type=external_identifier_type,
                        name=InternationalString(localized_string=LocalizedString(value="XDSDocumentEntry.patientId")),
                    ),
                ],
            )
        )

    else:
        response.registry_object_list = {}

    return response
