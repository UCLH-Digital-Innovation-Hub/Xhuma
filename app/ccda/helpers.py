import re
from datetime import date as CalendarDate
from datetime import datetime
from typing import List

import xmltodict
from defusedxml import ElementTree
from fastapi import HTTPException
from fhirclient.models import coding, fhirdate, identifier, organization, period
from fhirclient.models.humanname import HumanName

from .models.admin import AssignedAuthor, AuthorParticipation
from .models.datatypes import CD, II, IVL_TS, IVXB_TS, TS


def clean_number(x):
    # if x is a float and is an integer, convert to int
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def clean_soap(
    soap_request,
    namespaces: dict = {
        "http://www.w3.org/2003/05/soap-envelope": None,
        "http://www.w3.org/2005/08/addressing": None,
        "urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0": None,
        "urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0": None,
        "urn:ihe:iti:xds-b:2007": None,
        "urn:hl7-org:v3": None,
        "soap": None,
        "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd": None,
        "urn:oasis:names:tc:SAML:2.0:assertion": None,
    },
) -> dict:
    """
    Takes raw soap requests and cleans

    Args:
        - soap_request: XML IHE soap request
        - namespaces: dict of namespaces to process

    Returns
        - Soap envelope as dict
    """
    try:
        dom = ElementTree.fromstring(soap_request)
        # root = dom.getroot()

        xmldict = xmltodict.parse(
            ElementTree.tostring(dom),
            process_namespaces=True,
            namespaces=namespaces,
        )
        return xmldict["Envelope"]
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed XML in request body")


def code_with_translations(codings: List[coding.Coding]) -> CD:
    """
    Takes a list of coding objects and returns a CD object with translations
    Args:
        codings: List of fhir coding objects
    Returns:
        CD object with translations if more than one coding is provided
    """
    # Check if the list is empty
    if not codings:
        return None

    # sort for SNOMED first
    # codings.sort(key=lambda x: x.get("system") == "http://snomed.info/sct")

    codings.sort(key=lambda x: x.system == "http://snomed.info/sct", reverse=True)

    # Create the CD object
    cd = CD(
        code=codings[0].code,
        codeSystemName=codings[0].system,
        displayName=codings[0].display,
    )
    # Add translations for each coding
    if len(codings) > 1:
        cd.translation = [
            CD(
                code=coding.code,
                codeSystemName=coding.system,
                displayName=coding.display,
            )
            for coding in codings[1:]
        ]

    return cd


def fhir_to_cda_timestamp(date: fhirdate.FHIRDate | None) -> str | None:
    """Preserve FHIR date precision, fractional seconds and offset in CDA syntax.

    Use the original JSON value: isostring may add a month/day or lose fractions.
    This only formats the supplied date; callers choose its clinical meaning.
    """
    source = date.as_json() if date is not None else None
    if not source:
        return None
    day, _, time = source.partition("T")
    return day.replace("-", "") + time.replace(":", "").replace("Z", "+0000")


def cda_timestamp(date: fhirdate.FHIRDate | None) -> TS:
    """Build a standalone timestamp, explicitly marking an absent date unknown."""
    value = fhir_to_cda_timestamp(date)
    return TS(value=value) if value is not None else TS(nullFlavor="UNK")


def cda_time_bound(date: fhirdate.FHIRDate | None) -> IVXB_TS:
    """Build an interval endpoint, explicitly marking an absent date unknown."""
    value = fhir_to_cda_timestamp(date)
    return IVXB_TS(value=value) if value is not None else IVXB_TS(nullFlavor="UNK")


def cda_time_interval(start: fhirdate.FHIRDate | None, end: fhirdate.FHIRDate | None) -> IVL_TS:
    """Build an interval with explicit bounds; missing endpoints are unknown.

    Pass the same date twice for an instant. Callers that deliberately omit an
    endpoint should construct IVL_TS themselves instead of using this helper.
    """
    return IVL_TS(low=cda_time_bound(start), high=cda_time_bound(end))


def effective_time_helper(effective_period: period.Period | None) -> list[IVL_TS]:
    """Build medication duration, omitting endpoints absent from the source."""
    if effective_period is None:
        return []
    start, end = effective_period.start, effective_period.end
    if start is None and end is None:
        return []
    return [
        IVL_TS(
            low=cda_time_bound(start) if start is not None else None,
            high=cda_time_bound(end) if end is not None else None,
        )
    ]


def fhir_date_is_after(value: fhirdate.FHIRDate | None, reference_date: CalendarDate) -> bool:
    """Compare calendar dates only at the precision supplied by FHIR.

    A future year/month qualifies; the current year/month alone cannot establish
    a future end date. Preserve the source calendar day without timezone shifting.
    Missing values return False; malformed source dates are not silently ignored.
    """
    source = value.as_json() if value is not None else None
    if not source:
        return False
    parts = tuple(int(part) for part in source.partition("T")[0].split("-"))
    reference = (reference_date.year, reference_date.month, reference_date.day)
    return parts > reference[: len(parts)]


