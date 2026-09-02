"""
Contains CDA datatype objects with pydantic validation
"""

from __future__ import annotations

from typing import Dict, List, Optional, Union, ClassVar

from pydantic import BaseModel, Field, model_validator

# TODO: add Enums


class ANY(BaseModel):
    resource_type: ClassVar[str] = "ANY"
    xsi_type: Optional[str] = Field(default=None, alias="@xsi:type")
    nullFlavor: Optional[str] = Field(alias="@nullFlavor", default=None)  # enumeration


class ST(ANY):
    resource_type: ClassVar[str] = "ST"
    xsi_type: Optional[str] = Field(default="ST", alias="@xsi:type")
    text: Optional[str] = Field(alias="#text", default=None)


class BIN(ANY):
    resource_type: ClassVar[str] = "BIN"
    mixed: Optional[Dict] = None
    representation: Optional[str] = None  # enumeration B64 or TXT


class URL(ANY):
    resource_type: ClassVar[str] = "URL"
    value: Optional[str] = None


class TEL(URL):
    resource_type: ClassVar[str] = "TEL"
    usablePeriod: Optional[List[SXCM_TS]] = None
    use: Optional[str] = Field(alias="@use", default=None)
    value: Optional[str] = Field(alias="@value", default=None)


class AD(ANY):
    resource_type: ClassVar[str] = "AD"
    use: Optional[str] = Field(alias="@use", default=None)
    streetAddressLine: Optional[List[str]] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postalCode: Optional[str] = None
    country: Optional[str] = None


class ED(BIN):
    resource_type: ClassVar[str] = "ED"
    xsi_type: Optional[str] = Field(default="ED", alias="@xsi:type")
    reference: Optional[TEL] = None
    thumbnail: Optional[str] = None  # thumbnail
    compression: Optional[str] = None  # enum
    integrityCheck: Optional[str] = None
    integrityCheckAlgorithm: Optional[str] = None  # enum SHA1 or SHA256
    language: Optional[str] = None
    mediaType: Optional[str] = None
    xmlText: Optional[Union[str, dict]] = None


class QTY(ANY):
    resource_type: ClassVar[str] = "QTY"


class II(ANY):
    resource_type: ClassVar[str] = "II"
    assigningAuthorityName: Optional[str] = Field(
        alias="@assigningAuthorityName", default=None
    )
    displayable: Optional[bool] = None
    extension: Optional[str] = Field(alias="@extension", default=None)
    root: Optional[str] = Field(alias="@root")

    model_config = {
        "populate_by_name": True,
    }


CODE_SYSTEM_NAMES = {
    "http://snomed.info/sct": "2.16.840.1.113883.6.96",
    "https://dmd.nhs.uk": "2.16.840.1.113883.6.96",
    "https://fhir.nhs.uk/Id/snomed-ct": "2.16.840.1.113883.6.96",
    "https://fhir.nhs.uk/Id/dmd": "2.16.840.1.113883.6.96",
    "http://read.info/readv2": "2.16.840.1.113883.2.1.6.2",
    "http://read.info/ctv3": "2.16.840.1.113883.2.1.6.3",
    "https://fhir.nhs.uk/Id/read-codes": "2.16.840.1.113883.2.1.6.2",
    "LOINC": "2.16.840.1.113883.6.1",
    "https://fhir.hl7.org.uk/Id/multilex-drug-codes": "2.16.840.1.113883.2.1.6.4",
    "https://fhir.hl7.org.uk/Id/resipuk-gemscript-drug-codes": "2.16.840.1.113883.2.1.6.15",
    "https://fhir.hl7.org.uk/Id/emis-drug-codes": "2.16.840.1.113883.2.1.6.9",
    "https://fhir.hl7.org.uk/Id/egton-drug-codes": "2.16.840.1.113883.2.1.6.1",
}


class CD(ANY):
    resource_type: ClassVar[str] = "CD"
    xsi_type: Optional[str] = Field(default="CD", alias="@xsi:type")
    code: Optional[str] = Field(alias="@code", default=None)
    codeSystem: Optional[str] = Field(alias="@codeSystem", default=None)
    codeSystemName: Optional[str] = Field(alias="@codeSystemName", default=None)
    displayName: Optional[str] = Field(alias="@displayName", default=None)
    originalText: Optional[str] = None
    translation: Optional[List["CD"]] = None  # Forward reference

    @model_validator(mode="before")
    def validate_cd(cls, values):
        if not isinstance(values, dict):
            return values
        cs = values.get("codeSystemName")
        if cs and not values.get("codeSystem") and not values.get("@codeSystem"):
            values["codeSystem"] = CODE_SYSTEM_NAMES.get(cs)

        # if codesystem is not in code_system_names, print an alert to console
        if cs and not values.get("codeSystem") and not values.get("@codeSystem"):
            print(f"Warning🚨: Code system '{cs}' not found in CODE_SYSTEM_NAMES.")

        code = values.get("code") or values.get("@code")
        null_flavor = values.get("nullFlavor") or values.get("@nullFlavor")

        if not code and not null_flavor:
            raise ValueError("CD must have either a code or a nullFlavor")

        return values

    model_config = {
        "populate_by_name": True,
    }


