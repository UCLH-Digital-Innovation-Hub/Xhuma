from typing import Dict, List, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field

from .datatypes import CE, CS, II, TEL, TS


class Organization(BaseModel):
    """
    Represents the organization that is represented by the practitioner.
    https://build.fhir.org/ig/HL7/CDA-core-2.0//StructureDefinition-Organization.html
    """

    classcode: str = Field(default="ORG", alias="@classCode")
    determiner_code: str = Field(default="INSTANCE", alias="@determinerCode")
    realmCode: Optional[CS] = None
    typeId: Optional[II] = None
    templateId: Optional[List[II]] = None
    id: Optional[List[II]] = None
    name: Optional[List[str]] = None
    telecom: Optional[List[TEL]] = None
    address: Optional[List[Dict]] = None


class Person(BaseModel):
    """
    Represents the person assigned to the practitioner.
    https://build.fhir.org/ig/HL7/CDA-core-2.0//StructureDefinition-Person.html
    """

    classcode: str = Field(default="PSN", alias="@classCode")
    determiner_code: str = Field(default="INSTANCE", alias="@determinerCode")
    name: Optional[str] = None


class AuthoringDevice(BaseModel):
    """
    Represents the device used by the author.
    https://build.fhir.org/ig/HL7/CDA-core-2.0//StructureDefinition-AuthoringDevice.html
    """

    classcode: str = Field(default="DEV", alias="@classCode")
    determiner_code: str = Field(default="INSTANCE", alias="@determinerCode")
    templateId: Optional[List[II]] = None
    code: Optional[CE] = None
    softwareName: Optional[str] = None
    softwareVersion: Optional[str] = None


class AssignedAuthor(BaseModel):
    """
    Represents the author assigned to the practitioner.
    https://build.fhir.org/ig/HL7/CDA-core-2.0//StructureDefinition-AssignedAuthor.html
    """

    classcode: str = Field(default="ASSIGNED", alias="@classCode")
    context_control_code: str = Field(default="OP", alias="@contextControlCode")
    templateId: Optional[List[II]] = None
    id: List[II]
    code: Optional[CE] = None
    address: Optional[List[Dict]] = None
    telecom: Optional[List[TEL]] = None
    assignedPerson: Optional[Person] = None
    assignedAuthoringDevice: Optional[AuthoringDevice] = None
    representedOrganization: Optional[Organization] = None

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

    templateId: Optional[II] = None
    time: Optional[TS] = None
    mode_code: Optional[str] = None
    assignedAuthor: AssignedAuthor
    assignedPerson: Optional[Person] = None
    representedOrganization: Optional[Organization] = None
