import pytest
from fhirclient.models import coding

from app.ccda.helpers import code_with_translations
from app.ccda.models.datatypes import CD


def test_single_snomed_code_only():
    codings = [
        coding.Coding(
            {
                "system": "http://snomed.info/sct",
                "code": "1102181000000102",
                "display": "Immunisations",
            }
        )
    ]
    result = code_with_translations(codings)
    assert result.code == "1102181000000102"
    assert result.codeSystem == "2.16.840.1.113883.6.96"
    assert result.translation is None


def test_snomed_priority_and_translation():

    codings = [
        coding.Coding(
            {
                "system": "http://snomed.info/sct",
                "code": "325242002",
                "display": "Gliclazide 80mg tables",
            }
        ),
        coding.Coding(
            {
                "system": "https://fhir.hl7.org.uk/Id/multilex-drug-codes",
                "code": "03716001",
                "display": "Gliclazide 80mg tablets",
                "userSelected": True,
            },
        ),
    ]
    result = code_with_translations(codings)
    assert result.code == "325242002"
    assert result.codeSystemName == "http://snomed.info/sct"
    assert result.codeSystem == "2.16.840.1.113883.6.96"
    assert result.translation is not None
    assert result.translation[0].code == "03716001"
    assert (
        result.translation[0].codeSystemName
        == "https://fhir.hl7.org.uk/Id/multilex-drug-codes"
    )
    assert result.translation[0].codeSystem == "2.16.840.1.113883.2.1.6.4"
