import datetime

from fhirclient.models import bundle
from fhirclient.models import list as fhirlist
from fhirclient.models import patient

from .entries import allergy, medication, problem
from .helpers import date_helper, readable_date, templateId


async def convert_bundle(bundle: bundle.Bundle, index: dict) -> dict:
    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.1.15.html
    lists = [
        entry.resource
        for entry in bundle.entry
        if isinstance(entry.resource, fhirlist.List)
    ]

    subject = [
        entry.resource
        for entry in bundle.entry
        if isinstance(entry.resource, patient.Patient)
    ]
    ccda = {}
    ccda["ClinicalDocument"] = {
        "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "@xmlns": "urn:hl7-org:v3",
        "@xmlns:voc": "urn:hl7-org:v3/voc",
        "@xmlns:sdtc": "urn:hl7-org:sdtc",
        "realmCode": {"@code": "GB"},
        "typeId": {"@root": "2.16.840.1.113883.1.3", "@extension": "POCD_HD000040"},
    }
    ccda["ClinicalDocument"]["templateId"] = templateId(
        "2.16.840.1.113883.10.20.22.1.2", "2015-08-01"
    )

    # code
    ccda["ClinicalDocument"]["code"] = {
        "@code": "34133-9",
        "@codeSystem": "2.16.840.1.113883.6.1",
    }

    ccda["ClinicalDocument"]["title"] = {"#text": "Summary Care Record"}

    # patient
    # TODO refine address parsing as may have multiple

    patient_dict = {
        "patientRole": {
            "id": {
                "@extension": subject[0].identifier[0].value,
                "@root": "2.16.840.1.113883.2.1.4.1",
            },
            "addr": {
                "@use": "HP",
                "streetAddressLine": [x for x in subject[0].address[0].line],
                "city": {"#text": subject[0].address[0].city},
                "postalCode": {"#text": subject[0].address[0].postalCode},
            },
            "patient": {
                "name": {
                    "@use": "L",
                    "given": {"#text": " ".join(subject[0].name[0].given)},
                    "family": {"#text": subject[0].name[0].family},
                },
                "birthTime": {"@value": date_helper(subject[0].birthDate.isostring)},
            },
        }
    }

    ccda["ClinicalDocument"]["recordTarget"] = patient_dict

    # author
    ccda["ClinicalDocument"]["author"] = {
        "time": {"@value": datetime.date.today().strftime("%Y%m%d")},
        "assignedAuthor": {
            "addr": {"@nullFlavor": "NA"},
            "telecom": {"@nullFlavor": "NA"},
            "assignedAuthoringDevice": {
                "manufacturerModelName": {"#text": "SCR Connector"},
                "softwareName": {"#text": "SCR Connector v0.1"},
            },
        },
    }

    # documentationOf
    ccda["ClinicalDocument"]["documentationOf"] = {
        "serviceEvent": {
            "@classCode": "PCPR",
            "effectiveTime": {
                "low": {
                    "@value": date_helper(subject[0].birthDate.isostring),
                },
                "high": {"@value": datetime.date.today().strftime("%Y%m%d")},
            },
        }
    }

    # vital signs doesn't appear in the SCR therefore crate blank list to generate xml
    vital_signs = fhirlist.List()
    vital_signs.title = "Vital Signs"
    lists.append(vital_signs)

    def create_section(list: fhirlist.List) -> dict:
        templates = {
            "Allergies and adverse reactions": {
                "displayName": "Allergies, adverse reactions, alerts",
                "root": "2.16.840.1.113883.10.20.22.2.6.1",
                "Code": "48765-2",
            },
            "Medications and medical devices": {
                "displayName": "Medications",
                "root": "2.16.840.1.113883.10.20.22.2.1",
                "Code": "10160-0",
            },
            "Problems": {
                "displayName": "Problems List",
                "root": "2.16.840.1.113883.10.20.22.2.5.1",
                "Code": "11450-4",
            },
            "Immunisations": {
                "displayName": "Immunisations",
                "root": "2.16.840.1.113883.10.20.22.2.5",
                "Code": "11450-4",
            },
            "Vital Signs": {
                "displayName": "Vital Signs",
                "root": "2.16.840.1.113883.10.20.22.2.4.1",
                "Code": "8716-3",
            },
        }

        sections = [
            "Allergies and adverse reactions",
            "Immunisations",
            "Medications and medical devices",
            "Problems",
            "Vital Signs",
        ]

        # check if list is one of the desired ones
        if list.title in sections:
            # print(list.title)
            comp = {}
            comp["section"] = {
                "templateId": templateId(templates[list.title]["root"], "2015-8-1"),
                "code": {
                    "@code": templates[list.title]["Code"],
                    "@displayName": templates[list.title]["displayName"],
                    "@codeSystem": "2.16.840.1.113883.6.1",
                },
                "title": list.title,
                "text": "",  # Will be populated with table
            }

            table_headers = {
                "Allergies and adverse reactions": [
                    "Start Date",
                    "Status",
                    "Description",
                    "Reaction",
                ],
                "Medications and medical devices": [
                    "Start Date",
                    "End Date",
                    "Status",
                    "Medication",
                    "Instructions",
                ],
                "Problems": ["Date", "Status", "Condition"],
                "Immunisations": ["Date", "Type", "Details"],
                "Vital Signs": ["Date", "Type", "Value", "Units"],
            }

            def create_headers(title: str) -> dict:
                """Create headers for the table based on the section

                Args:
                    title (str): Title of the fhir section

                Returns:
                    dict: dictionary with appropriate headers for the section to generate xml correctly
                """

                headers = []
                for header in table_headers[title]:
                    headers.append({header})
                return {"tr": {"th": table_headers[title]}}

            def create_row(entry_data) -> dict:
                """generates a table row from a list of inputs

                Args:
                    entry_data (List): _description_

                Returns:
                    dict: _description_
                """
                row = []
                for data in entry_data:
                    row.append({data})
                return {"td": entry_data}

            if not list.entry:
                # if there are no entries
                # Initialize empty table with appropriate headers based on section
                comp["section"]["text"] = {
                    "table": {
                        "thead": create_headers(list.title),
                        "tbody": {
                            "tr": {
                                "td": {
                                    "@colspan": len(table_headers[list.title]),
                                    "#text": "No Information Available",
                                }
                            }
                        },
                    }
                }
            else:
                comp["section"]["entry"] = []
                rows = []
                for entry in list.entry:
                    referenced_item = index[entry.item.reference]

                    if list.title == "Allergies and adverse reactions":
                        entry_data = allergy(referenced_item)
                        comp["section"]["entry"].append(entry_data)
                        rows.append(
                            create_row(
                                [
                                    readable_date(
                                        entry_data["act"]["effectiveTime"]["low"][
                                            "@value"
                                        ]
                                    ),
                                    entry_data["act"]["statusCode"]["@code"],
                                    entry_data["act"]["entryRelationship"][
                                        "observation"
                                    ]["participant"]["participantRole"][
                                        "playingEntity"
                                    ][
                                        "code"
                                    ][
                                        "@displayName"
                                    ],
                                    entry_data["act"]["entryRelationship"][
                                        "observation"
                                    ]["entryRelationship"]["observation"]["value"][
                                        "@displayName"
                                    ],
                                ]
                            )
                        )
                    elif list.title == "Problems":
                        entry_data = problem(referenced_item)
                        comp["section"]["entry"].append(entry_data)
                        rows.append(
                            create_row(
                                [
                                    readable_date(
                                        entry_data["act"]["effectiveTime"]["low"][
                                            "@value"
                                        ]
                                    ),
                                    entry_data["act"]["statusCode"]["@code"],
                                    entry_data["act"]["entryRelationship"][
                                        "observation"
                                    ]["value"]["@displayName"],
                                ]
                            )
                        )
                    elif list.title == "Medications and medical devices":
                        entry_data = medication(referenced_item, index)
                        comp["section"]["entry"].append(entry_data)
                        print(
                            entry_data["substanceAdministration"]["consumable"][
                                "manufacturedProduct"
                            ]["manufacturedMaterial"]["code"]
                        )
                        rows.append(
                            create_row(
                                [
                                    readable_date(
                                        entry_data["substanceAdministration"][
                                            "effectiveTime"
                                        ]["low"]["@value"]
                                    ),
                                    entry_data["substanceAdministration"][
                                        "effectiveTime"
                                    ]["high"]["@value"],
                                    entry_data["substanceAdministration"]["statusCode"][
                                        "@code"
                                    ],
                                    entry_data["substanceAdministration"]["consumable"][
                                        "manufacturedProduct"
                                    ]["manufacturedMaterial"]["code"]["displayName"],
                                    entry_data["substanceAdministration"][
                                        "entryRelationship"
                                    ]["act"]["text"]["#text"],
                                ]
                            )
                        )
                        print(rows)
                # Close the table after all entries are processed
                comp["section"]["text"] = {
                    "table": {
                        "thead": create_headers(list.title),
                        "tbody": {"tr": rows},
                    }
                }
                return comp

    bundle_components = [create_section(list) for list in lists]
    bundle_components = [x for x in bundle_components if x is not None]
    ccda["ClinicalDocument"]["component"] = {}
    ccda["ClinicalDocument"]["component"]["structuredBody"] = {}
    ccda["ClinicalDocument"]["component"]["structuredBody"][
        "component"
    ] = bundle_components

    return ccda
