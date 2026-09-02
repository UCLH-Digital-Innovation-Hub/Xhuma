import logging
from datetime import datetime
from typing import List, Optional
from xml.etree import ElementTree

import xmltodict
from fhirclient.models import coding, organization, period

from .models.admin import AssignedAuthor, AuthorParticipation, Organization
from .models.datatypes import CD, SXCM_TS, II, TEL, AD


def validateNHSnumber(number: int) -> bool:
    """validates NHS number

    Args:
        NHs number as integer

    Returns:
        Boolean if NHS number is valid or not
    """
    if len(str(number)) != 10:
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


class FHIRValidationError(Exception):
    pass


def extract_original_term(concept) -> Optional[str]:
    """
    Extracts the original clinical term from a CodeableConcept following GP Connect 1.6.2 precedence.
    Priority:
    1. CodeableConcept.text
    2. SNOMED description display extension (subject to userSelected)
    3. Coding.display (subject to userSelected)
    4. First available coding display (if only one coding or no userSelected present)
    """
    if not concept:
        return None

    if concept.text:
        return concept.text

    if not concept.coding:
        return None

    user_selected_codings = [c for c in concept.coding if c.userSelected]
    codings_to_check = user_selected_codings if user_selected_codings else concept.coding

    # SNOMED extension check would normally go here if modeled in fhirclient, 
    # but for now we check coding displays.
    for c in codings_to_check:
        if c.display:
            return c.display

    # Fallback to first available display if no userSelected has a display
    if not user_selected_codings:
        for c in concept.coding:
            if c.display:
                return c.display

    return None


def convert_codeable_concept(
    concept, *, degradation_code: Optional[CD] = None
) -> Optional[CD]:
    """
    Takes a CodeableConcept and returns a CD object, safely degrading if terminology is unsupported.
    """
    if not concept:
        return None

    original_text = extract_original_term(concept)

    if not concept.coding:
        if original_text:
            if degradation_code:
                return CD(
                    code=degradation_code.code,
                    codeSystem=degradation_code.codeSystem,
                    codeSystemName=degradation_code.codeSystemName,
                    displayName=degradation_code.displayName,
                    originalText=original_text,
                )
            return CD(nullFlavor="OTH", originalText=original_text)
        raise FHIRValidationError(
            "No coding and no original text found in CodeableConcept"
        )

    from .models.datatypes import CODE_SYSTEM_NAMES

    supported_codings = [c for c in concept.coding if c.system in CODE_SYSTEM_NAMES and c.code]

    if supported_codings:
        supported_codings.sort(
            key=lambda x: x.system == "http://snomed.info/sct", reverse=True
        )
        primary = supported_codings[0]

        cd = CD(
            code=primary.code,
            codeSystemName=primary.system,
            displayName=primary.display,
            originalText=original_text,
        )

        if len(supported_codings) > 1:
            cd.translation = [
                CD(
                    code=c.code,
                    codeSystemName=c.system,
                    displayName=c.display,
                )
                for c in supported_codings[1:]
            ]
        return cd
    else:
        if original_text:
            logging.warning(
                f"Unsupported coding system(s) safely degraded: {[c.system for c in concept.coding]}"
            )
            if degradation_code:
                return CD(
                    code=degradation_code.code,
                    codeSystem=degradation_code.codeSystem,
                    codeSystemName=degradation_code.codeSystemName,
                    displayName=degradation_code.displayName,
                    originalText=original_text,
                )
            return CD(nullFlavor="OTH", originalText=original_text)
        else:
            raise FHIRValidationError(
                f"Unsupported coding system(s) without original text: {[c.system for c in concept.coding]}"
            )


def templateId(root: str, extension: str) -> list:
    """
    takes root and extensions and returns list for proper
    ccda formatting
    """
    template = [{"@root": root}, {"@root": root, "@extension": extension}]

    return template


def date_helper(isodate):
    """
    takes iso string and returns to format valid for ccda

    """
    new_date = datetime.strptime(isodate[:10], "%Y-%m-%d").strftime("%Y%m%d")

    return new_date


