from pydantic import BaseModel, Field

from .datatypes import AD, CE, CS, II, TEL, TS


class Organization(BaseModel):
    """
    Represents the organization that is represented by the practitioner.
    https://build.fhir.org/ig/HL7/CDA-core-2.0//StructureDefinition-Organization.html
    """

    classcode: str = Field(default="ORG", alias="@classCode")
    determiner_code: str = Field(default="INSTANCE", alias="@determinerCode")
    realmCode: CS | None = None
    typeId: II | None = None
    templateId: list[II] | None = None
    id: list[II] | None = None
    name: list[str] | None = None
    telecom: list[TEL] | None = None
    address: list[AD] | None = None


class Person(BaseModel):
    """
    Represents the person assigned to the practitioner.
    https://build.fhir.org/ig/HL7/CDA-core-2.0//StructureDefinition-Person.html
    """

    classcode: str = Field(default="PSN", alias="@classCode")
    determiner_code: str = Field(default="INSTANCE", alias="@determinerCode")
    name: str | None = None


class AuthoringDevice(BaseModel):
    """
    Represents the device used by the author.
    https://build.fhir.org/ig/HL7/CDA-core-2.0//StructureDefinition-AuthoringDevice.html
    """

    classcode: str = Field(default="DEV", alias="@classCode")
    determiner_code: str = Field(default="INSTANCE", alias="@determinerCode")
    templateId: list[II] | None = None
    code: CE | None = None
    softwareName: str | None = None
    softwareVersion: str | None = None


class AssignedAuthor(BaseModel):
    """
    Represents the author assigned to the practitioner.
    https://build.fhir.org/ig/HL7/CDA-core-2.0//StructureDefinition-AssignedAuthor.html
    """

    classcode: str = Field(default="ASSIGNED", alias="@classCode")
    context_control_code: str = Field(default="OP", alias="@contextControlCode")
    templateId: list[II] | None = None
    id: list[II]
    code: CE | None = None
    address: list[AD] | None = None
    telecom: list[TEL] | None = None
    assignedPerson: Person | None = None
    assignedAuthoringDevice: AuthoringDevice | None = None
    representedOrganization: Organization | None = None

    # @field_serializer("id")
    # def serialize_id(self, value: Union[List[II], II]) -> List[II]:
    #     if isinstance(value, II):
    #         return [value]
    #     return value


class AuthorParticipation(BaseModel):
    """
    Represents the participation of the author in the document.
    https://build.fhir.org/ig/HL7/CDA-ccda-2.1-sd/StructureDefinition-AuthorParticipation.html
    """

    templateId: II | None = None
    time: TS | None = None
    mode_code: str | None = None
    assignedAuthor: AssignedAuthor
    assignedPerson: Person | None = None
    representedOrganization: Organization | None = None
