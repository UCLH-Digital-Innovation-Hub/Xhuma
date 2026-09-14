"""
Contains CDA datatype objects with pydantic validation
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

# TODO: add Enums


class ANY(BaseModel):
    resource_type: str = Field(
        "Any",
        description="This field provides a description for each date type",
        alias="@xsi:type",
    )
    nullFlavor: str | None = None  # enumeration


class BIN(ANY):
    resource_type: str = Field("BIN", description="Binary data.")
    mixed: dict | None = None
    representation: str | None = None  # enumeration B64 or TXT


class URL(ANY):
    resource_type: str = Field("URL", description="URL data.")
    value: str | None = None


class TEL(URL):
    resource_type: str = Field(
        "TEL",
        description="A telephone number, e-mail address, or other "
        "locator for a resource mediated by telecommunication equipment. "
        "The address is specified as a URL qualified by time specification "
        "and use codes that help in deciding which address to use for a "
        "given time and purpose.",
    )
    usablePeriod: list[SXCM_TS] | None = None
    use: list[str] | None = None
    value: str | None = Field(alias="@value", default=None)


class AD(ANY):
    resource_type: str = Field(
        "AD",
        description="Mailing and home or office addresses. A sequence of address parts.",
    )
    use: str | None = Field(alias="@use", default=None)
    streetAddressLine: list[str] | None = None
    city: str | None = None
    state: str | None = None
    postalCode: str | None = None
    country: str | None = None


class ED(BIN):
    resource_type: str = Field(
        "ED",
        description="Data that is primarily intended for human interpretation or for "
        "further machine processing is outside the scope of HL7.",
        alias="@xsi:type",
    )
    reference: TEL | None = None
    thumbnail: str | None = None  # thumbnail
    compression: str | None = None  # enum
    integrityCheck: str | None = None
    integrityCheckAlgorithm: str | None = None  # enum SHA1 or SHA256
    language: str | None = None
    mediaType: str | None = None
    xmlText: str | None = None


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
    assigningAuthorityName: str | None = Field(alias="@assigningAuthorityName", default=None)
    displayable: bool | None = None
    extension: str | None = Field(alias="@extension", default=None)
    root: str | None = Field(alias="@root")

    model_config = {
        "populate_by_name": True,
    }


CODE_SYSTEM_NAMES = {
    "http://snomed.info/sct": "2.16.840.1.113883.6.96",
    "https://dmd.nhs.uk": "2.16.840.1.113883.6.96",
    "https://fhir.nhs.uk/Id/snomed-ct": "2.16.840.1.113883.6.96",
    "https://fhir.nhs.uk/Id/dmd": "2.16.840.1.113883.6.96",
    "https://fhir.nhs.uk/Id/read-codes": "2.16.840.1.113883.2.1.6.2",
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
    codeSystem: str | None = Field(alias="@codeSystem", default=None)
    codeSystemName: str | None = Field(alias="@codeSystemName", default=None)
    displayName: str | None = Field(alias="@displayName", default=None)
    translation: list[CD] | None = None  # Forward reference

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
    value: float | None = None


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
    translation: list[PQR] | None = None
    unit: str | None = Field(alias="@unit", default=None)
    value: float | None = Field(alias="@value", default=None)


class TS(QTY):
    resource_type: str = Field(
        "TS",
        description="A quantity specifying a point on the axis of natural time. A point "
        "in time is most often represented as a calendar expression.",
    )
    value: str | None = Field(
        alias="@value",
        default=None,
        description="Date Format: YYYYMMDDHHMMSS.UUUU[+|-ZZzz]",
    )


class SXCM_TS(TS):
    resource_type: str = Field("SXCM_TS", description="", alias="@xsi:type")
    operator: str | None = Field(alias="@operator", default=None)
    model_config = {
        "populate_by_name": True,
    }


class SXCM_PQ(PQ):
    resource_type: str = Field("SXCM_PQ", description="", alias="@xsi:type")
    operator: str | None = None  # enumeration


class IVXB_TS(SXCM_TS):
    resource_type: str = Field("IVXB_TS", description="", alias="@xsi:type")
    inclusive: bool | None = Field(None, description="Specifies whether the limit is included in the interval.")


class IVXB_PQ(PQ):
    resource_type: str = Field("IVXB_PQ", description="", alias="@xsi:type")
    inclusive: bool | None = Field(None, description="Specifies whether the limit is included in the interval.")


class IVL_PQ(ANY):
    resource_type: str = Field(
        "IVL_PQ",
        alias="@xsi:type",
    )
    unit: CS | None = Field(alias="@unit", default=None)
    value: PQ | None = Field(alias="@value", default=None)
    operator: CS | None = Field(alias="@operator", default=None)
    low: IVXB_PQ | None = None
    center: PQ | None = None
    width: PQ | None = None
    high: IVXB_PQ | None = None
    model_config = {
        "populate_by_name": True,
    }


class IVL_TS(IVXB_TS):
    resource_type: str = Field("IVL_TS", description="Time interval.", alias="@xsi:type")
    low: IVXB_TS | None = None
    center: TS | None = None
    width: PQ | None = None
    high: IVXB_TS | None = None
    model_config = {
        "populate_by_name": True,
    }


class IVL_INT(ANY):
    resource_type: str = Field("IVL_INT", description="Interval of integers.", alias="@xsi:type")
    nullFlavor: str | None = Field(alias="@nullFlavor", default=None)
    value: int | None = Field(alias="@value", default=None)
    operator: str | None = Field(alias="@operator", default=None)
    low: int | None = None
    center: int | None = None
    width: int | None = None
    high: int | None = None
    model_config = {
        "populate_by_name": True,
    }


class PIVL_TS(SXCM_TS):
    resource_type: str = Field("PIVL_TS", description="", alias="@xsi:type")
    phase: IVL_TS | None = None
    period: IVL_PQ | PQ | None = None
    alignment: CalendarCycle | None = Field(alias="@alignment", default=None)
    institutionSpecified: str | None = Field(alias="@institutionSpecified", default=None)
    model_config = {
        "populate_by_name": True,
    }


class EIVL_TS(SXCM_TS):
    resource_type: str = Field("EIVL_TS", description="", alias="@xsi:type")
    event: CE | None = None
    offset: IVL_PQ | None = None
    model_config = {
        "populate_by_name": True,
    }


class CalendarCycle(ANY):
    resource_type: str = Field("CalendarCycle", description="", alias="@xsi:type")
    name: str | None = None


class RTO_PQ_PQ(QTY):
    resource_type: str = Field(
        "RTO_PQ_PQ",
        description="A ratio of two physical quantities.",
        alias="@xsi:type",
    )
    numerator: PQ | None = None
    denominator: PQ | None = None
