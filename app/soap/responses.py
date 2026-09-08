import json
import logging
import os
import uuid
from datetime import datetime, timedelta

import xmltodict
from fastapi import Request

from ..audit.models import SAMLAttributes
from ..gpconnect import gpconnect
from ..redis_connect import redis_client
from .models import (
    XDS_DEFERRED_CREATION_STATUS,
    XDS_ERROR_SEVERITY,
    XDS_FAILURE_STATUS,
    XDS_ON_DEMAND_DOCUMENT_ENTRY,
    Acknowledgement,
    AcknowledgementDetail,
    AdhocQueryResponse,
    AssignedEntity,
    AuthorOrPerformer,
    Classification,
    CodeElement,
    Custodian,
    ExternalIdentifier,
    ExtrinsicObject,
    Identifier,
    InternationalString,
    ITI38ResponseBody,
    ITI39DocumentResponse,
    ITI39ErrorResponseBody,
    ITI39ErrorRetrieveDocumentSetResponse,
    ITI39RegistryErrorList,
    ITI39RegistryResponse,
    ITI39ResponseBody,
    ITI55ControlActResponse,
    ITI55ResponseBody,
    ITI55ResponseMessage,
    LocalizedString,
    Patient,
    PatientPerson,
    PersonName,
    ProviderIdentifier,
    ProviderOrganization,
    QueryAcknowledgement,
    RegistrationEvent,
    RegistryError,
    RegistryErrorList,
    RegistryObjectList,
    ResponseHeader,
    RetrieveDocumentSetResponse,
    SecurityHeader,
    SecurityTimestamp,
    Slot,
    SoapEnvelope,
    Subject,
    Subject1,
    TargetMessage,
    TextElement,
    ValueElement,
)

# REGISTRY_ID = redis_client.get("registry")
COMMUNITY_ID = os.getenv("COMMUNITY_ID", "2.16.840.1.113883.2.1.3.34.9001")
REGISTRY_ID = os.getenv("REGISTRY_ID", "2.16.840.1.113883.2.1.3.34.69.420")


def create_security():
    current_time = datetime.now()
    expiration_time = current_time + timedelta(minutes=5)

    current_timestamp = current_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    expiration_timestamp = expiration_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    return SecurityHeader(
        timestamp=SecurityTimestamp(
            created=TextElement(text=current_timestamp),
            expires=TextElement(text=expiration_timestamp),
        )
    ).to_xml_dict()


def create_header(message_urn: str, message_id: str):
    return ResponseHeader.create(message_urn, message_id).to_xml_dict()


def create_envelope(header, body):
    return SoapEnvelope.create(header, body).to_xml_dict()


def create_id(root, extension):
    return Identifier(root=root, extension=extension).to_xml_dict()


def _select_usual_name(patient: dict) -> dict:
    """Select the FHIR ``usual`` name, falling back for legacy patient data."""

    names = patient["name"]
    # Some older PDS fixtures do not carry a use code. Retaining the first-name
    # fallback avoids rejecting those patients while preferring the intended name.
    return next((name for name in names if name.get("use") == "usual"), names[0])


async def iti_55_response(message_id, patient, query):
    """Generate a successful ITI-55 patient discovery response."""

    gp = patient["generalPractitioner"][0]
    usual_name = _select_usual_name(patient)
    gender = {"male": "M", "female": "F"}.get(patient["gender"], "UNK")
    # The second identifier is the service's internal correlation identifier;
    # the first remains the nationally assigned NHS number.
    patient_ids = [
        Identifier(root="2.16.840.1.113883.2.1.4.1", extension=patient["id"]),
        Identifier(root="2.16.840.1.113883.2.1.4.1.99", extension=patient["id"]),
    ]

    message = ITI55ResponseMessage(
        acknowledgement=Acknowledgement(
            type_code=CodeElement(code="AA"),
            target_message=TargetMessage(identifier=Identifier(root=message_id)),
        ),
        control_act_process=ITI55ControlActResponse(
            author_or_performer=AuthorOrPerformer(),
            subject=Subject(
                registration_event=RegistrationEvent(
                    # ITI-55 health-data-location support is advertised through
                    # this custodian role for the responding community.
                    custodian=Custodian(
                        assigned_entity=AssignedEntity(
                            identifier=Identifier(root=COMMUNITY_ID),
                        )
                    ),
                    subject=Subject1(
                        patient=Patient(
                            identifiers=patient_ids,
                            patient_person=PatientPerson(
                                name=PersonName(
                                    given=TextElement(text=usual_name["given"][0]),
                                    family=TextElement(text=usual_name["family"]),
                                ),
                                gender=CodeElement(code=gender),
                                birth_time=ValueElement(
                                    value=patient["birthDate"].replace("-", "")
                                ),
                            ),
                            provider_organization=ProviderOrganization(
                                identifier=ProviderIdentifier(
                                    identifier=gp["identifier"]["value"],
                                )
                            ),
                        )
                    ),
                )
            ),
            query_ack=QueryAcknowledgement(
                query_id=(
                    query.get("queryId", {"@root": "unknown"})
                    if isinstance(query, dict)
                    else {"@root": "unknown"}
                ),
                response_code=CodeElement(code="OK"),
                status_code=CodeElement(code="deliveredResponse"),
            ),
            query_by_parameter=query if query else {},
        ),
    )

    body = ITI55ResponseBody(message=message)
    header = ResponseHeader.create(
        "urn:hl7-org:v3:PRPA_IN201306UV02:CrossGatewayPatientDiscovery", message_id
    )
    return xmltodict.unparse(
        SoapEnvelope.create(header, body).to_xml_dict(), pretty=True
    )


