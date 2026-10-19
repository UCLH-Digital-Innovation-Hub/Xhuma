"""Regression coverage at the serialized CCDA and ITI-55 boundaries."""

import base64
import xml.etree.ElementTree as ET

import pytest
from fhirclient.models.bundle import Bundle
from pydantic import ValidationError

from app.ccda.convert_mime import base64_xml
from app.ccda.demographics import patient_record_target
from app.ccda.fhir2ccda import convert_bundle
from app.ccda.models.demographics import PatientRole, RecordTarget
from app.soap.responses.iti_55 import iti_55_response

NS = {"cda": "urn:hl7-org:v3"}


async def converted_patient(source, target):
    """Exercise the real FHIR parser and both production XML serializers."""
    patient = {"resourceType": "Patient", "id": "synthetic", **source}
    if target == "ccda":
        bundle = Bundle({"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": patient}]})
        ccda = await convert_bundle(bundle, {})
        # Typed header must round-trip through the emitted dictionary.
        header = ccda["ClinicalDocument"]["recordTarget"]
        assert RecordTarget.model_validate(header).to_xml_dict() == header
        xml = base64.b64decode(base64_xml(ccda))
        root = ET.fromstring(xml)
        return root.find("cda:recordTarget/cda:patientRole", NS), "patient"
    xml = await iti_55_response("synthetic-message", patient, {"queryId": {"@root": "synthetic-query"}})
    root = ET.fromstring(xml)
    return root.find(".//cda:subject1/cda:patient", NS), "patientPerson"


@pytest.mark.parametrize("target", ["ccda", "iti55"])
@pytest.mark.parametrize(
    "source, given, family, text, unknown",
    [
        ({}, [], [], None, True),
        ({"name": []}, [], [], None, True),
        ({"name": [{}]}, [], [], None, True),
        ({"name": [{"family": "Synthetic"}]}, [], ["Synthetic"], None, False),
        ({"name": [{"given": ["Alex", "Robin"]}]}, ["Alex", "Robin"], [], None, False),
        ({"name": [{"text": "A single unstructured name"}]}, [], [], "A single unstructured name", False),
    ],
)
@pytest.mark.asyncio
async def test_partial_and_absent_names_survive_conversion(target, source, given, family, text, unknown):
    role, person_tag = await converted_patient(source, target)
    name = role.find(f"cda:{person_tag}/cda:name", NS)
    assert [part.text for part in name.findall("cda:given", NS)] == given
    assert [part.text for part in name.findall("cda:family", NS)] == family
    assert (name.get("nullFlavor") == "UNK") == unknown
    if text:
        assert name.text == text
    person = role.find(f"cda:{person_tag}", NS)
    assert person.find("cda:birthTime", NS).attrib == {"nullFlavor": "UNK"}
    assert person.find("cda:administrativeGenderCode", NS).attrib == {"nullFlavor": "UNK"}


@pytest.mark.parametrize("target", ["ccda", "iti55"])
@pytest.mark.asyncio
async def test_all_names_and_parts_survive_xml_escaping_and_preference_order(target):
    source = {
        "name": [
            {"use": "official", "family": "Official"},
            {
                "use": "usual",
                "prefix": ["Dr", "Professor"],
                "given": ["Alex & Robin", "<Middle>"],
                "family": "O'Synthetic",
                "suffix": ["Jr", "PhD"],
                "period": {"start": "2020-01-01", "end": "2024-12-31"},
            },
            {"use": "nickname", "text": "Friendly name"},
        ]
    }
    role, person_tag = await converted_patient(source, target)
    names = role.findall(f"cda:{person_tag}/cda:name", NS)
    assert len(names) == 3
    assert [part.text for part in names[0].findall("cda:given", NS)] == ["Alex & Robin", "<Middle>"]
    assert [part.text for part in names[0].findall("cda:prefix", NS)] == ["Dr", "Professor"]
    assert [part.text for part in names[0].findall("cda:suffix", NS)] == ["Jr", "PhD"]
    assert names[0].find("cda:validTime/cda:low", NS).get("value") == "20200101"
    assert names[0].find("cda:validTime/cda:high", NS).get("value") == "20241231"
    assert names[1].get("use") == "L"
    assert names[2].text.strip() == "Friendly name"
    assert names[2].get("use") == "P"


