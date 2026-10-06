import json
from pathlib import Path

import pytest
import xmltodict
from fhirclient.models.allergyintolerance import AllergyIntolerance

from app.ccda.entries.allergy import allergy
from app.ccda.models.allergy import AllergyEntry


def resource(**changes):
    return AllergyIntolerance(
        {
            "resourceType": "AllergyIntolerance",
            "patient": {"reference": "Patient/test"},
            "clinicalStatus": "active",
            "verificationStatus": "confirmed",
            "assertedDate": "2024-02-03",
            "type": "allergy",
            "category": ["medication"],
            "code": {
                "coding": [
                    {"system": "https://fhir.nhs.uk/Id/read-codes", "code": "test", "display": "Local substance"},
                    {"system": "http://snomed.info/sct", "code": "123", "display": "Test substance"},
                ]
            },
            **changes,
        }
    )


def get_observation(result):
    return result.entry["act"]["entryRelationship"][0]["observation"]


def test_entry_retains_narrative_substance_and_translations_without_reaction():
    entry = resource(onsetDateTime="2020-01-02")
    original = entry.as_json()
    result = allergy(entry)
    assert result.row == [
        {"BR": ["Onset: 02/01/2020 <br />", "Asserted: 03/02/2024 <br />"]},
        "Test substance",
        {"BR": ["Clinical status: Active <br />", "Verification status: Confirmed <br />"]},
        "",
        "",
        "",
    ]
    obs = get_observation(result)
    assert obs["effectiveTime"]["low"]["@value"] == "20200102"
    assert obs["value"]["@code"] == "416098002"
    substance = obs["participant"][0]["participantRole"]["playingEntity"]["code"]
    assert substance["@xsi:type"] == "CE"
    assert substance["@code"] == "123"
    assert substance["translation"][0]["@code"] == "test"
    assert "entryRelationship" not in obs
    assert entry.as_json() == original
    assert AllergyEntry.model_validate(result.entry).act.entryRelationship[0].observation.participant


def test_all_reaction_manifestations_survive_xml_serialization():
    result = allergy(
        resource(
            reaction=[
                {
                    "onset": "2021-04-05",
                    "manifestation": [
                        {"coding": [{"system": "http://snomed.info/sct", "code": code, "display": code}]}
                        for code in ["1", "2"]
                    ],
                },
                {
                    "manifestation": [{"coding": [{"system": "http://snomed.info/sct", "code": "3"}]}],
                },
            ]
        )
    )
    parsed = xmltodict.parse(xmltodict.unparse({"entry": result.entry}))
    reactions = parsed["entry"]["act"]["entryRelationship"]["observation"]["entryRelationship"]
    reactions = [r for r in reactions if r["@typeCode"] == "MFST"]
    assert [r["observation"]["value"]["@code"] for r in reactions] == ["1", "2", "3"]
    assert all(r["@typeCode"] == "MFST" and r["@inversionInd"] == "true" for r in reactions)
    assert reactions[0]["observation"]["effectiveTime"]["low"]["@value"] == "20210405"
    assert reactions[2]["observation"]["effectiveTime"]["low"]["@nullFlavor"] == "UNK"


@pytest.mark.parametrize(
    "kind,category,expected",
    [
        ("allergy", ["food"], "414285001"),
        ("intolerance", ["medication"], "59037007"),
        ("intolerance", ["food"], "235719002"),
        ("allergy", ["environment"], "419199007"),
        (None, [], "420134006"),
    ],
)
def test_type_mapping(kind, category, expected):
    assert get_observation(allergy(resource(type=kind, category=category)))["value"]["@code"] == expected


def test_unknown_onset_is_not_assertion_date():
    assert get_observation(allergy(resource()))["effectiveTime"]["low"]["@nullFlavor"] == "UNK"


def test_onset_period_end_is_not_resolution_and_partial_dates_are_preserved():
    result = allergy(resource(onsetPeriod={"start": "2020", "end": "2021"}, assertedDate="2024"))
    interval = get_observation(result)["effectiveTime"]
    assert interval["low"]["@value"] == "2020"
    assert "high" not in interval
    assert result.row[0] == {"BR": ["Onset from: 2020 <br />", "Onset to: 2021 <br />", "Asserted: 2024 <br />"]}


def test_unknown_assertion_and_resolution_dates():
    result = allergy(resource(assertedDate=None, clinicalStatus="resolved"))
    assert result.row[0] == ""
    assert result.entry["act"]["effectiveTime"]["low"]["@nullFlavor"] == "UNK"
    assert get_observation(result)["effectiveTime"]["high"]["@nullFlavor"] == "UNK"


