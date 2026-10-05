import pytest
import xmltodict
from pydantic import ValidationError

from app.ccda.models.allergy import (
    Allergy,
    AllergyEntry,
    AllergyIntoleranceObservation,
    AllergyObservation,
    AllergyReaction,
    ReactionObservation,
)
from app.ccda.models.datatypes import IVXB_TS


def observation_data():
    return {
        "effectiveTime": {"low": {"@nullFlavor": "UNK"}},
        "value": {"@code": "419199007", "@codeSystem": "2.16.840.1.113883.6.96"},
        "participant": [{"participantRole": {"playingEntity": {"code": {"@code": "test-substance"}}}}],
    }


def test_allergy_entry_xml_preserves_substance_reaction_and_unknown_dates():
    data = observation_data()
    data["effectiveTime"]["high"] = {"@nullFlavor": "UNK"}
    data["negationInd"] = False
    data["entryRelationship"] = [
        AllergyReaction(observation=ReactionObservation(value={"@code": "test-manifestation"}))
    ]
    obs = AllergyIntoleranceObservation(**data)
    entry = AllergyEntry(act=Allergy(entryRelationship=[AllergyObservation(observation=obs)]))
    dumped = entry.model_dump(by_alias=True, exclude_none=True)
    xml = xmltodict.unparse({"entry": dumped})
    parsed = xmltodict.parse(xml)["entry"]["act"]["entryRelationship"]["observation"]
    entity = parsed["participant"]["participantRole"]
    assert entity["@classCode"] == "MANU"
    assert entity["playingEntity"]["@classCode"] == "MMAT"
    assert entity["playingEntity"]["code"]["@code"] == "test-substance"
    assert parsed["effectiveTime"]["high"]["@nullFlavor"] == "UNK"
    assert parsed["@negationInd"] == "false"
    assert parsed["value"]["@code"] == "419199007"
    reaction = parsed["entryRelationship"]
    assert reaction["@typeCode"] == "MFST"
    assert reaction["@inversionInd"] == "true"
    assert reaction["observation"]["value"]["@code"] == "test-manifestation"
    assert reaction["observation"]["statusCode"]["@code"] == "completed"
    restored = AllergyEntry.model_validate(dumped)
    assert restored.model_dump(by_alias=True, exclude_none=True) == dumped


@pytest.mark.parametrize(
    "changes",
    [
        {"effectiveTime": None},
        {"effectiveTime": {}},
        {"id": []},
        {"value": None},
        {"code": {"@code": "wrong"}},
        {"statusCode": {"@code": "active"}},
        {"@classCode": "ACT"},
        {"participant": [{"participantRole": {"code": {"@code": "misplaced"}}}]},
    ],
)
def test_invalid_observation_is_rejected(changes):
    with pytest.raises(ValidationError):
        AllergyIntoleranceObservation(**(observation_data() | changes))


def test_type_and_interval_must_be_supplied():
    with pytest.raises(ValidationError):
        AllergyIntoleranceObservation()


def test_identifiers_are_unique_and_interpretations_repeat():
    first = AllergyIntoleranceObservation(**observation_data(), interpretationCode=[{"@code": "A"}, {"@code": "N"}])
    second = AllergyIntoleranceObservation(**observation_data())
    assert first.id[0].root != second.id[0].root
    assert len(first.interpretationCode) == 2


@pytest.mark.parametrize("changes", [{"typeCode": "SUBJ"}, {"inversionInd": False}])
def test_reaction_relationship_constraints(changes):
    with pytest.raises(ValidationError):
        AllergyReaction(observation=ReactionObservation(value={"@code": "reaction"}), **changes)


@pytest.mark.parametrize("key", ["nullFlavor", "@nullFlavor"])
def test_unknown_time_serializes_null_flavor_as_attribute(key):
    assert IVXB_TS(**{key: "UNK"}).model_dump(by_alias=True, exclude_none=True)["@nullFlavor"] == "UNK"
