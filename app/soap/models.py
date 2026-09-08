"""Pydantic models for the IHE SOAP messages handled by this package.

The field aliases deliberately match ``xmltodict``'s representation: XML
attributes start with ``@`` and element text is stored under ``#text``.  Keeping
that convention lets the existing XML serializer remain in place while giving
the request handlers and response builders typed, named structures.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer

HL7_INTERACTION_ROOT = "2.16.840.1.113883.1.18"
NHS_NUMBER_ROOT = "2.16.840.1.113883.2.1.4.1"
GP_ORGANIZATION_ROOT = "2.16.840.1.113883.2.1.4.3"

XDS_SUCCESS_STATUS = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success"
XDS_FAILURE_STATUS = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Failure"
XDS_ERROR_SEVERITY = "urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error"
XDS_DEFERRED_CREATION_STATUS = "urn:ihe:iti:2010:StatusType:DeferredCreation"
XDS_ON_DEMAND_DOCUMENT_ENTRY = "urn:uuid:34268e47-fdf5-41a6-ba33-82133c465248"


class XmlModel(BaseModel):
    """Base model that can round-trip through ``xmltodict`` dictionaries."""

    # IHE profiles permit extension content. Keeping unknown fields allows the
    # typed subset used here to round-trip extensions without silently removing
    # data from echoed queries.
    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
        arbitrary_types_allowed=True,
    )

    def to_xml_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class TextElement(XmlModel):
    must_understand: int | None = Field(default=None, alias="@s:mustUnderstand")
    text: Any = Field(alias="#text")


class CodeElement(XmlModel):
    code: str = Field(alias="@code")
    code_system: str | None = Field(default=None, alias="@codeSystem")


class ValueElement(XmlModel):
    value: Any = Field(alias="@value")


class Identifier(XmlModel):
    root: str = Field(alias="@root")
    extension: str | None = Field(default=None, alias="@extension")


# ---------------------------------------------------------------------------
# Incoming request models


class ReplyTo(XmlModel):
    address: str | None = Field(default=None, alias="Address")


class RequestHeader(XmlModel):
    message_id: str | None = Field(default=None, alias="MessageID")
    reply_to: ReplyTo | None = Field(default=None, alias="ReplyTo")
    security: dict[str, Any] | None = Field(default=None, alias="Security")


class LivingSubjectId(XmlModel):
    value: Identifier | list[Identifier] | None = None

    def identifiers(self) -> list[Identifier]:
        if self.value is None:
            return []
        return self.value if isinstance(self.value, list) else [self.value]


class ITI55ParameterList(XmlModel):
    living_subject_id: LivingSubjectId | list[LivingSubjectId] | None = Field(
        default=None, alias="livingSubjectId"
    )

    def identifiers(self) -> list[Identifier]:
        if self.living_subject_id is None:
            return []
        parameters = (
            self.living_subject_id
            if isinstance(self.living_subject_id, list)
            else [self.living_subject_id]
        )
        return [identifier for item in parameters for identifier in item.identifiers()]


class ITI55QueryByParameter(XmlModel):
    query_id: Any = Field(default=None, alias="queryId")
    status_code: Any = Field(default=None, alias="statusCode")
    response_modality_code: Any = Field(default=None, alias="responseModalityCode")
    response_priority_code: Any = Field(default=None, alias="responsePriorityCode")
    parameter_list: ITI55ParameterList | None = Field(
        default=None, alias="parameterList"
    )


class ITI55ControlActProcess(XmlModel):
    query_by_parameter: ITI55QueryByParameter | None = Field(
        default=None, alias="queryByParameter"
    )


class ITI55Payload(XmlModel):
    control_act_process: ITI55ControlActProcess | None = Field(
        default=None, alias="controlActProcess"
    )


class ITI55RequestBody(XmlModel):
    payload: ITI55Payload | None = Field(default=None, alias="PRPA_IN201305UV02")


class ITI55Request(XmlModel):
    header: RequestHeader = Field(default_factory=RequestHeader, alias="Header")
    body: ITI55RequestBody = Field(default_factory=ITI55RequestBody, alias="Body")

    @property
    def query(self) -> ITI55QueryByParameter | None:
        payload = self.body.payload
        control_act = payload.control_act_process if payload else None
        return control_act.query_by_parameter if control_act else None

    def patient_identifier(self, assigning_authority: str) -> str | None:
        parameters = self.query.parameter_list if self.query else None
        if parameters is None:
            return None
        for identifier in parameters.identifiers():
            if identifier.root == assigning_authority:
                return identifier.extension
        return None


class ValueList(XmlModel):
    value: Any = Field(default=None, alias="Value")

    def first_value(self) -> str | None:
        # The stored-query schema permits repeated values. This service handles
        # one patient per request, matching the behaviour of the original code.
        value = (
            self.value[0] if isinstance(self.value, list) and self.value else self.value
        )
        if isinstance(value, dict):
            value = value.get("#text")
        return str(value) if value is not None else None


class QuerySlot(XmlModel):
    name: str | None = Field(default=None, alias="@name")
    value_list: ValueList | None = Field(default=None, alias="ValueList")


class AdhocQuery(XmlModel):
    query_id: str = Field(default="unknown", alias="@id")
    slots: QuerySlot | list[QuerySlot] | None = Field(default=None, alias="Slot")

    def slot_value(self, name: str) -> str | None:
        if self.slots is None:
            return None
        slots = self.slots if isinstance(self.slots, list) else [self.slots]
        for slot in slots:
            if slot.name == name and slot.value_list is not None:
                return slot.value_list.first_value()
        return None


class AdhocQueryContainer(XmlModel):
    adhoc_query: AdhocQuery | None = Field(default=None, alias="AdhocQuery")


class ITI38RequestBody(XmlModel):
    adhoc_query_request: AdhocQueryContainer | None = Field(
        default=None, alias="AdhocQueryRequest"
    )
    cross_gateway_query: AdhocQueryContainer | None = Field(
        default=None, alias="CrossGatewayQuery"
    )

    @property
    def query(self) -> AdhocQuery | None:
        container = self.adhoc_query_request or self.cross_gateway_query
        return container.adhoc_query if container else None


class ITI38Request(XmlModel):
    header: RequestHeader = Field(default_factory=RequestHeader, alias="Header")
    body: ITI38RequestBody = Field(default_factory=ITI38RequestBody, alias="Body")


class DocumentRequest(XmlModel):
    home_community_id: str | None = Field(default=None, alias="HomeCommunityId")
    repository_unique_id: str | None = Field(default=None, alias="RepositoryUniqueId")
    document_unique_id: str | None = Field(default=None, alias="DocumentUniqueId")


class RetrieveDocumentSetRequest(XmlModel):
    document_request: DocumentRequest | list[DocumentRequest] | None = Field(
        default=None, alias="DocumentRequest"
    )

    @property
    def first_document(self) -> DocumentRequest | None:
        """Return the document handled by this single-document endpoint."""

        if isinstance(self.document_request, list):
            return self.document_request[0] if self.document_request else None
        return self.document_request


class ITI39RequestBody(XmlModel):
    retrieve_document_set_request: RetrieveDocumentSetRequest | None = Field(
        default=None, alias="RetrieveDocumentSetRequest"
    )


class ITI39Request(XmlModel):
    header: RequestHeader = Field(default_factory=RequestHeader, alias="Header")
    body: ITI39RequestBody = Field(default_factory=ITI39RequestBody, alias="Body")


# ---------------------------------------------------------------------------
# Shared response envelope models


class SecurityTimestamp(XmlModel):
    timestamp_id: str = Field(default="_0", alias="@u:Id")
    created: TextElement = Field(alias="u:Created")
    expires: TextElement = Field(alias="u:Expires")


class SecurityHeader(XmlModel):
    must_understand: int = Field(default=1, alias="@s:mustUnderstand")
    xmlns_o: str = Field(
        default=(
            "http://docs.oasis-open.org/wss/2004/01/"
            "oasis-200401-wss-wssecurity-secext-1.0.xsd"
        ),
        alias="@xmlns:o",
    )
    timestamp: SecurityTimestamp = Field(alias="u:Timestamp")


class ResponseHeader(XmlModel):
    action: TextElement = Field(alias="a:Action")
    relates_to: TextElement = Field(alias="a:RelatesTo")

    @classmethod
    def create(cls, action: str, message_id: str) -> ResponseHeader:
        return cls(
            action=TextElement(must_understand=1, text=action),
            relates_to=TextElement(text=message_id),
        )


class SoapEnvelopeContent(XmlModel):
    xmlns_s: str = Field(
        default="http://www.w3.org/2003/05/soap-envelope", alias="@xmlns:s"
    )
    xmlns_a: str = Field(
        default="http://www.w3.org/2005/08/addressing", alias="@xmlns:a"
    )
    xmlns_u: str = Field(
        default=(
            "http://docs.oasis-open.org/wss/2004/01/"
            "oasis-200401-wss-wssecurity-utility-1.0.xsd"
        ),
        alias="@xmlns:u",
    )
    header: Any = Field(alias="s:Header")
    body: Any = Field(alias="s:Body")


class SoapEnvelope(XmlModel):
    envelope: SoapEnvelopeContent = Field(alias="s:Envelope")

    @classmethod
    def create(cls, header: Any, body: Any) -> SoapEnvelope:
        if isinstance(header, XmlModel):
            header = header.to_xml_dict()
        if isinstance(body, XmlModel):
            body = body.to_xml_dict()
        return cls(envelope=SoapEnvelopeContent(header=header, body=body))


# ---------------------------------------------------------------------------
# ITI-55 response models


class Device(XmlModel):
    class_code: str = Field(default="DEV", alias="@classCode")
    determiner_code: str = Field(default="INSTANCE", alias="@determinerCode")


class MessageEndpoint(XmlModel):
    type_code: str = Field(alias="@typeCode")
    device: Device = Field(default_factory=Device)


class TargetMessage(XmlModel):
    identifier: Identifier = Field(alias="id")


class AcknowledgementDetail(XmlModel):
    text: str = Field(alias="@text")


class Acknowledgement(XmlModel):
    type_code: CodeElement = Field(alias="typeCode")
    target_message: TargetMessage = Field(alias="targetMessage")
    detail: AcknowledgementDetail | None = Field(
        default=None, alias="acknowledgementDetail"
    )


class AssignedDevice(XmlModel):
    class_code: str = Field(default="ASSIGNED", alias="@classCode")
    identifier: Identifier = Field(
        default_factory=lambda: Identifier(root=NHS_NUMBER_ROOT), alias="id"
    )


class AuthorOrPerformer(XmlModel):
    type_code: str = Field(default="AUT", alias="@typeCode")
    assigned_device: AssignedDevice = Field(
        default_factory=AssignedDevice, alias="assignedDevice"
    )


class AssignedEntity(XmlModel):
    class_code: str = Field(default="ASSIGNED", alias="@classCode")
    identifier: Identifier = Field(alias="id")
    code: CodeElement = Field(
        default_factory=lambda: CodeElement(
            code="SupportsHealthDataLocator",
            code_system="1.3.6.1.4.1.19376.1.2.27.2",
        )
    )


class Custodian(XmlModel):
    type_code: str = Field(default="CST", alias="@typeCode")
    assigned_entity: AssignedEntity = Field(alias="assignedEntity")


class PersonName(XmlModel):
    given: TextElement
    family: TextElement


class PatientPerson(XmlModel):
    class_code: str = Field(default="PSN", alias="@classCode")
    determiner_code: str = Field(default="INSTANCE", alias="@determinerCode")
    name: PersonName
    gender: CodeElement = Field(alias="administrativeGenderCode")
    birth_time: ValueElement = Field(alias="birthTime")


class ProviderIdentifier(XmlModel):
    root: str = Field(default=GP_ORGANIZATION_ROOT, alias="@root")
    identifier: Any = Field(alias="id")


class ProviderOrganization(XmlModel):
    class_code: str = Field(default="ORG", alias="@classCode")
    determiner_code: str = Field(default="INSTANCE", alias="@determinerCode")
    identifier: ProviderIdentifier = Field(alias="id")


class Patient(XmlModel):
    class_code: str = Field(default="PAT", alias="@classCode")
    identifiers: list[Identifier] = Field(alias="id")
    status_code: CodeElement = Field(
        default_factory=lambda: CodeElement(code="active"), alias="statusCode"
    )
    patient_person: PatientPerson = Field(alias="patientPerson")
    provider_organization: ProviderOrganization = Field(alias="providerOrganization")


class Subject1(XmlModel):
    type_code: str = Field(default="SBJ", alias="@typeCode")
    patient: Patient


class RegistrationEvent(XmlModel):
    class_code: str = Field(default="REG", alias="@classCode")
    mood_code: str = Field(default="EVN", alias="moodCode")
    status_code: CodeElement = Field(
        default_factory=lambda: CodeElement(code="active"), alias="statusCode"
    )
    custodian: Custodian | None = None
    subject: Subject1 = Field(alias="subject1")


class Subject(XmlModel):
    type_code: str = Field(default="SUBJ", alias="@typeCode")
    context_conduction: str = Field(default="false", alias="@contextConductionInd")
    registration_event: RegistrationEvent = Field(alias="registrationEvent")


class QueryAcknowledgement(XmlModel):
    query_id: Any = Field(alias="queryId")
    response_code: CodeElement = Field(alias="queryResponseCode")
    status_code: CodeElement = Field(alias="statusCode")


class ITI55ControlActResponse(XmlModel):
    class_code: str = Field(default="CACT", alias="@classCode")
    mood_code: str = Field(default="EVN", alias="@moodCode")
    code: CodeElement = Field(
        default_factory=lambda: CodeElement(
            code="PRPA_TE201306UV02", code_system=HL7_INTERACTION_ROOT
        )
    )
    author_or_performer: AuthorOrPerformer | None = Field(
        default=None, alias="authorOrPerformer"
    )
    subject: Subject | None = None
    query_ack: QueryAcknowledgement = Field(alias="queryAck")
    query_by_parameter: Any = Field(alias="queryByParameter")


class ITI55ResponseMessage(XmlModel):
    xmlns: str = Field(default="urn:hl7-org:v3", alias="@xmlns")
    its_version: str = Field(default="XML_1.0", alias="@ITSVersion")
    identifier: Identifier = Field(
        default_factory=lambda: Identifier(root=str(uuid4())), alias="id"
    )
    creation_time: ValueElement = Field(
        default_factory=lambda: ValueElement(value=int(datetime.now().timestamp())),
        alias="creationTime",
    )
    interaction_id: Identifier = Field(
        default_factory=lambda: Identifier(
            root=HL7_INTERACTION_ROOT, extension="PRPA_IN201306UV02"
        ),
        alias="interactionId",
    )
    # These fixed transmission values preserve the existing synchronous test-mode
    # contract: test processing, no separate accept acknowledgement.
    processing_code: CodeElement = Field(
        default_factory=lambda: CodeElement(code="T"), alias="processingCode"
    )
    processing_mode_code: CodeElement = Field(
        default_factory=lambda: CodeElement(code="T"), alias="processingModeCode"
    )
    accept_ack_code: CodeElement = Field(
        default_factory=lambda: CodeElement(code="NE"), alias="acceptAckCode"
    )
    receiver: MessageEndpoint = Field(
        default_factory=lambda: MessageEndpoint(type_code="RCV")
    )
    sender: MessageEndpoint = Field(
        default_factory=lambda: MessageEndpoint(type_code="SND")
    )
    acknowledgement: Acknowledgement
    control_act_process: ITI55ControlActResponse = Field(alias="controlActProcess")


class ITI55ResponseBody(XmlModel):
    xmlns_xsi: str = Field(
        default="http://www.w3.org/2001/XMLSchema-instance", alias="@xmlns:xsi"
    )
    xmlns_xsd: str = Field(
        default="http://www.w3.org/2001/XMLSchema", alias="@xmlns:xsd"
    )
    message: ITI55ResponseMessage = Field(alias="PRPA_IN201306UV02")


# ---------------------------------------------------------------------------
# ITI-38 response models


class RegistryError(XmlModel):
    error_code: str = Field(alias="@errorCode")
    code_context: str = Field(alias="@codeContext")
    location: str | None = Field(default=None, alias="@location")
    severity: str = Field(alias="@severity")


class RegistryErrorList(XmlModel):
    highest_severity: str = Field(alias="@highestSeverity")
    error: RegistryError = Field(alias="RegistryError")


class SlotValueList(XmlModel):
    value: TextElement = Field(alias="Value")


class Slot(XmlModel):
    name: str = Field(alias="@name")
    value_list: SlotValueList = Field(alias="ValueList")

    @classmethod
    def create(cls, name: str, value: Any) -> Slot:
        return cls(name=name, value_list=SlotValueList(value=TextElement(text=value)))


class LocalizedString(XmlModel):
    value: str = Field(alias="@value")


class InternationalString(XmlModel):
    localized_string: LocalizedString = Field(alias="LocalizedString")


class Classification(XmlModel):
    classification_scheme: str = Field(alias="@classificationScheme")
    classified_object: str = Field(alias="@classifiedObject")
    identifier: str = Field(alias="@id")
    node_representation: str = Field(alias="@nodeRepresentation")
    object_type: str = Field(alias="@objectType")
    slot: Slot = Field(alias="Slot")
    name: InternationalString = Field(alias="Name")


class ExternalIdentifier(XmlModel):
    identification_scheme: str = Field(alias="@identificationScheme")
    value: str = Field(alias="@value")
    identifier: str = Field(alias="@id")
    registry_object: str = Field(alias="@registryObject")
    object_type: str = Field(alias="@objectType")
    name: InternationalString = Field(alias="Name")


class ExtrinsicObject(XmlModel):
    identifier: str = Field(alias="@id")
    status: str = Field(alias="@status")
    object_type: str = Field(alias="@objectType")
    mime_type: str = Field(alias="@mimeType")
    slots: list[Slot] = Field(alias="Slot")
    classifications: list[Classification] = Field(alias="Classification")
    external_identifiers: list[ExternalIdentifier] = Field(alias="ExternalIdentifier")


class RegistryObjectList(XmlModel):
    xmlns: str = Field(
        default="urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0", alias="@xmlns"
    )
    extrinsic_object: ExtrinsicObject = Field(alias="ExtrinsicObject")


class AdhocQueryResponse(XmlModel):
    status: str = Field(default=XDS_SUCCESS_STATUS, alias="@status")
    xmlns: str = Field(
        default="urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0", alias="@xmlns"
    )
    registry_error_list: RegistryErrorList | None = Field(
        default=None, alias="RegistryErrorList"
    )
    registry_object_list: RegistryObjectList | dict[str, Any] | None = Field(
        default=None, alias="RegistryObjectList"
    )


class ITI38ResponseBody(XmlModel):
    response: AdhocQueryResponse = Field(alias="AdhocQueryResponse")


# ---------------------------------------------------------------------------
# ITI-39 response models


class ITI39RegistryErrorList(XmlModel):
    highest_severity: str = Field(alias="@highestSeverity")
    error: RegistryError = Field(alias="rs:RegistryError")


class ITI39RegistryResponse(XmlModel):
    identifier: str | None = Field(default=None, alias="@id")
    status: str = Field(default=XDS_SUCCESS_STATUS, alias="@status")
    registry_error_list: ITI39RegistryErrorList | None = Field(
        default=None, alias="rs:RegistryErrorList"
    )


class ITI39DocumentResponse(XmlModel):
    home_community_id: TextElement = Field(alias="ns4:HomeCommunityId")
    repository_unique_id: TextElement = Field(alias="ns4:RepositoryUniqueId")
    document_unique_id: TextElement = Field(alias="ns4:DocumentUniqueId")
    mime_type: TextElement = Field(
        default_factory=lambda: TextElement(text="text/xml"), alias="ns4:mimeType"
    )
    document: bytes | str = Field(alias="ns4:Document")

    @field_serializer("document")
    def serialize_document(self, value: bytes | str) -> str:
        # Redis stores the generated C-CDA as bytes; the established SOAP payload
        # embeds that XML text directly inside the Document element.
        return value.decode("utf-8") if isinstance(value, bytes) else value


class RetrieveDocumentSetResponse(XmlModel):
    xmlns_ns4: str = Field(default="urn:ihe:iti:xds-b:2007", alias="@xmlns:ns4")
    xmlns_ns8: str | None = Field(
        default="urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0",
        alias="@xmlns:ns8",
    )
    xmlns_rs: str | None = Field(default=None, alias="@xmlns:rs")
    registry_response: ITI39RegistryResponse = Field(alias="ns8:RegistryResponse")
    document_response: ITI39DocumentResponse | None = Field(
        default=None, alias="ns4:DocumentResponse"
    )


class ITI39ResponseBody(XmlModel):
    response: RetrieveDocumentSetResponse = Field(
        alias="ns4:RetrieveDocumentSetResponse"
    )


class ITI39ErrorRetrieveDocumentSetResponse(XmlModel):
    xmlns_ns4: str = Field(default="urn:ihe:iti:xds-b:2007", alias="@xmlns:ns4")
    xmlns_rs: str = Field(
        default="urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0", alias="@xmlns:rs"
    )
    registry_response: ITI39RegistryResponse = Field(alias="rs:RegistryResponse")


class ITI39ErrorResponseBody(XmlModel):
    response: ITI39ErrorRetrieveDocumentSetResponse = Field(
        alias="ns4:RetrieveDocumentSetResponse"
    )
