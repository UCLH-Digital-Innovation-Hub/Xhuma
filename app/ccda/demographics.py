"""Shared repeated-value mappings for FHIR patient names and addresses."""

from collections.abc import Mapping

from fhirclient.models.fhirdate import FHIRDate

from .helpers import fhir_to_cda_timestamp
from .models.demographics import (
    CDAPatient,
    DemographicCode,
    DemographicIdentifier,
    DemographicPeriod,
    DemographicTime,
    NamePart,
    PatientRole,
    PersonName,
    PostalAddress,
    ProviderOrganization,
    RecordTarget,
)

NHS_NUMBER_ROOT = "2.16.840.1.113883.2.1.4.1"
ODS_ROOT = "2.16.840.1.113883.2.1.4.3"
IDENTIFIER_ROOTS = {
    "https://fhir.nhs.uk/Id/nhs-number": NHS_NUMBER_ROOT,
    "https://fhir.nhs.uk/Id/ods-organization-code": ODS_ROOT,
}
# FHIR's usual name does not assert that it is a legal name.
NAME_USES = {"official": "L", "nickname": "P"}
ADDRESS_USES = {"home": "H", "work": "WP", "temp": "TMP"}


def source_dict(value) -> dict:
    if value is None:
        return {}
    return dict(value) if isinstance(value, Mapping) else value.as_json()


def demographic_time(value: str | None) -> DemographicTime:
    return DemographicTime(value=fhir_to_cda_timestamp(FHIRDate(value))) if value else DemographicTime(nullFlavor="UNK")


def demographic_period(value) -> DemographicPeriod | None:
    source = source_dict(value)
    if not source.get("start") and not source.get("end"):
        return None
    return DemographicPeriod(
        low=demographic_time(source["start"]) if source.get("start") else None,
        high=demographic_time(source["end"]) if source.get("end") else None,
    )


def person_names(values) -> list[PersonName]:
    """Keep every name and part, placing usual then official names first."""
    sources = [source_dict(value) for value in values or []]
    sources.sort(key=lambda name: {"usual": 0, "official": 1}.get(name.get("use"), 2))
    result = []
    for source in sources:
        parts = {
            field: [NamePart(text=value) for value in source.get(field) or [] if value]
            for field in ("prefix", "given", "suffix")
        }
        parts["family"] = [NamePart(text=source["family"])] if source.get("family") else []
        # PN is either fielded or a string. Do not duplicate text alongside parts.
        text = source.get("text") if not any(parts.values()) else None
        result.append(
            PersonName(
                **parts,
                text=text,
                use=NAME_USES.get(source.get("use")),
                validTime=demographic_period(source.get("period")),
                nullFlavor="UNK" if not any(parts.values()) and not text else None,
            )
        )
    return result or [PersonName(nullFlavor="UNK")]


def postal_addresses(values, *, required=False) -> list[PostalAddress]:
    result = []
    for value in values or []:
        source = source_dict(value)
        fields = {field: source.get(field) or None for field in ("city", "state", "postalCode", "country")}
        fields["county"] = source.get("district") or None
        lines = [line for line in source.get("line") or [] if line]
        text = source.get("text") if not lines and not any(fields.values()) else None
        period = demographic_period(source.get("period"))
        result.append(
            PostalAddress(
                **fields,
                streetAddressLine=lines,
                text=text,
                use=ADDRESS_USES.get(source.get("use")),
                useablePeriod=[period] if period else [],
                nullFlavor="UNK" if not lines and not text and not any(fields.values()) else None,
            )
        )
    return result or ([PostalAddress(nullFlavor="UNK")] if required else [])


def demographic_identifiers(values) -> list[DemographicIdentifier]:
    result = []
    for value in values or []:
        source = source_dict(value)
        if not source.get("value"):
            continue
        system = source.get("system")
        root = IDENTIFIER_ROOTS.get(system, system)
        if root and root.startswith("urn:oid:"):
            root = root.removeprefix("urn:oid:")
        result.append(DemographicIdentifier(root=root, extension=source["value"], nullFlavor=None if root else "UNK"))
    return result or [DemographicIdentifier(nullFlavor="UNK")]


def provider_organization(value) -> ProviderOrganization:
    source = source_dict(value)
    return ProviderOrganization(
        id=demographic_identifiers(source.get("identifier")),
        name=[source["name"]] if source.get("name") else [],
        addr=postal_addresses(source.get("address")),
    )


ODS_ORGANIZATION_SYSTEM = "https://fhir.nhs.uk/Id/ods-organization-code"


def resolve_managing_organization(patient, index: dict):
    """Resolve Patient.managingOrganization from index using relative or absolute reference."""
    source = source_dict(patient)
    reference = source.get("managingOrganization") or {}
    ref = reference.get("reference")
    organization = index.get(ref)
    if organization is None and ref:
        organization = index.get("/".join(ref.rstrip("/").split("/")[-2:]))
    return organization, reference


def managing_organization_label(patient, index: dict) -> str | None:
    """Return human-readable managing organisation text for narrative display, or None.

    Resolves Patient.managingOrganization against index. Displays name and/or ODS code.
    Never returns raw references or fallbacks.
    """
    organization, reference = resolve_managing_organization(patient, index)
    org_source = source_dict(organization) if organization is not None else {}

    raw_name = org_source.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None

    # ODS identifier: only from https://fhir.nhs.uk/Id/ods-organization-code
    ods_code = None
    for identifier in org_source.get("identifier") or []:
        ident = source_dict(identifier)
        if ident.get("system") == ODS_ORGANIZATION_SYSTEM and ident.get("value"):
            val = str(ident["value"]).strip()
            if val:
                ods_code = val
                break

    # If no org name, check reference display as fallback
    if not name:
        display = reference.get("display")
        if isinstance(display, str) and display.strip():
            candidate = display.strip()
            ref = reference.get("reference")
            ref_prefixes = ("Organization/", "Practitioner/", "Patient/")
            if candidate != ref and not any(candidate.startswith(p) for p in ref_prefixes):
                name = candidate

    if name and ods_code:
        return f"{name} (ODS: {ods_code})"
    if name:
        return name
    if ods_code:
        return f"ODS {ods_code}"
    return None


def patient_record_target(patient, index: dict) -> RecordTarget:
    source = source_dict(patient)
    organization, reference = resolve_managing_organization(patient, index)
    provider = provider_organization(organization) if organization is not None else None
    if provider is None and reference.get("display"):
        provider = ProviderOrganization(name=[reference["display"]])
    gender = {"male": "M", "female": "F", "other": "UN"}.get(source.get("gender"))
    return RecordTarget(
        patientRole=PatientRole(
            id=demographic_identifiers(source.get("identifier")),
            addr=postal_addresses(source.get("address"), required=True),
            patient=CDAPatient(
                name=person_names(source.get("name")),
                administrativeGenderCode=DemographicCode(code=gender) if gender else DemographicCode(nullFlavor="UNK"),
                birthTime=demographic_time(source.get("birthDate")),
            ),
            providerOrganization=provider,
        )
    )
