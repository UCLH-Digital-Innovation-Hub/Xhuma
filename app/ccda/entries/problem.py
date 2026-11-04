import uuid

from fhirclient.models import condition

from ..helpers import cda_time_bound, readable_date, templateId
from .types import EntryWithRow


def problem(entry: condition.Condition) -> EntryWithRow:
    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.3.html
    prob = {
        "act": {
            "@classCode": "ACT",
            "@moodCode": "EVN",
        }
    }

    prob["act"]["templateId"] = templateId("2.16.840.1.113883.10.20.22.4.3", "2015-08-01")
    prob["act"]["id"] = {"@root": uuid.uuid4()}
    prob["act"]["code"] = {"@code": "CONC", "@codeSystem": "2.16.840.1.113883.5.6"}

    prob["act"]["statusCode"] = {"@code": entry.clinicalStatus}
    prob["act"]["effectiveTime"] = {
        "low": cda_time_bound(entry.assertedDate).model_dump(by_alias=True, exclude_none=True)
    }
    prob["act"]["entryRelationship"] = {"@typeCode": "SUBJ"}

    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.4.html
    observation = {"@classCode": "OBS", "@moodCode": "EVN"}
    observation["templateId"] = templateId("2.16.840.1.113883.10.20.22.4.4", "2015-08-01")
    observation["id"] = {"@root": uuid.uuid4()}
    observation["code"] = [
        {
            "@code": "64572001",
            "@displayName": "Condition",
            "@codeSystemName": "SNOMED CT",
            "@codeSystem": "2.16.840.1.113883.6.96",
        },
        {
            "@code": "75323-6",
            "@displayName": "Condition",
            "@codeSystemName": "LOINC",
            "@codeSystem": "2.16.840.1.113883.6.1",
        },
    ]
    observation["statusCode"] = {"@code": "completed"}
    observation["effectiveTime"] = {
        "low": cda_time_bound(entry.assertedDate).model_dump(by_alias=True, exclude_none=True)
    }
    observation["value"] = {
        "@xsi:type": "CD",
        "@code": entry.code.coding[0].code,
        "@displayName": entry.code.coding[0].display,
        "@codeSystemName": "SNOMED CT",
        "@codeSystem": "2.16.840.1.113883.6.96",
    }

    prob["act"]["entryRelationship"]["observation"] = observation

    problem_row = [
        readable_date(prob["act"]["effectiveTime"]["low"]["@value"]) if entry.assertedDate else "",
        prob["act"]["statusCode"].get("@code", ""),
        observation["value"].get("@displayName", ""),
    ]

    return EntryWithRow(entry=prob, row=problem_row)