def effective_time_helper(effective_period: period.Period) -> List[SXCM_TS]:
    """
    Takes a FHIR effective period and returns a list of SXCM_TS objects
    """
    # effective_period = effective_period.as_json()
    start = effective_period.start
    # end = effective_period.get("end")
    # print(effective_period.as_json())
    # print(date_helper(start.isostring))

    # Create the SXCM_TS objects
    sxcm_ts_list = []
    if start:
        low_value = SXCM_TS(operator="low")
        low_value.value = date_helper(start.isostring)
        sxcm_ts_list.append(low_value)
    if effective_period.end:
        high_value = SXCM_TS(operator="high")
        high_value.value = date_helper(effective_period.end.isostring)
        sxcm_ts_list.append(high_value)
        # sxcm_ts_list.append(SXCM_TS(operator="high", value=date_helper(effective_period.end.isostring)))
    # Example usage of as_dict
    return sxcm_ts_list


def readable_date(date):
    """
    takes date string in YYYYMMDD format and returns to more readable format
    """
    new_date = datetime.strptime(date, "%Y%m%d").strftime("%d/%m/%Y")

    return new_date


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
    dom = ElementTree.fromstring(soap_request)
    # root = dom.getroot()

    xmldict = xmltodict.parse(
        ElementTree.tostring(dom),
        process_namespaces=True,
        namespaces=namespaces,
    )
    return xmldict["Envelope"]


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


TELECOM_USE_MAP = {
    "home": "HP",
    "work": "WP",
    "temp": "TMP",
    "old": "BAD",
    "mobile": "MC",
}


def contact_point_to_cda_tel(contact) -> Optional[TEL]:
    if not contact.value:
        return None

    value = contact.value
    system = contact.system

    use = None

    if system == "phone":
        value = f"tel:{value}"
    elif system == "fax":
        value = f"x-text-fax:{value}"
    elif system == "email":
        value = f"mailto:{value}"
    elif system == "url":
        if value.startswith("http://") or value.startswith("https://"):
            value = value
        else:
            logging.warning(f"Malformed URL telecom value omitted: {value}")
            return None
    elif system == "pager":
        value = f"tel:{value}"
        use = "PG"
    else:
        logging.warning(f"Unsupported telecom system encountered and omitted: {system}")
        return None

    if not use and contact.use:
        use = TELECOM_USE_MAP.get(contact.use)

    kwargs = {"@value": value}
    if use:
        kwargs["@use"] = use

    return TEL(**kwargs)


ADDRESS_USE_MAP = {
    "home": "HP",
    "work": "WP",
    "temp": "TMP",
    "old": "BAD",
    "billing": "BIL",
}


def address_to_cda_ad(address) -> AD:
    kwargs = {}
    if address.use and address.use in ADDRESS_USE_MAP:
        kwargs["@use"] = ADDRESS_USE_MAP[address.use]

    kwargs["streetAddressLine"] = list(address.line or [])
    if address.city:
        kwargs["city"] = address.city
    if address.state:
        kwargs["state"] = address.state
    if address.postalCode:
        kwargs["postalCode"] = address.postalCode
    if address.country:
        kwargs["country"] = address.country

    return AD(**kwargs)


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
        id=[
            II(**{"@root": ident.system, "@extension": ident.value})
            for ident in organization.identifier
        ],
    )
    if organization.name:
        author.representedOrganization = Organization(**{"name": [organization.name]})

    if organization.telecom:
        author.telecom = []
        for telecom in organization.telecom:
            tel = contact_point_to_cda_tel(telecom)
            if tel:
                author.telecom.append(tel)

    if organization.address:
        author.address = []
        for addr in organization.address:
            author.address.append(address_to_cda_ad(addr))

    org = AuthorParticipation(assignedAuthor=author)

    return org


def clean_number(x):
    # if x is a float and is an integer, convert to int
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x
