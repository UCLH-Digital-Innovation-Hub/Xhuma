"""Typed document header, composed from the existing CDA models.

This models the header emitted by Xhuma, not the complete CCD profile.
Field order follows the CDA document's XML sequence.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from .admin import AuthorParticipation
from .datatypes import CE, CS, II, IVL_TS, ST, TS
from .demographics import RecordTarget


class ServiceEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    classCode: Literal["PCPR"] = Field(default="PCPR", alias="@classCode")
    effectiveTime: IVL_TS


class DocumentationOf(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceEvent: ServiceEvent


class ClinicalDocumentHeader(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    xmlns: str = Field(default="urn:hl7-org:v3", alias="@xmlns")
    xmlns_xsi: str = Field(default="http://www.w3.org/2001/XMLSchema-instance", alias="@xmlns:xsi")
    xmlns_voc: str = Field(default="urn:hl7-org:v3/voc", alias="@xmlns:voc")
    xmlns_sdtc: str = Field(default="urn:hl7-org:sdtc", alias="@xmlns:sdtc")
    realmCode: CS
    typeId: II
    templateId: list[II]
    code: CE
    title: ST
    effectiveTime: TS
    recordTarget: RecordTarget
    author: AuthorParticipation
    documentationOf: DocumentationOf

    @field_serializer("typeId")
    def serialize_type_id(self, value: II) -> dict:
        # The typeId element supplies its specialised CDA schema type.
        return value.model_dump(by_alias=True, exclude_none=True, exclude={"resource_type"})

    def to_xml_dict(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True)