CD.model_rebuild()


class CE(CD):
    resource_type: ClassVar[str] = "CE"
    xsi_type: Optional[str] = Field(default=None, alias="@xsi:type")


class CV(CE):
    resource_type: ClassVar[str] = "CV"


class PQR(CV):
    resource_type: ClassVar[str] = "PQR"
    value: Optional[float] = Field(alias="@value", default=None)


class CS(CV):
    resource_type: ClassVar[str] = "CS"


class PQ(QTY):
    resource_type: ClassVar[str] = "PQ"
    xsi_type: Optional[str] = Field(default="PQ", alias="@xsi:type")
    translation: Optional[List[PQR]] = None
    unit: Optional[str] = Field(alias="@unit", default=None)
    value: Optional[float] = Field(alias="@value", default=None)


class TS(QTY):
    resource_type: ClassVar[str] = "TS"
    value: Optional[str] = Field(
        alias="@value",
        default=None,
        description="Date Format: YYYYMMDDHHMMSS.UUUU[+|-ZZzz]",
    )


class SXCM_TS(TS):
    resource_type: ClassVar[str] = "SXCM_TS"
    xsi_type: Optional[str] = Field(default="SXCM_TS", alias="@xsi:type")
    operator: Optional[str] = Field(alias="@operator", default=None)
    model_config = {
        "populate_by_name": True,
    }


class SXCM_PQ(PQ):
    resource_type: ClassVar[str] = "SXCM_PQ"
    xsi_type: Optional[str] = Field(default="SXCM_PQ", alias="@xsi:type")
    operator: Optional[str] = None  # enumeration


class IVXB_TS(SXCM_TS):
    resource_type: ClassVar[str] = "IVXB_TS"
    xsi_type: Optional[str] = Field(default="IVXB_TS", alias="@xsi:type")
    inclusive: Optional[bool] = Field(
        None, description="Specifies whether the limit is included in the interval."
    )


class IVXB_PQ(PQ):
    resource_type: ClassVar[str] = "IVXB_PQ"
    xsi_type: Optional[str] = Field(default="IVXB_PQ", alias="@xsi:type")
    inclusive: Optional[bool] = Field(
        None, description="Specifies whether the limit is included in the interval."
    )


class IVL_PQ(ANY):
    resource_type: ClassVar[str] = "IVL_PQ"
    xsi_type: Optional[str] = Field(default="IVL_PQ", alias="@xsi:type")
    unit: Optional[CS] = Field(alias="@unit", default=None)
    value: Optional[PQ] = Field(alias="@value", default=None)
    operator: Optional[CS] = Field(alias="@operator", default=None)
    low: Optional[IVXB_PQ] = None
    center: Optional[PQ] = None
    width: Optional[PQ] = None
    high: Optional[IVXB_PQ] = None
    model_config = {
        "populate_by_name": True,
    }


class IVL_TS(IVXB_TS):
    resource_type: ClassVar[str] = "IVL_TS"
    xsi_type: Optional[str] = Field(default="IVL_TS", alias="@xsi:type")
    low: Optional[IVXB_TS] = None
    center: Optional[TS] = None
    width: Optional[PQ] = None
    high: Optional[IVXB_TS] = None
    model_config = {
        "populate_by_name": True,
    }


class IVL_INT(ANY):
    resource_type: ClassVar[str] = "IVL_INT"
    nullFlavor: Optional[str] = Field(alias="@nullFlavor", default=None)
    value: Optional[int] = Field(alias="@value", default=None)
    operator: Optional[str] = Field(alias="@operator", default=None)
    low: Optional[int] = None
    center: Optional[int] = None
    width: Optional[int] = None
    high: Optional[int] = None
    model_config = {
        "populate_by_name": True,
    }


class PIVL_TS(SXCM_TS):
    resource_type: ClassVar[str] = "PIVL_TS"
    xsi_type: Optional[str] = Field(default="PIVL_TS", alias="@xsi:type")
    phase: Optional[IVL_TS] = None
    period: Optional[Union[IVL_PQ, PQ]] = None
    alignment: Optional[CalendarCycle] = Field(alias="@alignment", default=None)
    institutionSpecified: Optional[str] = Field(
        alias="@institutionSpecified", default=None
    )
    model_config = {
        "populate_by_name": True,
    }


class EIVL_TS(SXCM_TS):
    resource_type: ClassVar[str] = "EIVL_TS"
    xsi_type: Optional[str] = Field(default="EIVL_TS", alias="@xsi:type")
    event: Optional[CE] = None
    offset: Optional[IVL_PQ] = None
    model_config = {
        "populate_by_name": True,
    }


class CalendarCycle(ANY):
    resource_type: ClassVar[str] = "CalendarCycle"
    name: Optional[str] = None


class RTO_PQ_PQ(QTY):
    resource_type: ClassVar[str] = "RTO_PQ_PQ"
    xsi_type: Optional[str] = Field(default="RTO_PQ_PQ", alias="@xsi:type")
    numerator: Optional[PQ] = None
    denominator: Optional[PQ] = None
