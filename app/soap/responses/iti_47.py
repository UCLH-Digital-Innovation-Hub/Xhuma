import uuid
from datetime import datetime

import xmltodict

from .helpers import create_envelope, create_header, create_id


def _select_usual_name(patient: dict) -> dict:
    """Select the FHIR ``usual`` name, falling back for legacy patient data."""

    names = patient["name"]
    # Some older PDS fixtures do not carry a use code. Retaining the first-name
    # fallback avoids rejecting those patients while preferring the intended name.
    return next((name for name in names if name.get("use") == "usual"), names[0])


async def iti_47_response(message_id, patient, ceid, query):
    """ITI47 response message generator

    Args:
        message_id (_type_): _description_
        patient (_type_): _description_
        ceid (_type_): _description_
        query (_type_): _description_

    Returns:
        _type_: _description_
    """

    gp = patient["generalPractitioner"][0]

    # pprint.pprint(patient["address"][0])
    address = patient["address"][0]
    patient_gender = patient["gender"]
    if patient_gender == "male":
        gender = "M"
    elif patient_gender == "female":
        gender = "F"
    else:
        gender = "UNK"

    usual_name = _select_usual_name(patient)

    ids = []
    ids.append(create_id("2.16.840.1.113883.2.1.4.1", patient["id"]))
    ids.append(create_id("1.2.840.114350.1.13.525.3.7.3.688884.100", ceid))

    body = {
        "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
    }

    body["PRPA_IN201306UV02"] = {
        "@xmlns": "urn:hl7-org:v3",
        "@ITSVersion": "XML_1.0",
        "id": {"@root": str(uuid.uuid4())},
        "creationTime": {"@value": int(datetime.now().timestamp())},
        "interactionId": {
            "@root": "2.16.840.1.113883.1.18",
            "@extension": "PRPA_IN201306UV02",
        },
        "processingCode": {"@code": "T"},
        "processingModeCode": {"@code": "T"},
        "acceptAckCode": {"@code": "NE"},
        "receiver": {
            "@typeCode": "RCV",
            "device": {"@classCode": "DEV", "@determinerCode": "INSTANCE"},
        },
        "sender": {
            "@typeCode": "SND",
            "device": {"@classCode": "DEV", "@determinerCode": "INSTANCE"},
        },
        "acknowledgement": {
            "typeCode": {"@code": "AA"},
            "targetMessage": {"id": {"@root": message_id}},
        },
        "controlActProcess": {
            "@classCode": "CACT",
            "@moodCode": "EVN",
            "code": {
                "@code": "PRPA_TE201306UV02",
                "@codeSystem": "2.16.840.1.113883.1.18",
            },
            "authorOrPerformer": {
                "@typeCode": "AUT",
                "assignedDevice": {
                    "@classCode": "ASSIGNED",
                    "id": {"@root": "1.2.840.114350.1.13.1610.1.7.3.688884.100"},
                },
            },
            "subject": {
                "@typeCode": "SUBJ",
                "@contextConductionInd": "false",
                "registrationEvent": {
                    "@classCode": "REG",
                    "moodCode": "EVN",
                    "statusCode": {"@code": "active"},
                    "subject1": {
                        "@typeCode": "SBJ",
                        "patient": {
                            "@classCode": "PAT",
                            "id": ids,
                            "statusCode": {"@code": "active"},
                            "patientPerson": {
                                "@classCode": "PSN",
                                "@determinerCode": "INSTANCE",
                                "name": {
                                    "given": {"#text": usual_name["given"][0]},
                                    "family": {"#text": usual_name["family"]},
                                },
                                "administrativeGenderCode": {"@code": gender},
                                # birthTime is ISO 8601 format
                                "birthTime": {"@value": patient["birthDate"].replace("-", "")},
                                # "birthTime": {"@value": patient["birthDate"]},
                                "addr": {
                                    "streetAddressLine": address["line"],
                                    "postalCode": {"#text": address["postalCode"]},
                                },
                            },
                            "providerOrganization": {
                                "@classCode": "ORG",
                                "@determinerCode": "INSTANCE",
                                "id": {
                                    "@root": "2.16.840.1.113883.2.1.4.3",
                                    "id": gp["identifier"]["value"],
                                },
                            },
                        },
                    },
                },
            },
            "queryAck": {
                "queryId": query["queryId"],
                "queryResponseCode": {"@code": "OK"},
                "statusCode": {"@code": "deliveredResponse"},
            },
            "queryByParameter": query,
        },
    }
    header = create_header("urn:hl7-org:v3:PRPA_IN201306UV02", message_id)

    return xmltodict.unparse(create_envelope(header, body), pretty=True)
