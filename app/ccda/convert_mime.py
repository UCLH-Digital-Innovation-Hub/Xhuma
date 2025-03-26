import base64
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart

import xmltodict


def convert_mime(ccda: dict):
    """
    Takes CCDA and converts it to XOP for sending via SOAP

    Args:
        ccda: dictionary created by convert bundle

    Returns:
        XOP infoset
    """

    ccda_xml = xmltodict.unparse(ccda, pretty=True)

    msg = MIMEMultipart()
    xml = MIMEBase("application", "xop+xml")
    xml.set_payload(ccda_xml)
    msg.attach(xml)

    return msg.as_string()


def base64_xml(ccda: dict):
    """
    Takes CCDA and converts it to base64 enconded XML for sending via SOAP

    Args:
        ccda: dictionary created by convert bundle

    Returns:
        XOP infoset
    """

    ccda_xml = xmltodict.unparse(ccda, pretty=True)

    # base63 encode the xml
    ccda_64 = base64.b64encode(ccda_xml.encode("utf-8")).decode("utf-8")

    print("ccda64:", ccda_64)

    return ccda_64
