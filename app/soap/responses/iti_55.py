import logging

import xmltodict

from app.ccda.demographics import (
    ODS_ROOT,
    demographic_time,
    person_names,
    postal_addresses,
    provider_organization,
)
from app.ccda.models.demographics import DemographicCode

from ..models import (
    Acknowledgement,
    AcknowledgementDetail,
    AssignedEntity,
    AuthorOrPerformer,
    CodeElement,
    Custodian,
    Identifier,
    ITI55ControlActResponse,
    ITI55ResponseBody,
    ITI55ResponseMessage,
    Patient,
    PatientPerson,
    QueryAcknowledgement,
    RegistrationEvent,
    ResponseHeader,
    SoapEnvelope,
    Subject,
    Subject1,
    TargetMessage,
)
from .constants import COMMUNITY_ID


async def iti_55_response(message_id, patient, query):
    """Generate a successful ITI-55 patient discovery response."""

    providers = []
    for reference in patient.get("generalPractitioner") or []:
        identifier = reference.get("identifier") or {}
        # PDS supplies a GP organisation identifier without a system. The
        # transaction's known ODS authority applies to these identifiers only.
        if identifier and not identifier.get("system"):
            identifier = {**identifier, "system": f"urn:oid:{ODS_ROOT}"}
        providers.append(provider_organization({"identifier": [identifier], "name": reference.get("display")}))
    # The HL7 v3 patient role has only one scoping provider organisation.
    # Multiple GP references do not establish which organisation should scope it.
    provider = next(iter(providers)) if len(providers) == 1 else None
    if len(providers) > 1:
        logging.warning("ITI-55 provider organisation omitted: multiple GP references supplied")
    gender = {"male": "M", "female": "F", "other": "UN"}.get(patient.get("gender"))
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
                                name=person_names(patient.get("name")),
                                gender=DemographicCode(code=gender) if gender else DemographicCode(nullFlavor="UNK"),
                                birth_time=demographic_time(patient.get("birthDate")),
                                addresses=postal_addresses(patient.get("address")),
                            ),
                            provider_organization=provider,
                        )
                    ),
                )
            ),
            query_ack=QueryAcknowledgement(
                query_id=(
                    query.get("queryId", {"@root": "unknown"}) if isinstance(query, dict) else {"@root": "unknown"}
                ),
                response_code=CodeElement(code="OK"),
                status_code=CodeElement(code="deliveredResponse"),
            ),
            query_by_parameter=query if query else {},
        ),
    )

    body = ITI55ResponseBody(message=message)
    header = ResponseHeader.create("urn:hl7-org:v3:PRPA_IN201306UV02:CrossGatewayPatientDiscovery", message_id)
    return xmltodict.unparse(SoapEnvelope.create(header, body).to_xml_dict(), pretty=True)


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
                query_id=(query["queryId"] if "queryId" in query else "can't find queryID"),
                response_code=CodeElement(code="AE"),
                status_code=CodeElement(code="aborted"),
            ),
            query_by_parameter=query,
        ),
    )

    body = ITI55ResponseBody(message=message)
    header = ResponseHeader.create("urn:hl7-org:v3:PRPA_IN201306UV02:CrossGatewayPatientDiscovery", message_id)
    return xmltodict.unparse(SoapEnvelope.create(header, body).to_xml_dict(), pretty=True)
