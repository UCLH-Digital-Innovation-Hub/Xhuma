import uuid

from fhirclient.models import allergyintolerance

from ..helpers import code_with_translations, date_helper, readable_date, templateId
from .types import EntryWithRow


def allergy(entry: allergyintolerance.AllergyIntolerance) -> EntryWithRow:
    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.30.html
    all = {
        "act": {
            "@classCode": "ACT",
            "@moodCode": "EVN",
        }
    }
    all["act"]["templateId"] = templateId(
        "2.16.840.1.113883.10.20.22.4.30", "2015-08-01"
    )
    all["act"]["id"] = {"@root": uuid.uuid4()}
    all["act"]["code"] = {"@code": "CONC", "@codeSystem": "2.16.840.1.113883.5.6"}

    # may need to be made dynamic if force to query old allergies
    all["act"]["statusCode"] = {"@code": "active"}
    all["act"]["effectiveTime"] = {
        "low": {"@value": date_helper(entry.assertedDate.isostring)}
    }
    all["act"]["entryRelationship"] = {"@typeCode": "SUBJ"}

    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.7.html
    observation = {"@classCode": "OBS", "@moodCode": "EVN"}
    observation["templateId"] = templateId(
        "2.16.840.1.113883.10.20.22.4.7", "2014-06-09"
    )
    observation["id"] = {"@root": uuid.uuid4()}
    observation["code"] = {"@code": "ASSERTION", "@codeSystem": "2.16.840.1.113883.5.4"}
    observation["statusCode"] = {"@code": "completed"}
    observation["value"] = {
        "@xsi:type": "CD",
        "@code": "416098002",
        "@displayName": "drug allergy",
        "@codeSystemName": "SNOMED CT",
        "@codeSystem": "2.16.840.1.113883.6.96",
    }

    observation["participant"] = {
        "@typeCode": "CSM",
        "participantRole": {
            "@classCode": "MANU",
            "playingEntity": {
                "@classCode": "MMAT",
                "code": code_with_translations(entry.code.coding).model_dump(
                    by_alias=True, exclude_none=True
                ),
            },
        },
    }
    # if there is a reaction, add manifestation as entryRelationship
    if entry.reaction and entry.reaction[0].manifestation:
        observation["entryRelationship"] = {
            "@typeCode": "MFST",
            "@inversionInd": "true",
            "observation": {
                "@classCode": "OBS",
                "@moodCode": "EVN",
                "templateId": templateId(
                    "2.16.840.1.113883.10.20.22.4.9", "2014-06-09"
                ),
                "id": {"@root": uuid.uuid4()},
                "code": {"@code": "ASSERTION", "@codeSystem": "2.16.840.1.113883.5.4"},
                "effectiveTime": {
                    "low": {"@value": date_helper(entry.assertedDate.isostring)}
                },
                "value": {
                    "@xsi:type": "CD",
                    "@code": entry.reaction[0].manifestation[0].coding[0].code,
                    "@displayName": entry.reaction[0]
                    .manifestation[0]
                    .coding[0]
                    .display,
                    "@codeSystemName": "SNOMED CT",
                    "@codeSystem": "2.16.840.1.113883.6.96",
                },
            },
        }

    all["act"]["entryRelationship"]["observation"] = observation

    allergy_row = [
        readable_date(all["act"]["effectiveTime"].get("low", {}).get("@value", "")),
        all["act"]["statusCode"].get("@code", ""),
        observation["participant"]["participantRole"]["playingEntity"]["code"][
            "@displayName"
        ],
    ]

    return EntryWithRow(entry=all, row=allergy_row)
