"""
Contains CDA datatype objects with pydantic validation
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

# TODO: add Enums


class ANY(BaseModel):
    resource_type: str = Field(
        "Any",
        description="This field provides a description for each date type",
        alias="@xsi:type",
    )
    nullFlavor: Optional[str] = None  # enumeration


class BIN(ANY):
    resource_type: str = Field("BIN", description="Binary data.")
    mixed: Optional[Dict] = None
    representation: Optional[str] = None  # enumeration B64 or TXT


class URL(ANY):
    resource_type: str = Field("URL", description="URL data.")
    value: Optional[str] = None


class TEL(URL):
    resource_type: str = Field(
        "TEL",
        description="A telephone number, e-mail address, or other "
        "locator for a resource mediated by telecommunication equipment. "
        "The address is specified as a URL qualified by time specification "
        "and use codes that help in deciding which address to use for a "
        "given time and purpose.",
    )
    usablePeriod: Optional[List[SXCM_TS]] = None
    use: Optional[List[str]] = None


class ED(BIN):
    resource_type: str = Field(
        "ED",
        description="Data that is primarily intended for human interpretation or for "
        "further machine processing is outside the scope of HL7.",
    )
    reference: Optional[TEL] = None
    thumbnail: Optional[str] = None  # thumbnail
    compression: Optional[str] = None  # enum
    integrityCheck: Optional[str] = None
    integrityCheckAlgorithm: Optional[str] = None  # enum SHA1 or SHA256
    language: Optional[str] = None
    mediaType: Optional[str] = None


class QTY(ANY):
    resource_type: str = Field(
        "QTY",
        description="The quantity data type is an abstract generalization for all data "
        "types (1) whose value set has an order relation (less-or-equal) "
        "and (2) where difference is defined in all of the data type's "
        "totally ordered value subsets. The quantity type abstraction is "
        "needed in defining certain other types, such as the interval and "
        "the probability distribution.",
    )


class II(ANY):
    resource_type: str = Field(
        "II",
        description="An identifier that uniquely identifies a thing or object.",
        alias="@xsi:type",
    )
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
    "LOINC": "2.16.840.1.113883.6.1",
    "https://fhir.hl7.org.uk/Id/multilex-drug-codes": "2.16.840.1.113883.2.1.6.4",
    "https://fhir.hl7.org.uk/Id/resipuk-gemscript-drug-codes": "2.16.840.1.113883.2.1.6.15",
    "https://fhir.hl7.org.uk/Id/emis-drug-codes": "2.16.840.1.113883.2.1.6.9",
}


class CD(ANY):
    resource_type: str = Field(
        "CD",
        description="A concept descriptor represents any kind of concept usually by giving a "
        "code defined in a code system. A concept descriptor can contain the "
        "original text or phrase that served as the basis of the coding and one "
        "or more translations into different coding systems.",
        alias="@xsi:type",
    )
    code: str = Field(alias="@code")
    codeSystem: Optional[str] = Field(alias="@codeSystem", default=None)
    codeSystemName: Optional[str] = Field(alias="@codeSystemName", default=None)
    displayName: Optional[str] = Field(alias="@displayName", default=None)
    translation: Optional[List["CD"]] = None  # Forward reference

    @model_validator(mode="before")
    def set_code_system_from_name(cls, values):
        cs = values.get("codeSystemName")
        if cs and not values.get("codeSystem"):
            values["codeSystem"] = CODE_SYSTEM_NAMES.get(cs)

        # if codesystem is not in code_system_names, print an alert to console
        if cs and not values.get("codeSystem"):
            print(f"Warning🚨: Code system '{cs}' not found in CODE_SYSTEM_NAMES.")
        return values

    model_config = {
        "populate_by_name": True,
    }


CD.model_rebuild()


class CE(CD):
    resource_type: str = Field(
        "CE",
        description="Coded data, consists of a coded value (CV) and, optionally, "
        "coded value(s) from other coding systems that identify the same "
        "concept. Used when alternative codes may exist.",
        alias="@xsi:type",
    )


class CV(CE):
    resource_type: str = Field(
        "CV",
        description="Coded data, consists of a code, display name, code system, "
        "and original text. Used when a single code value must be sent.",
    )


class PQR(CV):
    resource_type: str = Field(
        "PQR",
        description="A representation of a physical quantity in a unit from any code "
        "system. Used to show alternative representation for a physical "
        "quantity.",
    )
    value: Optional[float] = None


class CS(CV):
    resource_type: str = Field(
        "CS",
        description="Coded data, consists of a code, display name, code system, and original "
        "text. Used when a single code value must be sent.",
        alias="@xsi:type",
    )


class PQ(QTY):
    resource_type: str = Field(
        "PQ",
        description="A dimensioned quantity expressing the result of a measurement act.",
        alias="@xsi:type",
    )
    translation: Optional[List[PQR]] = None
    unit: Optional[str] = Field(alias="@unit", default=None)
    value: Optional[float] = Field(alias="@value", default=None)


class TS(QTY):
    resource_type: str = Field(
        "TS",
        description="A quantity specifying a point on the axis of natural time. A point "
        "in time is most often represented as a calendar expression.",
    )
    value: Optional[str] = Field(
        alias="@value",
        default=None,
        description="Date Format: YYYYMMDDHHMMSS.UUUU[+|-ZZzz]",
    )


class SXCM_TS(TS):
    resource_type: str = Field("SXCM_TS", description="", alias="@xsi:type")
    operator: Optional[str] = Field(alias="@operator", default=None)
    model_config = {
        "populate_by_name": True,
    }


class SXCM_PQ(PQ):
    resource_type: str = Field("SXCM_PQ", description="", alias="@xsi:type")
    operator: Optional[str] = None  # enumeration


class IVXB_TS(SXCM_TS):
    resource_type: str = Field("IVXB_TS", description="", alias="@xsi:type")
    inclusive: Optional[bool] = Field(
        None, description="Specifies whether the limit is included in the interval."
    )


class IVXB_PQ(PQ):
    resource_type: str = Field("IVXB_PQ", description="", alias="@xsi:type")
    inclusive: Optional[bool] = Field(
        None, description="Specifies whether the limit is included in the interval."
    )


class IVL_PQ(ANY):
    resource_type: str = Field(
        "IVL_PQ",
        alias="@xsi:type",
    )
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
    resource_type: str = Field(
        "IVL_TS", description="Time interval.", alias="@xsi:type"
    )
    low: Optional[IVXB_TS] = None
    center: Optional[TS] = None
    width: Optional[PQ] = None
    high: Optional[IVXB_TS] = None
    model_config = {
        "populate_by_name": True,
    }


class PIVL_TS(SXCM_TS):
    resource_type: str = Field("PIVL_TS", description="", alias="@xsi:type")
    phase: Optional[IVL_TS] = None
    period: Optional[PQ] = None
    alignment: Optional[CalendarCycle] = Field(alias="@alignment", default=None)
    institutionSpecified: Optional[str] = Field(
        alias="@institutionSpecified", default=None
    )
    model_config = {
        "populate_by_name": True,
    }


class EIVL_TS(SXCM_TS):
    resource_type: str = Field("EIVL_TS", description="", alias="@xsi:type")
    event: Optional[CE] = None
    offset: Optional[IVL_PQ] = None
    model_config = {
        "populate_by_name": True,
    }


class CalendarCycle(ANY):
    resource_type: str = Field("CalendarCycle", description="", alias="@xsi:type")
    name: Optional[str] = None
