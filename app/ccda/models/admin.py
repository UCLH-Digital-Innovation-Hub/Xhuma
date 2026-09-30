from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .datatypes import AD, CE, CS, II, PQ, TEL, TS


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


class AssignedEntity(BaseModel):
    """A person or organization acting in an assigned administrative role."""

    model_config = ConfigDict(populate_by_name=True)
    classcode: str = Field(default="ASSIGNED", alias="@classCode")
    id: list[II]
    code: CE | None = None
    address: list[AD] | None = None
    telecom: list[TEL] | None = None
    assignedPerson: Person | None = None
    representedOrganization: Organization | None = None


class AssignedAuthor(BaseModel):
    """An authoring role, with its CDA-specific device and organization order."""

    model_config = ConfigDict(populate_by_name=True)
    classcode: str = Field(default="ASSIGNED", alias="@classCode")
    context_control_code: str | None = Field(default="OP", alias="@contextControlCode")
    templateId: list[II] | None = None
    id: list[II]
    code: CE | None = None
    address: list[AD] | None = None
    telecom: list[TEL] | None = None
    assignedPerson: Person | None = None
    assignedAuthoringDevice: AuthoringDevice | None = None
    representedOrganization: Organization | None = None


class PlayingEntity(BaseModel):
    """Material participating in a clinical entry, such as an allergen or sample."""

    model_config = ConfigDict(populate_by_name=True)
    classCode: Literal["MMAT"] = Field(default="MMAT", alias="@classCode")
    determinerCode: Literal["INSTANCE"] | None = Field(default=None, alias="@determinerCode")
    code: CE
    quantity: list[PQ] | None = None
    name: list[str] | None = None


class AuthorParticipation(BaseModel):
    """
    Represents the participation of the author in the document.
    https://build.fhir.org/ig/HL7/CDA-ccda-2.1-sd/StructureDefinition-AuthorParticipation.html
    """

    templateId: II | None = None
    # When this author participated in creating the entry. This is not Epic
    # section 7(a) result finalisation or 7(b) specimen collection time, and
    # must not be populated from a GP filing date without actual attribution.
    time: TS | None = None
    mode_code: str | None = None
    assignedAuthor: AssignedAuthor
    assignedPerson: Person | None = None
    representedOrganization: Organization | None = None
