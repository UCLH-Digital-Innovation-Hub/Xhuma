import re
from datetime import datetime
from typing import List
from xml.etree import ElementTree

import xmltodict
from fhirclient.models import coding

from .models.datatypes import CD


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
        codeSystem=codings[0].system,
        displayName=codings[0].display,
    )
    # Add translations for each coding
    if len(codings) > 1:
        # print("More than one coding found")
        cd.translation = [
            CD(
                code=coding.code,
                codeSystem=coding.system,
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