async def iti_55_error(message_id, query, error_text):
    """Generate an ITI-55 application-error response."""

    message = ITI55ResponseMessage(
        acknowledgement=Acknowledgement(
            type_code=CodeElement(code="AE"),
            target_message=TargetMessage(identifier=Identifier(root=message_id)),
            detail=AcknowledgementDetail(text=error_text),
        ),
        control_act_process=ITI55ControlActResponse(
            query_ack=QueryAcknowledgement(
                query_id=(
                    query["queryId"] if "queryId" in query else "can't find queryID"
                ),
                response_code=CodeElement(code="AE"),
                status_code=CodeElement(code="aborted"),
            ),
            query_by_parameter=query,
        ),
    )

    body = ITI55ResponseBody(message=message)
    header = ResponseHeader.create(
        "urn:hl7-org:v3:PRPA_IN201306UV02:CrossGatewayPatientDiscovery", message_id
    )
    return xmltodict.unparse(
        SoapEnvelope.create(header, body).to_xml_dict(), pretty=True
    )


async def iti_47_response(message_id, patient, ceid, query):
    """ITI47 response message generator

    Args:
        message_id (_type_): _description_
        patient (_type_): _description_
        ceid (_type_): _description_
        query (_type_): _description_

    Returns:
        _type_: _description_
    """

    gp = patient["generalPractitioner"][0]

    # pprint.pprint(patient["address"][0])
    address = patient["address"][0]
    patient_gender = patient["gender"]
    if patient_gender == "male":
        gender = "M"
    elif patient_gender == "female":
        gender = "F"
    else:
        gender = "UNK"

    # loop through names to find official name
    for name in patient["name"]:
        if name.use == "official":
            official_name = name
            break

    ids = []
    ids.append(create_id("2.16.840.1.113883.2.1.4.1", patient["id"]))
    ids.append(create_id("1.2.840.114350.1.13.525.3.7.3.688884.100", ceid))

    body = {
        "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
    }

    body["PRPA_IN201306UV02"] = {
        "@xmlns": "urn:hl7-org:v3",
        "@ITSVersion": "XML_1.0",
        "id": {"@root": str(uuid.uuid4())},
        "creationTime": {"@value": int(datetime.now().timestamp())},
        "interactionId": {
            "@root": "2.16.840.1.113883.1.18",
            "@extension": "PRPA_IN201306UV02",
        },
        "processingCode": {"@code": "T"},
        "processingModeCode": {"@code": "T"},
        "acceptAckCode": {"@code": "NE"},
        "receiver": {
            "@typeCode": "RCV",
            "device": {"@classCode": "DEV", "@determinerCode": "INSTANCE"},
        },
        "sender": {
            "@typeCode": "SND",
            "device": {"@classCode": "DEV", "@determinerCode": "INSTANCE"},
        },
        "acknowledgement": {
            "typeCode": {"@code": "AA"},
            "targetMessage": {"id": {"@root": message_id}},
        },
        "controlActProcess": {
            "@classCode": "CACT",
            "@moodCode": "EVN",
            "code": {
                "@code": "PRPA_TE201306UV02",
                "@codeSystem": "2.16.840.1.113883.1.18",
            },
            "authorOrPerformer": {
                "@typeCode": "AUT",
                "assignedDevice": {
                    "@classCode": "ASSIGNED",
                    "id": {"@root": "1.2.840.114350.1.13.1610.1.7.3.688884.100"},
                },
            },
            "subject": {
                "@typeCode": "SUBJ",
                "@contextConductionInd": "false",
                "registrationEvent": {
                    "@classCode": "REG",
                    "moodCode": "EVN",
                    "statusCode": {"@code": "active"},
                    "subject1": {
                        "@typeCode": "SBJ",
                        "patient": {
                            "@classCode": "PAT",
                            "id": ids,
                            "statusCode": {"@code": "active"},
                            "patientPerson": {
                                "@classCode": "PSN",
                                "@determinerCode": "INSTANCE",
                                "name": {
                                    "given": {"#text": official_name["given"][0]},
                                    "family": {"#text": official_name["family"]},
                                },
                                "administrativeGenderCode": {"@code": gender},
                                # birthTime is ISO 8601 format
                                "birthTime": {
                                    "@value": patient["birthDate"].replace("-", "")
                                },
                                # "birthTime": {"@value": patient["birthDate"]},
                                "addr": {
                                    "streetAddressLine": address["line"],
                                    "postalCode": {"#text": address["postalCode"]},
                                },
                            },
                            "providerOrganization": {
                                "@classCode": "ORG",
                                "@determinerCode": "INSTANCE",
                                "id": {
                                    "@root": "2.16.840.1.113883.2.1.4.3",
                                    "id": gp["identifier"]["value"],
                                },
                            },
                        },
                    },
                },
            },
            "queryAck": {
                "queryId": query["queryId"],
                "queryResponseCode": {"@code": "OK"},
                "statusCode": {"@code": "deliveredResponse"},
            },
            "queryByParameter": query,
        },
    }
    header = create_header("urn:hl7-org:v3:PRPA_IN201306UV02", message_id)

    return xmltodict.unparse(create_envelope(header, body), pretty=True)


