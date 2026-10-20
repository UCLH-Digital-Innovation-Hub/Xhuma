"""Typed CDA demographic XML shared with the HL7 v3 ITI-55 response.

This is the demographic subset of CDA core PN, AD, Patient and PatientRole,
not a validator for the complete US-realm CCD profile.
https://build.fhir.org/ig/HL7/CDA-core-2.0/StructureDefinition-Person.html
https://build.fhir.org/ig/HL7/CDA-core-2.0/StructureDefinition-PN.html
https://build.fhir.org/ig/HL7/CDA-core-2.0/StructureDefinition-AD.html
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


class DemographicElement(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    nullFlavor: str | None = Field(default=None, alias="@nullFlavor")

    @model_serializer(mode="wrap")
    def serialize_xml(self, handler):
        # An empty collection means no XML elements, including in typed dumps.
        return {key: value for key, value in handler(self).items() if value != []}

    def to_xml_dict(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True)


class DemographicTime(DemographicElement):
    value: str | None = Field(default=None, alias="@value")


class DemographicPeriod(DemographicElement):
    low: DemographicTime | None = None
    high: DemographicTime | None = None


class NamePart(DemographicElement):
    text: str = Field(alias="#text")


class PersonName(DemographicElement):
    """PN: repeated name parts or an unstructured name, with optional use/time."""

    use: str | None = Field(default=None, alias="@use")
    prefix: list[NamePart] = Field(default_factory=list)
    given: list[NamePart] = Field(default_factory=list)
    family: list[NamePart] = Field(default_factory=list)
    suffix: list[NamePart] = Field(default_factory=list)
    text: str | None = Field(default=None, alias="#text")
    validTime: DemographicPeriod | None = None

    @model_validator(mode="after")
    def validate_content(self):
        parts = self.prefix or self.given or self.family or self.suffix
        if self.text and parts:
            raise ValueError("A person name must contain structured parts or text, not both")
        if self.nullFlavor and (parts or self.text):
            raise ValueError("An unknown name cannot also contain a supplied name")
        if not self.nullFlavor and not parts and not self.text:
            raise ValueError("A person name requires content or nullFlavor")
        return self


class PostalAddress(DemographicElement):
    """AD: optional address parts; no implied home or permanent address."""

    use: str | None = Field(default=None, alias="@use")
    streetAddressLine: list[str] = Field(default_factory=list)
    city: str | None = None
    county: str | None = None
    state: str | None = None
    postalCode: str | None = None
    country: str | None = None
    text: str | None = Field(default=None, alias="#text")
    useablePeriod: list[DemographicPeriod] = Field(default_factory=list)


class DemographicIdentifier(DemographicElement):
    root: str | None = Field(default=None, alias="@root")
    extension: str | None = Field(default=None, alias="@extension")


class DemographicCode(DemographicElement):
    code: str | None = Field(default=None, alias="@code")
    codeSystem: str | None = Field(default=None, alias="@codeSystem")


class ProviderOrganization(DemographicElement):
    classCode: Literal["ORG"] = Field(default="ORG", alias="@classCode")
    determinerCode: Literal["INSTANCE"] = Field(default="INSTANCE", alias="@determinerCode")
    id: list[DemographicIdentifier] = Field(default_factory=list)
    name: list[str] = Field(default_factory=list)
    addr: list[PostalAddress] = Field(default_factory=list)


class CDAPatient(DemographicElement):
    classCode: Literal["PSN"] = Field(default="PSN", alias="@classCode")
    determinerCode: Literal["INSTANCE"] = Field(default="INSTANCE", alias="@determinerCode")
    name: list[PersonName] = Field(min_length=1)
    administrativeGenderCode: DemographicCode | None = None
    birthTime: DemographicTime


class PatientRole(DemographicElement):
    classCode: Literal["PAT"] = Field(default="PAT", alias="@classCode")
    id: list[DemographicIdentifier] = Field(min_length=1)
    addr: list[PostalAddress] = Field(min_length=1)
    patient: CDAPatient
    providerOrganization: ProviderOrganization | None = None


class RecordTarget(DemographicElement):
    typeCode: Literal["RCT"] = Field(default="RCT", alias="@typeCode")
    contextControlCode: Literal["OP"] = Field(default="OP", alias="@contextControlCode")
    patientRole: PatientRole