def test_existing_bundle_allergies_convert():
    bundle = json.loads((Path(__file__).parent / "fixtures/bundles/9692136744.json").read_text())
    entries = [item["resource"] for item in bundle["entry"] if item["resource"]["resourceType"] == "AllergyIntolerance"]
    assert entries
    for data in entries:
        result = allergy(AllergyIntolerance(data))
        assert AllergyEntry.model_validate(result.entry).act.entryRelationship
        assert len(result.row) == 6


def test_source_resource_id_is_preserved_across_conversions():
    source_id = "4b2a01f5-5833-4df4-84f7-4d82a624a919"
    entry = resource(id=source_id)
    for result in (allergy(entry), allergy(entry)):
        assert result.entry["act"]["id"][0]["@root"] == source_id
        assert get_observation(result)["id"][0]["@root"] == source_id


def test_missing_source_id_uses_generated_identifiers():
    result = allergy(resource())
    assert result.entry["act"]["id"][0]["@root"]
    assert get_observation(result)["id"][0]["@root"]


def test_notes_and_transfer_degraded_text_preserved_with_reactions():
    result = allergy(
        resource(
            code={
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        "code": "196461000000101",
                        "display": "Transfer-degraded drug allergy",
                    }
                ],
                "text": "Original allergy & details <unknown>",
            },
            note=[{"text": "Original source note"}, {"text": "Original source note"}, {"text": "Second note"}],
            reaction=[
                {"manifestation": [{"coding": [{"system": "http://snomed.info/sct", "code": "1", "display": "Rash"}]}]}
            ],
        )
    )
    notes = [
        "Original source note",
        "Original source note",
        "Second note",
        "Transfer degraded allergy text: Original allergy & details <unknown>",
    ]
    obs = get_observation(result)
    assert obs["entryRelationship"][0]["observation"]["value"]["@code"] == "1"
    comments = [r["act"] for r in obs["entryRelationship"] if "act" in r]
    assert [c["text"]["xmlText"] for c in comments] == notes
    assert all("<br" not in c["text"]["xmlText"] for c in comments)
    assert result.row[3] == "Rash"
    assert result.row[5] == {"BR": [f"{note} <br />" for note in notes]}
    restored = AllergyEntry.model_validate(result.entry)
    assert restored.model_dump(by_alias=True, exclude_none=True) == result.entry
    parsed = xmltodict.parse(xmltodict.unparse({"entry": result.entry}))
    parsed_comments = parsed["entry"]["act"]["entryRelationship"]["observation"]["entryRelationship"]
    assert [r["act"]["text"]["xmlText"] for r in parsed_comments if "act" in r] == notes


def test_fixture_notes_and_transfer_degraded_descriptions_are_preserved():
    bundle = json.loads((Path(__file__).parent / "fixtures/bundles/9692136744.json").read_text())
    degraded_count = 0
    for item in bundle["entry"]:
        data = item["resource"]
        if data["resourceType"] != "AllergyIntolerance":
            continue
        result = allergy(AllergyIntolerance(data))
        serialized = json.dumps(result.entry)
        narrative = json.dumps(result.row)
        for note in data.get("note", []):
            assert json.dumps(note["text"])[1:-1] in serialized
            assert json.dumps(note["text"])[1:-1] in narrative
        if any(c["code"] == "196461000000101" for c in data["code"]["coding"]):
            degraded_count += 1
            text = json.dumps("Transfer degraded allergy text: " + data["code"]["text"])[1:-1]
            assert text in serialized
            assert text in narrative
    assert degraded_count == 2


def test_observation_fallback_has_notes_column_and_retains_structured_notes():
    from fhirclient.models.observation import Observation

    from app.ccda.entries.observation_entry import observation_entry

    result = observation_entry(
        Observation(
            {
                "resourceType": "Observation",
                "id": "test",
                "status": "final",
                "code": {"coding": [{"code": "1", "display": "Allergy information"}]},
                "comment": "Original comment",
            }
        ),
        {},
        "Allergies and adverse reactions",
    )
    assert len(result.row) == 6
    assert result.row[1] == "Allergy information"
    assert result.row[2] == "Observation status: final"
    assert result.row[5] == "Original comment"
    assert "Original comment" in json.dumps(result.entry)


