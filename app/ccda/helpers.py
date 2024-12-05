import re
from datetime import datetime
from xml.etree import ElementTree

import xmltodict
from fhirclient.models import coding


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

    return code


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

    print("Extracting SOAP request from MIME message...")
    print(message)
    # Define the boundary pattern
    boundary_pattern = r"--uuid:[a-f0-9\-]+\+id=[0-9]"
    boundary_start = re.search(boundary_pattern, message)

    if not boundary_start:
        raise ValueError("Boundary not found in the message.")

    # Split the message by the boundary
    parts = message.split(boundary_start.group(0))

    # Extract the content within the SOAP Content-Type section
    for part in parts:
        if "application/soap+xml" in part:
            # The SOAP request starts after the Content-Type header
            soap_start = part.find("<?xml")
            if soap_start == -1:
                soap_start = part.find("<s:Envelope")
            if soap_start != -1:
                # Extract and return the SOAP request
                return part[soap_start:].strip()
    return None
