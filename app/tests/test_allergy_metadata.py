import base64

import pytest
import xmltodict
from fhirclient.models.bundle import Bundle
from fhirclient.models.patient import Patient
from fhirclient.models.practitioner import Practitioner

from app.ccda.convert_mime import base64_xml
from app.ccda.entries.allergy import allergy
from app.ccda.fhir2ccda import convert_bundle
from app.ccda.models.allergy import AllergyEntry, AllergyInformant
from app.tests.test_allergy_entry import get_observation, resource
from app.tests.test_ccda_names import create_bundle_dict


def practitioner(identifier, name):
    """Build a named practitioner fixture with a source identifier for provenance tests."""
    return Practitioner(
        {"id": identifier, "name": [{"text": name}], "identifier": [{"system": "urn:oid:1.2.3", "value": identifier}]}
    )


def test_asserter_recorder_and_note_author_are_distinct_and_survive_xml():
    index = {
        f"Practitioner/{n}": practitioner(n, label)
        for n, label in [("1", "Dr Asserter"), ("2", "Dr Recorder"), ("3", "Dr Note Author")]
    }
    entry = resource(
        asserter={"reference": "Practitioner/1"},
        recorder={"reference": "Practitioner/2"},
        note=[
            {
                "text": "Clinical note",
                "authorReference": {"reference": "Practitioner/3"},
                "time": "2024-02-03T10:11:12+01:00",
            }
        ],
    )
    result = allergy(entry, index)
    parsed = xmltodict.parse(xmltodict.unparse({"entry": result.entry}))["entry"]["act"]["entryRelationship"][
        "observation"
    ]
    assert parsed["informant"]["assignedEntity"]["assignedPerson"]["name"] == "Dr Asserter"
    assert parsed["author"]["assignedAuthor"]["assignedPerson"]["name"] == "Dr Recorder"
    assert parsed["author"]["time"]["@nullFlavor"] == "UNK"  # Assertion time is not recorder time.
    comment = parsed["entryRelationship"]["act"]
    assert comment["author"]["assignedAuthor"]["assignedPerson"]["name"] == "Dr Note Author"
    assert comment["author"]["time"]["@value"] == "20240203101112+0100"
    assert result.row[5]["BR"] == ["Clinical note <br />", "Asserter: Dr Asserter <br />"]
    assert "Dr Recorder" not in str(result.row)
    assert (
        list(parsed).index("author")
        < list(parsed).index("informant")
        < list(parsed).index("participant")
        < list(parsed).index("entryRelationship")
    )
    assert AllergyEntry.model_validate(result.entry).model_dump(by_alias=True, exclude_none=True) == result.entry


@pytest.mark.parametrize("reference", ["Practitioner/1", "https://provider.test/fhir/Practitioner/1", "#1"])
def test_asserter_reference_resolution(reference):
    p = practitioner("1", "Dr Source")
    entry = resource(asserter={"reference": reference}, contained=[p.as_json()] if reference.startswith("#") else None)
    result = allergy(entry, {"Practitioner/1": p})
    assert result.row[5]["BR"] == ["Asserter: Dr Source <br />"]


def test_unresolved_asserter_retains_reference_without_inventing_name():
    result = allergy(resource(asserter={"reference": "Practitioner/missing"}))
    entity = get_observation(result)["informant"][0]["assignedEntity"]
    assert "assignedPerson" not in entity
    assert entity["id"][0]["@root"] == "Practitioner/missing"
    assert result.row[5]["BR"] == ["Asserter: Practitioner/missing <br />"]


def test_display_only_asserter_is_an_unidentified_informant():
    result = allergy(resource(asserter={"display": "Patient's mother"}))
    informant = get_observation(result)["informant"][0]
    assert "assignedEntity" not in informant
    assert informant["relatedEntity"]["relatedPerson"]["name"] == "Patient's mother"
    with pytest.raises(ValueError):
        AllergyInformant()


def test_patient_can_be_the_asserter_and_recorder_can_be_display_only():
    patient = Patient({"id": "test", "name": [{"given": ["Test"], "family": "Patient"}]})
    entry = resource(asserter={"reference": "Patient/test"}, recorder={"display": "Dr Recorder"})
    result = allergy(entry, {"Patient/test": patient})
    obs = get_observation(result)
    assert obs["informant"][0]["assignedEntity"]["assignedPerson"]["name"] == "Test Patient"
    assert obs["author"][0]["assignedAuthor"]["id"][0]["@nullFlavor"] == "UNK"
    assert AllergyEntry.model_validate(result.entry).model_dump(by_alias=True, exclude_none=True) == result.entry


