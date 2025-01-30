import datetime

from fhirclient.models import bundle
from fhirclient.models import list as fhirlist
from fhirclient.models import patient

from .entries import allergy, medication, problem
from .helpers import date_helper, templateId


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
            print(list.title)
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
            # if there are no entries
            if not list.entry:
                # comp["section"]["@nullFlavour"] = "NI"
                # Initialize empty table with appropriate headers based on section
                if list.title == "Allergies and adverse reactions":
                    comp["section"]["text"] = "<table border='1'><tr><th>Start Date</th><th>Status</th><th>Description</th><th>Reaction</th></tr><tr><td colspan='4'>No Information Available</td></tr></table>"
                elif list.title == "Medications and medical devices":
                    comp["section"]["text"] = "<table border='1'><tr><th>Start Date</th><th>End Date</th><th>Status</th><th>Medication</th><th>Instructions</th></tr><tr><td colspan='5'>No Information Available</td></tr></table>"
                elif list.title == "Problems":
                    comp["section"]["text"] = "<table border='1'><tr><th>Date</th><th>Status</th><th>Condition</th></tr><tr><td colspan='3'>No Information Available</td></tr></table>"
                elif list.title == "Immunisations":
                    comp["section"]["text"] = "<table border='1'><tr><th>Date</th><th>Type</th><th>Details</th></tr><tr><td colspan='3'>No Information Available</td></tr></table>"
                elif list.title == "Vital Signs":
                    comp["section"]["text"] = "<table border='1'><tr><th>Date</th><th>Type</th><th>Value</th><th>Units</th></tr><tr><td colspan='4'>No Information Available</td></tr></table>"

            else:
                # Initialize table with headers based on section
                if list.title == "Allergies and adverse reactions":
                    comp["section"]["text"] = "<table border='1'><tr><th>Start Date</th><th>Status</th><th>Description</th><th>Reaction</th></tr>"
                elif list.title == "Medications and medical devices":
                    comp["section"]["text"] = "<table border='1'><tr><th>Start Date</th><th>End Date</th><th>Status</th><th>Medication</th><th>Instructions</th></tr>"
                elif list.title == "Problems":
                    comp["section"]["text"] = "<table border='1'><tr><th>Date</th><th>Status</th><th>Condition</th></tr>"
                elif list.title == "Immunisations":
                    comp["section"]["text"] = "<table border='1'><tr><th>Date</th><th>Type</th><th>Details</th></tr>"
                elif list.title == "Vital Signs":
                    comp["section"]["text"] = "<table border='1'><tr><th>Date</th><th>Type</th><th>Value</th><th>Units</th></tr>"

                comp["section"]["entry"] = []
                for entry in list.entry:
                    referenced_item = index[entry.item.reference]

                    if list.title == "Allergies and adverse reactions":
                        entry_data = allergy(referenced_item)
                        comp["section"]["entry"].append(entry_data)
                        comp["section"]["text"] += f"<tr><td>{entry_data['act']['effectiveTime']['low']['@value']}</td>" \
                            f"<td>{entry_data['act']['statusCode']['@code']}</td>" \
                            f"<td>{entry_data['act']['entryRelationship']['observation']['participant']['participantRole']['playingEntity']['code']['@displayName']}</td>" \
                            f"<td>{entry_data['act']['entryRelationship']['observation']['entryRelationship']['observation']['value']['@displayName']}</td></tr>"
                    elif list.title == "Problems":
                        entry_data = problem(referenced_item)
                        comp["section"]["entry"].append(entry_data)
                        comp["section"]["text"] += f"<tr><td>{entry_data['act']['effectiveTime']['low']['@value']}</td>" \
                            f"<td>{entry_data['act']['statusCode']['@code']}</td>" \
                            f"<td>{entry_data['act']['entryRelationship']['observation']['value']['@displayName']}</td></tr>"
                    elif list.title == "Medications and medical devices":
                        entry_data = medication(referenced_item, index)
                        comp["section"]["entry"].append(entry_data)
                        comp["section"]["text"] += f"<tr><td>{entry_data['substanceAdministration']['effectiveTime']['low']['@value']}</td>" \
                            f"<td>{entry_data['substanceAdministration']['effectiveTime']['high']['@value']}</td>" \
                            f"<td>{entry_data['substanceAdministration']['statusCode']['@code']}</td>" \
                            f"<td>{entry_data['substanceAdministration']['consumable']['manufacturedProduct']['manufacturedMaterial']['code'][0]['@displayName']}</td>" \
                            f"<td>{entry_data['substanceAdministration']['entryRelationship']['act']['text']['#text']}</td></tr>"

                # Close the table after all entries are processed
                comp["section"]["text"] += "</table>"

            return comp

    bundle_components = [create_section(list) for list in lists]
    bundle_components = [x for x in bundle_components if x is not None]
    ccda["ClinicalDocument"]["component"] = {}
    ccda["ClinicalDocument"]["component"]["structuredBody"] = {}
    ccda["ClinicalDocument"]["component"]["structuredBody"][
        "component"
    ] = bundle_components

    return ccda
