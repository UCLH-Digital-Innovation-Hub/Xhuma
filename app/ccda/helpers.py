import re
from datetime import datetime
from typing import List
from xml.etree import ElementTree

import xmltodict
from fhirclient.models import coding, organization, period

from .models.admin import AssignedAuthor, AuthorParticipation
from .models.datatypes import CD, SXCM_TS


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
            {"@root": ident.system, "@extension": ident.value}
            for ident in organization.identifier
        ],
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