def test_statuses_and_dates_are_labelled_without_inventing_missing_values():
    entry = resource(
        verificationStatus="unconfirmed",
        criticality="high",
        onsetDateTime="1978-02-07T08:30:00Z",
        assertedDate="1978-02-10T12:00:00+01:00",
        lastOccurrence="1980-06",
        reaction=[{"onset": "1980-06-02", "manifestation": [{"coding": [{"code": "1", "display": "Rash"}]}]}],
    )
    result = allergy(entry)
    assert result.row[0]["BR"] == [
        "Onset: 07/02/1978 <br />",
        "Asserted: 10/02/1978 <br />",
        "Last occurrence: 1980-06 <br />",
        "Reaction 1 onset: 02/06/1980 <br />",
    ]
    assert result.row[2]["BR"] == [
        "Clinical status: Active <br />",
        "Verification status: Unconfirmed <br />",
        "Criticality: High <br />",
    ]
    assert get_observation(result)["effectiveTime"]["low"]["@value"] == "19780207083000+0000"
    assert result.entry["act"]["effectiveTime"]["low"]["@value"] == "19780210120000+0100"
    missing = allergy(
        resource(
            clinicalStatus=None, verificationStatus="entered-in-error", assertedDate=None, type=None, category=None
        )
    )
    assert missing.row[0] == ""
    assert missing.row[2] == {"BR": ["Verification status: Entered in error <br />"]}
    assert "informant" not in get_observation(missing)
    assert "author" not in get_observation(missing)


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"onsetDateTime": "1980"}, "Onset: 1980"),
        ({"onsetDateTime": "1980-06"}, "Onset: 1980-06"),
        ({"onsetString": "Since childhood"}, "Onset: Since childhood"),
        ({"onsetAge": {"value": 5, "unit": "years"}}, "Onset age: 5 years"),
        ({"onsetRange": {"low": {"value": 5, "unit": "years"}}}, "Onset age from: 5 years"),
    ],
)
def test_onset_precision_and_alternatives(changes, expected):
    result = allergy(resource(assertedDate=None, **changes))
    assert result.row[0]["BR"] == [expected + " <br />"]


def test_unmapped_extensions_are_omitted_while_native_notes_and_identifiers_remain():
    entry = resource(
        id="metadata-test",
        identifier=[{"system": "https://provider.test/ids", "value": "source-id"}],
        category=["food", "environment"],
        extension=[{"url": "https://provider.test/custom", "valueString": "a & b <br /> literal"}],
        note=[{"text": "Repeated note", "authorString": "Named author", "time": "2024-03-01T09:00:00Z"}] * 2,
        code={
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "123",
                    "display": "Original code",
                    "extension": [{"url": "https://provider.test/description-id", "valueString": "123456"}],
                }
            ],
            "text": "Original free text",
        },
    )
    original = entry.as_json()
    result = allergy(entry)
    delivered = base64.b64decode(base64_xml({"entry": result.entry}))
    observation = xmltodict.parse(delivered)["entry"]["act"]["entryRelationship"]["observation"]
    assert b"FHIR AllergyIntolerance." not in delivered
    assert b"application/json" not in delivered
    assert b"https://provider.test/custom" not in delivered
    assert b"123456" not in delivered
    assert observation["id"][1] == {"@xsi:type": "II", "@root": "https://provider.test/ids", "@extension": "source-id"}
    assert entry.as_json() == original
    comments = [r["act"] for r in observation["entryRelationship"] if "act" in r]
    assert len(comments) == 2
    assert all(c["text"]["xmlText"] == "Repeated note" for c in comments)


def test_provenance_identifier_uses_source_url_as_root():
    p = Practitioner({"id": "1", "identifier": [{"system": "https://provider.test/staff", "value": "staff-id"}]})
    result = allergy(resource(asserter={"reference": "Practitioner/1"}), {"Practitioner/1": p})
    identifiers = get_observation(result)["informant"][0]["assignedEntity"]["id"]
    assert identifiers == [{"@xsi:type": "II", "@root": "https://provider.test/staff", "@extension": "staff-id"}]


