import uuid
from typing import List

from ..helpers import templateId


def empty_entry(title: str) -> List[dict]:
    """Generates an empty C-CDA entry with nullFlavor="NI" for empty sections."""
    entry_id = str(uuid.uuid4())
    if title == "Allergies and adverse reactions":
        return [
            {
                "act": {
                    "@classCode": "ACT",
                    "@moodCode": "EVN",
                    "templateId": templateId("2.16.840.1.113883.10.20.22.4.30", "2015-08-01"),
                    "id": [{"@root": entry_id}],
                    "code": {"@code": "CONC", "@codeSystem": "2.16.840.1.113883.5.6"},
                    "statusCode": {"@code": "active"},
                    "effectiveTime": {"@nullFlavor": "NI"},
                    "entryRelationship": [
                        {
                            "@typeCode": "SUBJ",
                            "observation": {
                                "@classCode": "OBS",
                                "@moodCode": "EVN",
                                "templateId": templateId("2.16.840.1.113883.10.20.22.4.7", "2014-06-09"),
                                "id": [{"@root": str(uuid.uuid4())}],
                                "code": {
                                    "@code": "ASSERTION",
                                    "@codeSystem": "2.16.840.1.113883.5.4",
                                },
                                "statusCode": {"@code": "completed"},
                                "value": {"@xsi:type": "CD", "@nullFlavor": "NI"},
                            },
                        }
                    ],
                }
            }
        ]
    elif title == "Problems":
        return [
            {
                "act": {
                    "@classCode": "ACT",
                    "@moodCode": "EVN",
                    "templateId": templateId("2.16.840.1.113883.10.20.22.4.3", "2015-08-01"),
                    "id": [{"@root": entry_id}],
                    "code": {"@code": "CONC", "@codeSystem": "2.16.840.1.113883.5.6"},
                    "statusCode": {"@code": "active"},
                    "effectiveTime": {"@nullFlavor": "NI"},
                    "entryRelationship": [
                        {
                            "@typeCode": "SUBJ",
                            "observation": {
                                "@classCode": "OBS",
                                "@moodCode": "EVN",
                                "templateId": templateId("2.16.840.1.113883.10.20.22.4.4", "2015-08-01"),
                                "id": [{"@root": str(uuid.uuid4())}],
                                "code": [
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
                                ],
                                "statusCode": {"@code": "completed"},
                                "effectiveTime": {"@nullFlavor": "NI"},
                                "value": {"@xsi:type": "CD", "@nullFlavor": "NI"},
                            },
                        }
                    ],
                }
            }
        ]
    elif title in [
        "Active Medications",
        "Past Medications",
        "Medications and medical devices",
    ]:
        return [
            {
                "substanceAdministration": {
                    "@classCode": "SBADM",
                    "@moodCode": "EVN",
                    "templateId": templateId("2.16.840.1.113883.10.20.22.4.16", "2014-06-09"),
                    "id": [{"@root": entry_id}],
                    "statusCode": {"@code": "active"},
                    "effectiveTime": [{"@nullFlavor": "NI"}],
                    "consumable": {
                        "manufacturedProduct": {
                            "templateId": templateId(
                                root="2.16.840.1.113883.10.20.22.4.23",
                                extension="2014-06-09",
                            ),
                            "manufacturedMaterial": {"code": {"@nullFlavor": "NI"}},
                        }
                    },
                }
            }
        ]
    elif title == "Immunisations":
        return [
            {
                "substanceAdministration": {
                    "@classCode": "SBADM",
                    "@moodCode": "EVN",
                    "templateId": templateId("2.16.840.1.113883.10.20.22.4.52", "2014-06-09"),
                    "id": [{"@root": entry_id}],
                    "statusCode": {"@code": "completed"},
                    "effectiveTime": [{"@nullFlavor": "NI"}],
                    "consumable": {
                        "manufacturedProduct": {
                            "templateId": templateId("2.16.840.1.113883.10.20.22.4.54", "2014-06-09"),
                            "manufacturedMaterial": {"code": {"@nullFlavor": "NI"}},
                        }
                    },
                }
            }
        ]
    elif title in ["Investigations and results", "Vital Signs"]:
        return [
            {
                "organizer": {
                    "@classCode": "BATTERY",
                    "@moodCode": "EVN",
                    "templateId": templateId("2.16.840.1.113883.10.20.22.4.1", "2015-08-01"),
                    "id": [{"@root": entry_id}],
                    "code": {"@nullFlavor": "NI"},
                    "statusCode": {"@code": "completed"},
                    "component": {
                        "observation": {
                            "@classCode": "OBS",
                            "@moodCode": "EVN",
                            "templateId": templateId("2.16.840.1.113883.10.20.22.4.2", "2015-08-01"),
                            "id": [{"@root": str(uuid.uuid4())}],
                            "code": {"@nullFlavor": "NI"},
                            "statusCode": {"@code": "completed"},
                            "effectiveTime": {"@nullFlavor": "NI"},
                            "value": {"@xsi:type": "ANY", "@nullFlavor": "NI"},
                        }
                    },
                }
            }
        ]
    return []
