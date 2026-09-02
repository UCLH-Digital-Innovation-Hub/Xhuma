from fhirclient.models import codeableconcept
from app.ccda.helpers import convert_codeable_concept, FHIRValidationError
import pytest
from app.ccda.models.datatypes import CD


def test_single_snomed_code_only():
    codings = [
        {
            "system": "http://snomed.info/sct",
            "code": "1102181000000102",
            "display": "Immunisations",
        }
    ]
    concept = codeableconcept.CodeableConcept(
        {"coding": codings, "text": "Immunisations"}
    )
    result = convert_codeable_concept(concept)
    assert result.code == "1102181000000102"
    assert result.codeSystem == "2.16.840.1.113883.6.96"
    assert result.translation is None
    assert result.originalText == "Immunisations"


def test_snomed_priority_and_translation():
    codings = [
        {
            "system": "http://snomed.info/sct",
            "code": "325242002",
            "display": "Gliclazide 80mg tables",
        },
        {
            "system": "https://fhir.hl7.org.uk/Id/multilex-drug-codes",
            "code": "03716001",
            "display": "Gliclazide 80mg tablets",
            "userSelected": True,
        },
    ]
    concept = codeableconcept.CodeableConcept({"coding": codings})
    result = convert_codeable_concept(concept)
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
    assert result.originalText == "Gliclazide 80mg tables"


def test_unsupported_coding_fallback_to_oth():
    codings = [
        {
            "system": "http://unknown.system",
            "code": "123",
            "display": "Unknown thing",
        }
    ]
    concept = codeableconcept.CodeableConcept(
        {"coding": codings, "text": "Original messy text"}
    )
    result = convert_codeable_concept(concept)
    assert result.nullFlavor == "OTH"
    assert result.originalText == "Original messy text"
    assert result.code is None


def test_unsupported_coding_with_domain_degradation():
    codings = [
        {
            "system": "http://unknown.system",
            "code": "123",
        }
    ]
    concept = codeableconcept.CodeableConcept(
        {"coding": codings, "text": "Degraded thing"}
    )
    degraded_cd = CD(
        code="999",
        codeSystem="1.2.3",
        codeSystemName="DegradationSystem",
        displayName="Degraded",
    )
    result = convert_codeable_concept(concept, degradation_code=degraded_cd)
    assert result.code == "999"
    assert result.codeSystem == "1.2.3"
    assert result.originalText == "Degraded thing"


def test_no_safe_original_term_raises_error():
    codings = [
        {
            "system": "http://unknown.system",
            "code": "123",
        }
    ]
    concept = codeableconcept.CodeableConcept({"coding": codings})
    with pytest.raises(FHIRValidationError, match="without original text"):
        convert_codeable_concept(concept)