@pytest.mark.parametrize("severity,code", [("mild", "255604002"), ("moderate", "6736007"), ("severe", "24484000")])
def test_reaction_severity_survives_xml_and_model_roundtrip(severity, code):
    result = allergy(
        resource(
            reaction=[
                {
                    "severity": severity,
                    "manifestation": [
                        {"coding": [{"system": "http://snomed.info/sct", "code": "1", "display": "Rash"}]}
                    ],
                }
            ]
        )
    )
    assert result.row[3:] == ["Rash", severity.title(), ""]
    restored = AllergyEntry.model_validate(result.entry)
    assert restored.model_dump(by_alias=True, exclude_none=True) == result.entry
    parsed = xmltodict.parse(xmltodict.unparse({"entry": result.entry}))
    reaction = parsed["entry"]["act"]["entryRelationship"]["observation"]["entryRelationship"]["observation"]
    relation = reaction["entryRelationship"]
    assert relation["@typeCode"] == "SUBJ"
    assert relation["@inversionInd"] == "true"
    obs = relation["observation"]
    assert obs["templateId"]["@root"] == "2.16.840.1.113883.10.20.22.4.8"
    assert obs["code"]["@code"] == "SEV"
    assert obs["statusCode"]["@code"] == "completed"
    assert obs["value"]["@code"] == code
    assert obs["value"]["@codeSystem"] == "2.16.840.1.113883.6.96"


def test_severity_remains_associated_with_each_reaction():
    def manifestation(label):
        return {"coding": [{"system": "http://snomed.info/sct", "code": "1", "display": label}]}

    result = allergy(
        resource(
            reaction=[
                {"severity": "mild", "manifestation": [manifestation("Rash"), manifestation("Itching")]},
                {"severity": "severe", "manifestation": [manifestation("Rash")]},
                {"manifestation": [manifestation("Cough")]},
            ]
        )
    )
    # Preserve the established severity column; associations remain in structured XML.
    assert result.row[4] == "Mild; Severe"
    reactions = get_observation(result)["entryRelationship"]
    assert [r["observation"]["entryRelationship"][0]["observation"]["value"]["@code"] for r in reactions[:3]] == [
        "255604002",
        "255604002",
        "24484000",
    ]
    assert "entryRelationship" not in reactions[3]["observation"]


def test_existing_severity_fixture_is_preserved():
    bundle = json.loads((Path(__file__).parent / "fixtures/bundles/9690937286.json").read_text())
    data = next(
        item["resource"] for item in bundle["entry"] if item["resource"]["resourceType"] == "AllergyIntolerance"
    )
    result = allergy(AllergyIntolerance(data))
    assert data["reaction"][0]["severity"].title() in result.row[4]
    reaction = get_observation(result)["entryRelationship"][0]["observation"]
    assert (
        reaction["entryRelationship"][0]["observation"]["value"]["@displayName"]
        == data["reaction"][0]["severity"].title()
    )


@pytest.mark.parametrize(
    "source_code, display, target_code",
    [
        ("716186003", "No known allergy", "419199007"),
        ("409137002", "No known drug allergy", "416098002"),
        ("429625007", "No known food allergy", "414285001"),
    ],
)
def test_explicit_no_known_allergy_uses_native_negation(source_code, display, target_code):
    entry = resource(
        id="absence-record",
        code={"coding": [{"system": "http://snomed.info/sct", "code": source_code, "display": display}]},
        note=[{"text": "Source assertion"}],
        recorder={"display": "Recording clinician"},
        asserter={"display": "Patient"},
    )
    original = entry.as_json()
    result = allergy(entry)
    obs = xmltodict.parse(xmltodict.unparse({"entry": result.entry}))["entry"]["act"]["entryRelationship"][
        "observation"
    ]
    assert obs["@negationInd"] == "true"
    assert obs["value"]["@code"] == target_code
    assert obs["value"]["@codeSystem"] == "2.16.840.1.113883.6.96"
    assert obs["participant"]["participantRole"]["playingEntity"]["code"] == {"@xsi:type": "CE", "@nullFlavor": "NA"}
    assert obs["id"]["@root"] == "absence-record"
    assert obs["author"]["assignedAuthor"]["assignedPerson"]["name"] == "Recording clinician"
    assert obs["informant"]["relatedEntity"]["relatedPerson"]["name"] == "Patient"
    assert obs["entryRelationship"]["act"]["text"]["xmlText"] == "Source assertion"
    assert result.entry["act"]["effectiveTime"]["low"]["@value"] == "20240203"
    assert result.row[1] == display
    assert AllergyEntry.model_validate(result.entry).model_dump(by_alias=True, exclude_none=True) == result.entry
    assert entry.as_json() == original


@pytest.mark.parametrize(
    "system, code, display",
    [
        ("https://provider.test/codes", "716186003", "No known allergy"),
        ("http://snomed.info/sct", "123", "No known allergy"),
        ("http://snomed.info/sct", "123", "Positive allergy"),
    ],
)
def test_absence_is_not_inferred_from_text_or_other_code_systems(system, code, display):
    result = allergy(resource(code={"coding": [{"system": system, "code": code, "display": display}]}))
    obs = get_observation(result)
    assert "@negationInd" not in obs
    assert obs["participant"][0]["participantRole"]["playingEntity"]["code"]["@code"] == code