@pytest.mark.parametrize("target", ["ccda", "iti55"])
@pytest.mark.asyncio
async def test_partial_repeated_and_text_only_addresses(target):
    source = {
        "address": [
            {"postalCode": "TEST 1AA"},
            {"use": "temp", "text": "Address known only as text"},
            {
                "use": "work",
                "line": ["1 Synthetic Street", "Suite & Co"],
                "city": "Test City",
                "district": "Test District",
                "state": "Test State",
                "country": "GB",
                "period": {"end": "2025-01-01"},
            },
            {},
        ]
    }
    role, _ = await converted_patient(source, target)
    address_parent = role if target == "ccda" else role.find("cda:patientPerson", NS)
    addresses = address_parent.findall("cda:addr", NS)
    assert len(addresses) == 4
    assert addresses[0].find("cda:postalCode", NS).text == "TEST 1AA"
    assert addresses[0].get("use") is None
    assert addresses[0].find("cda:streetAddressLine", NS) is None
    assert addresses[1].get("use") == "TMP"
    assert addresses[1].text.strip() == "Address known only as text"
    assert addresses[2].get("use") == "WP"
    assert [part.text for part in addresses[2].findall("cda:streetAddressLine", NS)] == [
        "1 Synthetic Street",
        "Suite & Co",
    ]
    assert addresses[2].find("cda:county", NS).text == "Test District"
    assert addresses[2].find("cda:country", NS).text == "GB"
    assert addresses[2].find("cda:useablePeriod/cda:low", NS) is None
    assert addresses[2].find("cda:useablePeriod/cda:high", NS).get("value") == "20250101"
    assert addresses[3].attrib == {"nullFlavor": "UNK"}


@pytest.mark.parametrize("target", ["ccda", "iti55"])
@pytest.mark.asyncio
async def test_missing_addresses_and_provider_do_not_fail(target):
    role, _ = await converted_patient({}, target)
    assert role.find("cda:providerOrganization", NS) is None
    if target == "ccda":
        assert role.find("cda:addr", NS).attrib == {"nullFlavor": "UNK"}
        assert role.find("cda:id", NS).attrib == {"nullFlavor": "UNK"}
    else:
        assert role.find("cda:addr", NS) is None


def test_provider_addresses_identifiers_and_absolute_reference():
    source = {
        "managingOrganization": {"reference": "https://synthetic.test/fhir/Organization/1"},
        "identifier": [
            {"system": "urn:oid:1.2.3", "value": "local-id"},
            {"system": "https://fhir.nhs.uk/Id/nhs-number", "value": "synthetic-nhs"},
        ],
    }
    organization = {
        "identifier": [{"system": "https://fhir.nhs.uk/Id/ods-organization-code", "value": "TEST"}],
        "address": [{"city": "Test City"}, {"line": ["Second Address"]}],
    }
    role = patient_record_target(source, {"Organization/1": organization}).patientRole
    assert [identifier.extension for identifier in role.id] == ["local-id", "synthetic-nhs"]
    assert role.id[1].root == "2.16.840.1.113883.2.1.4.1"
    assert len(role.providerOrganization.addr) == 2
    assert role.providerOrganization.name == []
    assert role.providerOrganization.id[0].root == "2.16.840.1.113883.2.1.4.3"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gp, expected_id, expected_name",
    [
        ({"identifier": {"value": "GP1"}}, {"root": "2.16.840.1.113883.2.1.4.3", "extension": "GP1"}, None),
        ({"display": "Known GP"}, {"nullFlavor": "UNK"}, "Known GP"),
    ],
)
async def test_iti55_gp_identifier_and_display_only_provider(gp, expected_id, expected_name):
    role, _ = await converted_patient({"generalPractitioner": [gp]}, "iti55")
    provider = role.find("cda:providerOrganization", NS)
    assert provider.find("cda:id", NS).attrib == expected_id
    assert provider.find("cda:id/cda:id", NS) is None
    name = provider.find("cda:name", NS)
    assert (name.text if name is not None else None) == expected_name


@pytest.mark.asyncio
async def test_iti55_ambiguous_gp_organisations_are_not_arbitrarily_selected():
    role, _ = await converted_patient(
        {"generalPractitioner": [{"identifier": {"value": "GP1"}}, {"display": "Known GP"}]}, "iti55"
    )
    assert role.find("cda:providerOrganization", NS) is None


@pytest.mark.parametrize("count", [0, 2])
@pytest.mark.asyncio
async def test_ambiguous_or_absent_patient_fails_explicitly(count):
    source = Bundle({"resourceType": "Bundle", "type": "searchset"})
    from fhirclient.models.bundle import BundleEntry

    source.entry = [BundleEntry({"resource": {"resourceType": "Patient", "id": str(i)}}) for i in range(count)]
    with pytest.raises(ValueError, match="Expected exactly one Patient"):
        await convert_bundle(source, {})


def test_header_models_reject_empty_required_collections():
    with pytest.raises(ValidationError):
        PatientRole(id=[], addr=[], patient={"name": [], "birthTime": {"@nullFlavor": "UNK"}})
