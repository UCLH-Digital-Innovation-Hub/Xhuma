import pytest
from fhirclient.models import codeableconcept
from app.ccda.helpers import (
    extract_original_term,
    convert_codeable_concept,
    FHIRValidationError,
)


def create_snomed_extension(display_text):
    return [
        {
            "url": "https://fhir.hl7.org.uk/STU3/StructureDefinition/Extension-coding-sctdescid",
            "extension": [{"url": "descriptionDisplay", "valueString": display_text}],
        }
    ]


def test_extract_original_term_a_text_wins():
    concept = codeableconcept.CodeableConcept(
        {
            "text": "Concept Text",
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "123",
                    "display": "Coding Display",
                    "userSelected": True,
                    "extension": create_snomed_extension("Extension Display"),
                }
            ],
        }
    )
    assert extract_original_term(concept) == "Concept Text"


def test_extract_original_term_b_selected_snomed_ext_wins():
    concept = codeableconcept.CodeableConcept(
        {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "123",
                    "display": "Coding Display",
                    "userSelected": True,
                    "extension": create_snomed_extension("Extension Display"),
                }
            ]
        }
    )
    assert extract_original_term(concept) == "Extension Display"


def test_extract_original_term_c_one_coding_no_user_selected_snomed_ext_wins():
    concept = codeableconcept.CodeableConcept(
        {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "123",
                    "display": "Coding Display",
                    "extension": create_snomed_extension("Extension Display"),
                }
            ]
        }
    )
    assert extract_original_term(concept) == "Extension Display"


def test_extract_original_term_d_selected_no_ext_display_wins():
    concept = codeableconcept.CodeableConcept(
        {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "123",
                    "display": "Coding Display",
                    "userSelected": True,
                }
            ]
        }
    )
    assert extract_original_term(concept) == "Coding Display"


def test_extract_original_term_e_one_coding_no_user_selected_no_ext_display_wins():
    concept = codeableconcept.CodeableConcept(
        {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "123",
                    "display": "Coding Display",
                }
            ]
        }
    )
    assert extract_original_term(concept) == "Coding Display"


def test_extract_original_term_f_multiple_codings_no_user_selected_no_text_fails():
    concept = codeableconcept.CodeableConcept(
        {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "123",
                    "display": "Coding Display 1",
                },
                {
                    "system": "http://read.info/readv2",
                    "code": "456",
                    "display": "Coding Display 2",
                },
            ]
        }
    )
    assert extract_original_term(concept) is None
    with pytest.raises(FHIRValidationError):
        convert_codeable_concept(concept)
