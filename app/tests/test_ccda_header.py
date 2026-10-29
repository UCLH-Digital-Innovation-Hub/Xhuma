"""Verify the composed header at its XML serialization boundary."""

from datetime import datetime
from xml.etree import ElementTree as ET

import pytest
import xmltodict
from fhirclient.models.patient import Patient

from app.ccda.header import document_header
from app.ccda.models.header import ClinicalDocumentHeader
from app.version import VERSION

NS = {"cda": "urn:hl7-org:v3"}


@pytest.mark.parametrize("birth_date, expected_low", [(None, {"nullFlavor": "UNK"}), ("1980-02", {"value": "198002"})])
def test_document_header_serializes_author_device_and_service_period(birth_date, expected_low):
    patient = Patient({"birthDate": birth_date} if birth_date else {})
    header = document_header(patient, {}, generated_at=datetime(2026, 10, 19, 23, 59, 59)).to_xml_dict()
    assert ClinicalDocumentHeader.model_validate(header).to_xml_dict() == header
    xml = xmltodict.unparse({"ClinicalDocument": header})
    root = ET.fromstring(xml)

    # The models must emit attributes and text, never internal Python field names.
    assert "resource_type" not in xml
    assert root.find("cda:typeId", NS).attrib == {"root": "2.16.840.1.113883.1.3", "extension": "POCD_HD000040"}
    assert root.find("cda:effectiveTime", NS).get("value") == "20261019235959"
    assert root.findtext("cda:title", namespaces=NS) == "GP Connect: Access Record Structured"
    assert root.find("cda:author/cda:time", NS).get("value") == "20261019"

    author = root.find("cda:author/cda:assignedAuthor", NS)
    assert author.find("cda:id", NS).get("nullFlavor") == "UNK"
    assert author.find("cda:addr", NS).get("nullFlavor") == "NA"
    assert author.find("cda:telecom", NS).get("nullFlavor") == "NA"
    device = author.find("cda:assignedAuthoringDevice", NS)
    assert device.findtext("cda:manufacturerModelName", namespaces=NS) == "Xhuma"
    assert device.findtext("cda:softwareName", namespaces=NS) == f"Xhuma v{VERSION}"

    event = root.find("cda:documentationOf/cda:serviceEvent", NS)
    assert event.get("classCode") == "PCPR"
    low = event.find("cda:effectiveTime/cda:low", NS)
    assert {key: value for key, value in low.attrib.items() if not key.startswith("{")} == expected_low
    assert event.find("cda:effectiveTime/cda:high", NS).get("value") == "20261019"

    elements = [element.tag.removeprefix("{urn:hl7-org:v3}") for element in root]
    assert elements.index("title") < elements.index("effectiveTime") < elements.index("recordTarget")
    assert elements.index("recordTarget") < elements.index("author") < elements.index("documentationOf")