async def iti_38_response(
    request: Request, nhsno: int, ceid, queryid: str, saml_attrs: SAMLAttributes
):
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

    if docid is None:
        # no cached ccda
        r = await gpconnect(nhsno, saml_attrs, request=request)
        # print("-" * 40)
        # print(r.body)
        # print("-" * 40)
        try:
            r = await gpconnect(nhsno, saml_attrs, request=request)

            # print("-" * 40)
            logging.info("no cached ccda, used internal call for patient")
            r = json.loads(r.body)
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
                object_type=(
                    "urn:oasis:names:tc:ebxml-regrep:ObjectType:"
                    "RegistryObject:Classification"
                ),
                slot=Slot.create("codingScheme", value),
                name=InternationalString(
                    localized_string=LocalizedString(value=localized_string)
                ),
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
        external_identifier_type = (
            "urn:oasis:names:tc:ebxml-regrep:ObjectType:"
            "RegistryObject:ExternalIdentifier"
        )
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
                        identification_scheme=(
                            "urn:uuid:2e82c1f6-a085-4c72-9da3-8640a32e42ab"
                        ),
                        value=docid,
                        identifier=docid,
                        registry_object=object_id,
                        object_type=external_identifier_type,
                        name=InternationalString(
                            localized_string=LocalizedString(
                                value="XDSDocumentEntry.uniqueId"
                            )
                        ),
                    ),
                    ExternalIdentifier(
                        identification_scheme=(
                            "urn:uuid:58a6f841-87b3-4a3e-92fd-a8ffeff98427"
                        ),
                        value=f"{nhsno}^^^&2.16.840.1.113883.2.1.4.99.1&ISO",
                        identifier=f"PID-{nhsno}",
                        registry_object=object_id,
                        object_type=external_identifier_type,
                        name=InternationalString(
                            localized_string=LocalizedString(
                                value="XDSDocumentEntry.patientId"
                            )
                        ),
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


async def iti_39_response(message_id: str, document_id: str, document):
    """Generate a successful ITI-39 document retrieval response."""

    body = ITI39ResponseBody(
        response=RetrieveDocumentSetResponse(
            registry_response=ITI39RegistryResponse(
                identifier=str(uuid.uuid4()),
            ),
            document_response=ITI39DocumentResponse(
                home_community_id=TextElement(text=f"urn:oid:{COMMUNITY_ID}"),
                repository_unique_id=TextElement(text=REGISTRY_ID),
                document_unique_id=TextElement(text=document_id),
                document=document,
            ),
        )
    )
    soap_response = SoapEnvelope.create(
        ResponseHeader.create(
            "urn:ihe:iti:2007:CrossGatewayRetrieveResponse", message_id
        ),
        body,
    )
    return xmltodict.unparse(soap_response.to_xml_dict(), pretty=True)


async def iti_39_error(message_id: str, document_id: str) -> str:
    """Generate an ITI-39 missing-document registry error response."""

    body = ITI39ErrorResponseBody(
        response=ITI39ErrorRetrieveDocumentSetResponse(
            registry_response=ITI39RegistryResponse(
                status=XDS_FAILURE_STATUS,
                registry_error_list=ITI39RegistryErrorList(
                    highest_severity=XDS_ERROR_SEVERITY,
                    error=RegistryError(
                        error_code="XDSDocumentUniqueIdError",
                        code_context=f"Document with Id {document_id} not found",
                        severity=XDS_ERROR_SEVERITY,
                    ),
                ),
            )
        ),
    )
    soap_response = SoapEnvelope.create(
        ResponseHeader.create(
            "urn:ihe:iti:2007:CrossGatewayRetrieveResponse", message_id
        ),
        body,
    )
    return xmltodict.unparse(
        soap_response.to_xml_dict(), full_document=False, pretty=True
    )