def extract_soap_request(message):
    """
    Extracts the SOAP request from a MIME message.
    """

    # print("Extracting SOAP request from MIME message...")
    # print(message)

    # iterate throught the message lines and find soap envelope

    for line in message.splitlines():
        if line.startswith("<s:Envelope "):
            return line
    # if can't find a soap envelope raise an error
    raise ValueError("SOAP envelope not found in the message.")


def generate_code(coding: coding.Coding) -> dict:
    code = {
        "@code": coding.code,
        "@displayName": coding.display,
        "@codeSystemName": coding.system,
    }

    if coding.system == "http://snomed.info/sct":
        code["@codeSystem"] = "2.16.840.1.113883.6.96"
    elif coding.system == "https://fhir.hl7.org.uk/Id/multilex-drug-codes":
        code["@codeSystem"] = "2.16.840.1.113883.2.1.6.4"

    return code


def id_helper(identities: identifier.Identifier) -> list[II]:
    """
    takes list of dicts with root and extension and returns list of II objects
    """
    return [II(**{"@root": item.system, "@extension": item.value}) for item in identities]


def organization_to_author(
    organization: organization.Organization,
) -> AuthorParticipation:
    """
    Converts a FHIR Organization resource to an AuthoeParticpation object.
    Args:
        organization (organization.Organization): FHIR Organization resource.
    Returns:
        AuthorParticipation: An AuthorParticipation object with the organization details.
    """
    author = AssignedAuthor(
        id=[{"@root": ident.system, "@extension": ident.value} for ident in organization.identifier],
    )
    if organization.name:
        author.representedOrganization = {"name": organization.name}

    if organization.telecom:
        author.telecom = [
            {
                "@use": telecom.use,
                "@value": telecom.value,
            }
            for telecom in organization.telecom
        ]
    if organization.address:
        author.address = [addr.as_json() for addr in organization.address]

    org = AuthorParticipation(assignedAuthor=author)

    return org


def readable_date(value: str) -> str:
    """Display a valid CDA timestamp as a date without adding missing precision.

    Full dates display DD/MM/YYYY; partial dates retain YYYY or YYYY-MM. Time
    and offset are validated but omitted from display, without timezone shifting.
    Empty or malformed inputs raise ValueError; callers handle absent dates.
    """
    match = re.fullmatch(
        r"(?P<year>[0-9]{4})(?:(?P<month>[0-9]{2})(?:(?P<day>[0-9]{2})"
        r"(?:(?P<hour>[0-9]{2})(?:(?P<minute>[0-9]{2})(?:(?P<second>[0-9]{2})"
        r"(?P<fraction>\.[0-9]+)?)?)?(?P<zone>[+-][0-9]{4})?)?)?)?",
        value,
    )
    if match is None:
        raise ValueError(f"Invalid CDA timestamp: {value!r}")
    fields = match.groupdict()
    # Validate calendar and clock components; defaults are for validation only.
    datetime(
        int(fields["year"]),
        int(fields["month"] or 1),
        int(fields["day"] or 1),
        int(fields["hour"] or 0),
        int(fields["minute"] or 0),
        int(fields["second"] or 0),
    )
    zone = fields["zone"]
    if zone and (int(zone[1:3]) > 14 or int(zone[3:]) > 59 or (zone[1:3] == "14" and zone[3:] != "00")):
        raise ValueError(f"Invalid CDA timezone: {zone!r}")
    if fields["day"]:
        return f"{fields['day']}/{fields['month']}/{fields['year']}"
    if fields["month"]:
        return f"{fields['year']}-{fields['month']}"
    return fields["year"]


def select_patient_name(names: list[HumanName] | None) -> HumanName:
    """Select usual, then official, then the first supplied FHIR patient name.

    Return the original HumanName, preserving source order within each use.
    Raise ValueError when no names were supplied rather than inventing a name.
    """
    if not names:
        raise ValueError("Patient record contains no names")

    for preferred_use in ("usual", "official"):
        for name in names:
            if name.use == preferred_use:
                return name
    return names[0]


def templateId(root: str, extension: str) -> list:
    """
    takes root and extensions and returns list for proper
    ccda formatting
    """
    template = [{"@root": root}, {"@root": root, "@extension": extension}]

    return template


def validateNHSnumber(number: int) -> bool:
    """validates NHS number

    Args:
        NHs number as integer

    Returns:
        Boolean if NHS number is valid or not
    """
    if len(str(number)) != 10 or not str(number).isdigit():
        return False

    numbers = [int(c) for c in str(number)]

    total = 0
    for idx in range(0, 9):
        multiplier = 10 - idx
        total += numbers[idx] * multiplier

    _, modtot = divmod(total, 11)
    checkdig = 11 - modtot

    if checkdig == 11:
        checkdig = 0

    return checkdig == numbers[9]
