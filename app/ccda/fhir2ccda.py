import asyncio
import datetime
import json
import pprint
from typing import List

import xmltodict
from fhirclient.models import bundle
from fhirclient.models import list as fhirlist
from fhirclient.models import patient

from .entries import allergy, immunization_entry, medication, problem, result
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

    ccda["ClinicalDocument"]["title"] = {
        "#text": "GP Connect: Access Record Structured"
    }

    # patient
    # TODO refine address parsing as may have multiple

    patient_dict = {
        "patientRole": {
            "id": {
                "@extension": subject[0].identifier[0].value,
                "@root": "2.16.840.1.113883.2.1.4.1",
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
    # "addr": {
    #             "@use": "HP",
    #             "streetAddressLine": [x for x in subject[0].address[0].line],
    #             "city": {"#text": subject[0].address[0].city},
    #             "postalCode": {"#text": subject[0].address[0].postalCode},
    #         },
    if subject[0].address:
        patient_dict["patientRole"]["patient"]["addr"] = {
            "@use": "HP",
            "streetAddressLine": [x for x in subject[0].address[0].line],
            "city": {"#text": subject[0].address[0].city},
            "postalCode": {"#text": subject[0].address[0].postalCode},
        }

    ccda["ClinicalDocument"]["recordTarget"] = patient_dict

    # author
    ccda["ClinicalDocument"]["author"] = {
        "time": {"@value": datetime.date.today().strftime("%Y%m%d")},
        "assignedAuthor": {
            "addr": {"@nullFlavor": "NA"},
            "telecom": {"@nullFlavor": "NA"},
            "assignedAuthoringDevice": {
                "manufacturerModelName": {"#text": "Xhuma"},
                "softwareName": {"#text": "Xhuma v0.1"},
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
    # vital_signs = fhirlist.List()
    # vital_signs.title = "Vital Signs"
    # lists.append(vital_signs)s

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
                "root": "2.16.840.1.113883.10.20.22.2.2",
                "Code": "11369-6",
            },
            "Vital Signs": {
                "displayName": "Vital Signs",
                "root": "2.16.840.1.113883.10.20.22.2.4.1",
                "Code": "8716-3",
            },
            "Investigations and results": {
                "displayName": "Investigations and results",
                "root": "2.16.840.1.113883.6.1",
                "Code": "30954-2",
            },
        }

        sections = [
            "Allergies and adverse reactions",
            "Immunisations",
            "Medications and medical devices",
            "Problems",
            # "Vital Signs",
            # "Investigations and results",
        ]
        # print(list.title)
        # check if list is one of the desired ones
        if list.title in sections:
            print(list.title)
            comp = {}
            comp["section"] = {
                "templateId": templateId(templates[list.title]["root"], "2015-08-01"),
                "code": {
                    "@code": templates[list.title]["Code"],
                    "@displayName": templates[list.title]["displayName"],
                    "@codeSystem": "2.16.840.1.113883.6.1",
                },
                "title": templates[list.title]["displayName"],
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
                "Immunisations": ["Date", "Vaccine", "Lot Number", "Status"],
                "Vital Signs": ["Date", "Type", "Value", "Units"],
                "Investigations and results": ["Date", "Type", "Result"],
            }

            section_setup = {
                "Allergies and adverse reactions": {
                    "section_headers": [
                        "Start Date",
                        "Status",
                        "Description",
                        "Reaction",
                    ],
                    "parser": lambda list: [allergy(entry) for entry in list],
                },
                "Medications and medical devices": {
                    "section_headers": [
                        "Start Date",
                        "End Date",
                        "Status",
                        "Medication",
                        "Instructions",
                        "Prescribing Agency",
                        "Last Issued Date",
                    ],
                    "parser": lambda list: [medication(entry, index) for entry in list],
                },
                "Problems": {
                    "section_headers": ["Date", "Status", "Condition"],
                    "parser": lambda list: [problem(entry) for entry in list],
                },
                "Immunisations": {
                    "section_headers": ["Date", "Vaccine", "Lot Number", "Status"],
                    "parser": lambda list: [
                        immunization_entry(entry) for entry in list
                    ],
                },
            }

            def create_headers(title: str) -> dict:
                """Create headers for the table based on the section

                Args:
                    title (str): Title of the fhir section

                Returns:
                    dict: dictionary with appropriate headers for the section to generate xml correctly
                """

                headers = []
                for header in section_setup[title]["section_headers"]:
                    headers.append({header})
                return {"tr": {"th": section_setup[title]["section_headers"]}}

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

            # if list has attribute empty reason
            # check if the list is empty
            # if hasattr(list, "emptyReason"):
            #     print(f"list {list.title} is empty")
            #     # if the list is empty
            #     comp["section"]["text"] = {
            #         "table": {
            #             "thead": create_headers(list.title),
            #             "tbody": {
            #                 "tr": {
            #                     "td": {
            #                         "@colspan": len(table_headers[list.title]),
            #                         # "#text": list.emptyReason[0].text,
            #                         "#text": "No Information Available",
            #                     }
            #                 }
            #             },
            #         }
            #     }
            #     return comp
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
                references = [index[entry.item.reference] for entry in list.entry]
                print(f"processing entries for {list.title}")
                # print(section_setup[list.title]["section_headers"])
                headers = create_headers(list.title)

                items = section_setup[list.title]["parser"](references)
                entries = [i.entry for i in items]
                rows = [i.row for i in items if i.row is not None]
                table_rows = [create_row(row) for row in rows]
                comp["section"]["entry"] = entries
                comp["section"]["text"] = {
                    "table": {"thead": headers, "tbody": {"tr": table_rows}}
                }

            if hasattr(list, "note") and list.note is not None:
                comp["section"]["text"]["list"] = {}
                comp["section"]["text"]["list"]["item"] = [
                    note.text for note in list.note
                ]

            return comp

    bundle_components = [create_section(list) for list in lists]
    bundle_components = [x for x in bundle_components if x is not None]
    ccda["ClinicalDocument"]["component"] = {}
    ccda["ClinicalDocument"]["component"]["structuredBody"] = {}
    ccda["ClinicalDocument"]["component"]["structuredBody"][
        "component"
    ] = bundle_components

    return ccda


if __name__ == "__main__":
    # Example usage
    with open("app/tests/fixtures/bundles/9690937472.json", "r") as f:
        structured_dosage_bundle = json.load(f)

    comment_index = None
    for j, i in enumerate(structured_dosage_bundle["entry"]):
        if "fhir_comments" in i.keys():
            comment_index = j
    if comment_index is not None:
        structured_dosage_bundle["entry"].pop(comment_index)
    fhir_bundle = bundle.Bundle(structured_dosage_bundle)

    fhir_bundle = bundle.Bundle(structured_dosage_bundle)

    # index resources to allow for resolution
    bundle_index = {}
    for entry in fhir_bundle.entry:
        try:
            address = f"{entry.resource.resource_type}/{entry.resource.id}"
            bundle_index[address] = entry.resource
        except:
            pass

    # ccda = await convert_bundle(fhir_bundle, bundle_index)
    ccda = asyncio.run(convert_bundle(fhir_bundle, bundle_index))
    # pprint.pprint(ccda)
    with open("output.xml", "w") as output:
        output.write(xmltodict.unparse(ccda, pretty=True))
