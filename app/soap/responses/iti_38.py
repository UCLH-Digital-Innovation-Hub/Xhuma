import json
import logging
import uuid

import xmltodict
from fastapi import Request

from ...audit.audit import AuditFailureException, attempt_audit
from ...audit.models import AuditOutcome, SAMLAttributes
from ...gpconnect import gpconnect
from ...redis_connect import redis_client
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
from .constants import REGISTRY_ID


async def iti_38_response(request: Request, nhsno: int, ceid, queryid: str, saml_attrs: SAMLAttributes):
    response = AdhocQueryResponse()

    def set_failure(code_context: str) -> None:
        response.status = XDS_FAILURE_STATUS
        response.registry_error_list = RegistryErrorList(
            highest_severity=XDS_ERROR_SEVERITY,
            error=RegistryError(
                error_code="XDSRegistryError",
                code_context=code_context,
                location="",
                severity=XDS_ERROR_SEVERITY,
            ),
        )

    # check the redis cache if there's an existing ccda
    docid = redis_client.get(nhsno)

    cache_hit = docid is not None
    if docid is None:
        # no cached ccda
        try:
            r = await gpconnect(nhsno, saml_attrs, request=request)

            # print("-" * 40)
            logging.info("no cached ccda, used internal call for patient")
            r = json.loads(r.body)
        except AuditFailureException:
            raise
        except Exception as e:
            logging.error(f"Error: {e}")
            r = {
                "success": False,
                "error": f"Internal error retrieving structured record for patient. error: {e}",
            }
            set_failure("Unable to locate SCR for patient")

        if not r.get("success"):
            logging.warning(f"gpconnect failed for patient: {r.get('error')}")
            set_failure(r.get("error", "Unknown error"))
        else:
            docid = r["document_id"]

    await attempt_audit(
        request=request,
        nhs_number=str(nhsno),
        saml=saml_attrs,
        action="iti38_document_query",
        outcome=AuditOutcome.ok if docid else AuditOutcome.fail,
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

    soap_response = SoapEnvelope.create(
        ResponseHeader.create("urn:ihe:iti:2007:CrossGatewayQueryResponse", queryid),
        ITI38ResponseBody(response=response),
    )
    return xmltodict.unparse(soap_response.to_xml_dict(), pretty=True)
