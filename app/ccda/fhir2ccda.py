import asyncio
import datetime
import json
import os
import pprint
from copy import deepcopy
from typing import List

import xmltodict
from fhirclient.models import bundle, humanname
from fhirclient.models import list as fhirlist
from fhirclient.models import medicationstatement, patient

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

    # document level effective time, use local time
    ccda["ClinicalDocument"]["effectiveTime"] = {
        "@value": datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    }

    ccda["ClinicalDocument"]["title"] = {
        "#text": "GP Connect: Access Record Structured"
    }

    # patient
    # TODO refine address parsing as may have multiple

    # loop through names to find official name
    for name in subject[0].name:
        if name.use == "official":
            official_name = name
            break

    patient_dict = {
        "patientRole": {
            "id": {
                "@extension": subject[0].identifier[0].value,
                "@root": "2.16.840.1.113883.2.1.4.1",
            },
            "patient": {
                "name": {
                    "@use": "L",
                    "given": {"#text": " ".join(official_name.given)},
                    "family": {"#text": official_name.family},
                },
                "birthTime": {"@value": date_helper(subject[0].birthDate.isostring)},
            },
        }
    }

    if subject[0].address:
        patient_dict["patientRole"]["addr"] = {
            "@use": "HP",
            "streetAddressLine": [x for x in subject[0].address[0].line],
            "city": {"#text": subject[0].address[0].city},
            "postalCode": {"#text": subject[0].address[0].postalCode},
        }

    gp_organization = subject[0].managingOrganization.reference
    gp = index[gp_organization]

    patient_dict["patientRole"]["providerOrganization"] = {
        "id": {"@root": gp.identifier[0].system, "@extension": gp.identifier[0].value},
        "name": {"#text": gp.name},
        "addr": {
            "streetAddressLine": [x for x in gp.address[0].line],
            "city": {"#text": gp.address[0].city},
            "postalCode": {"#text": gp.address[0].postalCode},
        },
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

    async def create_section(list: fhirlist.List) -> dict:
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
            "Active Medications": {
                "displayName": "Active Medications",
                "root": "2.16.840.1.113883.10.20.22.2.1",
                "Code": "10160-0",
            },
            "Past Medications": {
                "displayName": "Past Medications",
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
            "Active Medications",
            "Past Medications",
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
                "Active Medications": [
                    "Start Date",
                    "End Date",
                    "Status",
                    "Medication",
                    "Instructions",
                ],
                "Past Medications": [
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

            async def parse_medications(references):
                # run lookups concurrently (much faster than awaiting in a loop)
                return await asyncio.gather(
                    *(medication(entry, index) for entry in references)
                )

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
                        "Prescription Type",
                        "Medication",
                        "Instructions",
                        "Misc Notes",
                        "Prescribing Agency",
                        "Last Issued Date",
                    ],
                    "parser": parse_medications,
                },
                "Active Medications": {
                    "section_headers": [
                        "Start Date",
                        "End Date",
                        "Status",
                        "Prescription Type",
                        "Medication",
                        "Instructions",
                        "Misc Notes",
                        "Prescribing Agency",
                        "Last Issued Date",
                    ],
                    "parser": parse_medications,
                },
                "Past Medications": {
                    "section_headers": [
                        "Start Date",
                        "End Date",
                        "Status",
                        "Prescription Type",
                        "Medication",
                        "Instructions",
                        "Misc Notes",
                        "Prescribing Agency",
                        "Last Issued Date",
                    ],
                    "parser": parse_medications,
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
                # row = []
                # for data in entry_data:
                #     row.append({data})
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
                # print(f"processing entries for {list.title}")
                # print(section_setup[list.title]["section_headers"])
                headers = create_headers(list.title)

                # items = section_setup[list.title]["parser"](references)
                parser = section_setup[list.title]["parser"]
                items = parser(references)
                if asyncio.iscoroutine(items):  # or: inspect.isawaitable(items)
                    items = await items
                entries = [i.entry for i in items]
                rows = [i.row for i in items if i.row is not None]
                table_rows = [create_row(row) for row in rows]
                # if mediations sort rows by status then name of medication
                if list.title == "Medications and medical devices":
                    table_rows.sort(key=lambda x: (x["td"][2], x["td"][4]))
                comp["section"]["entry"] = entries
                comp["section"]["text"] = {
                    "paragraph": {
                        "@styleCode": "flagData",
                    },
                    "table": {"thead": headers, "tbody": {"tr": table_rows}},
                }

            if hasattr(list, "note") and list.note is not None:
                # TODO changing to paragraph before text with stylecode flagData
                # comp["section"]["text"]["list"] = {}
                # comp["section"]["text"]["list"]["item"] = [
                #     note.text for note in list.note
                # ]

                comp["section"]["text"]["paragraph"]["#text"] = [
                    f"{note.text}<br />" for note in list.note
                ]
                comp["section"]["text"]["paragraph"]["#text"] = "".join(
                    list.note[i].text + "<br />" for i in range(len(list.note))
                )

            return comp

    def split_medications(medications: fhirlist.List) -> List[dict]:
        """Splits medications into active and past based on status

        Args:
            medications (fhirlist.List): list of medication dicts with status field

        Returns:
            List[dict]: list of medication dicts with active and past medications split
        """
        active = []
        past = []

        for med in medications.entry:
            print(med)
            referenced_med = index[med.item.reference]
            # print(referenced_med)
            # status active or end date in the future
            if referenced_med.status == "active":
                active.append(med)
            elif (
                referenced_med.status == "completed"
                and hasattr(referenced_med, "endDate")
                and referenced_med.endDate is not None
                and date_helper(referenced_med.endDate) > datetime.datetime.now()
            ):
                active.append(med)
            else:
                past.append(med)
        # print(f"split medications into {len(active)} active and {len(past)} past")
        # print(active)
        return active, past

    def clone_list(original, new_title, new_entries):
        new_list = deepcopy(original)
        new_list.title = new_title
        new_list.entry = new_entries
        return new_list

    # bundle_components = [await create_section(list) for list in lists]
    # bundle_components = [x for x in bundle_components if x is not None]
    bundle_components = []
    for list_obj in lists:
        if list_obj.title == "Medications and medical devices":
            try:
                active, past = split_medications(list_obj)
                # print(f"active medications: {len(active)}, past medications: {len(past)}")
                active_section = await create_section(
                    clone_list(list_obj, "Active Medications", active)
                )

                past_section = await create_section(
                    clone_list(list_obj, "Past Medications", past)
                )
                bundle_components.append(active_section)
                bundle_components.append(past_section)
            except Exception as e:
                print(f"Error processing medications: {e}")
                try:
                    section = await create_section(list_obj)
                    if section is not None:
                        bundle_components.append(section)
                except Exception as e:
                    print(f"Error processing medications without split: {e}")
        else:
            section = await create_section(list_obj)
            if section is not None:
                bundle_components.append(section)
    caching_period = os.environ.get("CCDA_CACHING_PERIOD", "24 hours")
    header_components = {
        "templateId": templateId("2.16.840.1.113883.10.20.22.2.64", "2016-11-01"),
        "title": "Important Information",
        "text": f"This record contains information from the patients GP record. It was generated on {datetime.datetime.now().strftime('%Y-%m-%d')} and information added to the record in the last {caching_period} may be missing.",
    }
    bundle_components.insert(0, {"section": header_components})

    ccda["ClinicalDocument"]["component"] = {}
    ccda["ClinicalDocument"]["component"]["structuredBody"] = {}
    ccda["ClinicalDocument"]["component"]["structuredBody"][
        "component"
    ] = bundle_components

    return ccda


if __name__ == "__main__":
    # Example usage
    with open("app/tests/fixtures/bundles/9692136744.json", "r") as f:
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
        xml = xmltodict.unparse(ccda, pretty=True)
        xml = xml.replace("&lt;br /&gt;", "<br/>").replace("&lt;br&gt;", "<br/>")
        output.write(xml)
