import uuid
from datetime import datetime

import xmltodict

from .constants import COMMUNITY_ID
from .helpers import create_envelope, create_header, create_id


def _select_usual_name(patient: dict) -> dict:
    """Select the FHIR ``usual`` name, falling back for legacy patient data."""

    names = patient["name"]
    # Some older PDS fixtures do not carry a use code. Retaining the first-name
    # fallback avoids rejecting those patients while preferring the intended name.
    return next((name for name in names if name.get("use") == "usual"), names[0])


async def iti_55_response(message_id, patient, query):
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
    # we need to add an additional ID as an "internal" CEID
    ids.append(create_id("2.16.840.1.113883.2.1.4.1.99", patient["id"]))

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
                    # NHS number needs to be the assigned authority
                    "id": {"@root": "2.16.840.1.113883.2.1.4.1"},
                },
            },
            "subject": {
                "@typeCode": "SUBJ",
                "@contextConductionInd": "false",
                "registrationEvent": {
                    "@classCode": "REG",
                    "moodCode": "EVN",
                    "statusCode": {"@code": "active"},
                    "custodian": {
                        "@typeCode": "CST",
                        "assignedEntity": {
                            "@classCode": "ASSIGNED",
                            "id": {
                                "@root": COMMUNITY_ID,
                            },
                            "code": {
                                "@code": "SupportsHealthDataLocator",
                                "@codeSystem": "1.3.6.1.4.1.19376.1.2.27.2",
                            },
                        },
                    },
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
                "queryId": query.get("queryId", {"@root": "unknown"})
                if isinstance(query, dict)
                else {"@root": "unknown"},
                "queryResponseCode": {"@code": "OK"},
                "statusCode": {"@code": "deliveredResponse"},
            },
            "queryByParameter": query if query else {},
        },
    }
    header = create_header("urn:hl7-org:v3:PRPA_IN201306UV02:CrossGatewayPatientDiscovery", message_id)

    return xmltodict.unparse(create_envelope(header, body), pretty=True)


async def iti_55_error(message_id, query, error_text):
    """ITI55 error response message generator

    Args:
        message_id (_type_): _description_
        query (_type_): _description_
        error_text (_type_): _description_

    Returns:
        _type_: _description_
    """

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
            "typeCode": {"@code": "AE"},
            "targetMessage": {"id": {"@root": message_id}},
            "acknowledgementDetail": {
                "@text": error_text,
            },
        },
        "controlActProcess": {
            "@classCode": "CACT",
            "@moodCode": "EVN",
            "code": {
                "@code": "PRPA_TE201306UV02",
                "@codeSystem": "2.16.840.1.113883.1.18",
            },
            "queryAck": {
                # todo: handle missing queryId
                "queryId": (query["queryId"] if "queryId" in query else "can't find queryID"),
                "queryResponseCode": {"@code": "AE"},
                "statusCode": {"@code": "aborted"},
            },
            "queryByParameter": query,
        },
    }
    header = create_header("urn:hl7-org:v3:PRPA_IN201306UV02:CrossGatewayPatientDiscovery", message_id)

    return xmltodict.unparse(create_envelope(header, body), pretty=True)