@pytest.mark.asyncio
async def test_bundle_orders_entries_and_rows_by_onset_with_stable_ties_and_unknowns_last(monkeypatch):
    monkeypatch.setenv("GP_CONNECT_INCLUDE_MEDICATION", "false")
    monkeypatch.setenv("GP_CONNECT_INCLUDE_PROBLEMS", "false")
    data = create_bundle_dict([{"use": "usual", "given": ["Test"], "family": "Patient"}])
    records = [
        resource(id="unknown-first", assertedDate="1900"),
        resource(id="recent", onsetDateTime="2020-01-02"),
        resource(id="partial", onsetDateTime="1970"),
        resource(id="old", onsetPeriod={"start": "1960-02", "end": "1960-03"}),
        resource(id="tie", onsetDateTime="2020-01-02"),
        resource(id="unknown-last", onsetString="Childhood"),
    ]
    for r in records:
        r.code.coding[-1].display = r.id
        data["entry"].append({"resource": r.as_json()})
    data["entry"].append(
        {
            "resource": {
                "resourceType": "List",
                "id": "allergies",
                "status": "current",
                "mode": "snapshot",
                "title": "Allergies and adverse reactions",
                "entry": [{"item": {"reference": f"AllergyIntolerance/{r.id}"}} for r in records],
            }
        }
    )
    b = Bundle(data)
    index = {f"{e.resource.resource_type}/{e.resource.id}": e.resource for e in b.entry}
    ccda = await convert_bundle(b, index)
    section = next(
        c["section"]
        for c in ccda["ClinicalDocument"]["component"]["structuredBody"]["component"]
        if c["section"]["code"]["@code"] == "48765-2"
    )
    expected = ["old", "partial", "recent", "tie", "unknown-first", "unknown-last"]
    assert [e["act"]["id"][0]["@root"] for e in section["entry"]] == expected
    assert [r["td"][1] for r in section["text"]["table"]["tbody"]["tr"]] == expected
    assert section["text"]["table"]["thead"]["tr"]["th"] == [
        "Dates",
        "Description",
        "Status",
        "Reaction",
        "Severity",
        "Notes",
    ]
    assert all(len(r["td"]) == 6 for r in section["text"]["table"]["tbody"]["tr"])


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [True, False])
async def test_empty_and_no_known_allergy_sections_match_new_columns(empty):
    data = create_bundle_dict([{"use": "usual", "given": ["Test"], "family": "Patient"}])
    data["entry"].append(
        {
            "resource": {
                "resourceType": "List",
                "id": "allergies",
                "status": "current",
                "mode": "snapshot",
                "title": "Allergies and adverse reactions",
                "entry": [] if empty else [{"item": {"reference": "Observation/none"}}],
            }
        }
    )
    if not empty:
        data["entry"].append(
            {
                "resource": {
                    "resourceType": "Observation",
                    "id": "none",
                    "status": "final",
                    "code": {
                        "coding": [
                            {"system": "http://snomed.info/sct", "code": "716186003", "display": "No known allergy"}
                        ]
                    },
                }
            }
        )
    b = Bundle(data)
    index = {f"{e.resource.resource_type}/{e.resource.id}": e.resource for e in b.entry}
    ccda = await convert_bundle(b, index)
    parsed = xmltodict.parse(base64.b64decode(base64_xml(ccda)))
    sections = parsed["ClinicalDocument"]["component"]["structuredBody"]["component"]
    section = next(c["section"] for c in sections if c["section"]["code"]["@code"] == "48765-2")
    row = section["text"]["table"]["tbody"]["tr"]["td"]
    if empty:
        assert row == {"@colspan": "6", "#text": "No information received"}
        observation = section["entry"]["act"]["entryRelationship"]["observation"]
        assert observation["value"]["@nullFlavor"] == "NI"
        assert observation.get("@negationInd") != "true"
    else:
        assert len(row) == 6
        assert row[1] == "No known allergy"
        assert row[2] == "Observation status: final"


@pytest.mark.asyncio
async def test_absence_and_positive_allergies_remain_separate_in_full_ccda():
    data = create_bundle_dict([{"use": "official", "given": ["Test"], "family": "Patient"}])
    records = [
        resource(id="positive", onsetDateTime="2024-01-01"),
        resource(
            id="absence",
            onsetDateTime="2020-01-01",
            code={"coding": [{"system": "http://snomed.info/sct", "code": "716186003", "display": "No known allergy"}]},
        ),
    ]
    data["entry"].extend({"resource": r.as_json()} for r in records)
    data["entry"].append(
        {
            "resource": {
                "resourceType": "List",
                "id": "allergies",
                "status": "current",
                "mode": "snapshot",
                "title": "Allergies and adverse reactions",
                "entry": [{"item": {"reference": f"AllergyIntolerance/{r.id}"}} for r in records],
            }
        }
    )
    b = Bundle(data)
    index = {f"{e.resource.resource_type}/{e.resource.id}": e.resource for e in b.entry}
    ccda = await convert_bundle(b, index)
    parsed = xmltodict.parse(base64.b64decode(base64_xml(ccda)))
    section = next(
        c["section"]
        for c in parsed["ClinicalDocument"]["component"]["structuredBody"]["component"]
        if c["section"]["code"]["@code"] == "48765-2"
    )
    observations = [e["act"]["entryRelationship"]["observation"] for e in section["entry"]]
    assert len(observations) == 2
    assert observations[0]["id"]["@root"] == "absence"
    assert observations[0]["@negationInd"] == "true"
    assert observations[0]["value"]["@code"] == "419199007"
    assert observations[1]["id"]["@root"] == "positive"
    assert "@negationInd" not in observations[1]
    assert [r["td"][1] for r in section["text"]["table"]["tbody"]["tr"]] == ["No known allergy", "Test substance"]
