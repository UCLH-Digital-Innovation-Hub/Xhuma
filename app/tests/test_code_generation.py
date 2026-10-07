import pytest
from fhirclient.models import coding

from app.ccda.helpers import code_with_translations


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


@pytest.mark.parametrize("snomed_first", [True, False])
def test_snomed_priority_and_translation(snomed_first):

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
    if not snomed_first:
        codings.reverse()
    source_order = list(codings)
    source_values = [item.as_json() for item in codings]

    result = code_with_translations(codings)

    assert codings == source_order
    assert [item.as_json() for item in codings] == source_values
    assert result.code == "325242002"
    assert result.codeSystemName == "http://snomed.info/sct"
    assert result.codeSystem == "2.16.840.1.113883.6.96"
    assert result.translation is not None
    assert result.translation[0].code == "03716001"
    assert result.translation[0].codeSystemName == "https://fhir.hl7.org.uk/Id/multilex-drug-codes"
    assert result.translation[0].codeSystem == "2.16.840.1.113883.2.1.6.4"
