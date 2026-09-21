import xmltodict

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
    PersonName,
    ProviderIdentifier,
    ProviderOrganization,
    QueryAcknowledgement,
    RegistrationEvent,
    ResponseHeader,
    SoapEnvelope,
    Subject,
    Subject1,
    TargetMessage,
    TextElement,
    ValueElement,
)
from .constants import COMMUNITY_ID


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
                                birth_time=ValueElement(value=patient["birthDate"].replace("-", "")),
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
