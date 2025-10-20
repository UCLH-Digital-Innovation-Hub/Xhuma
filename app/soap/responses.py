import base64
import logging
import os
import pprint
import json
import uuid
from datetime import datetime, timedelta

import xmltodict
from fastapi import Request
from httpx import AsyncClient

from ..gpconnect import gpconnect
from ..redis_connect import redis_client

# REGISTRY_ID = redis_client.get("registry")
COMMUNITY_ID = os.getenv("COMMUNITY_ID", "2.16.840.1.113883.2.1.3.34.9001")
REGISTRY_ID = os.getenv("REGISTRY_ID", "2.16.840.1.113883.2.1.3.34.69.420")


def create_security():
    current_time = datetime.now()
    expiration_time = current_time + timedelta(minutes=5)

    current_timestamp = current_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    expiration_timestamp = expiration_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    security = {
        "@s:mustUnderstand": 1,
        "@xmlns:o": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
        "u:Timestamp": {
            "@u:Id": "_0",
            "u:Created": {"#text": current_timestamp},
            "u:Expires": {"#text": expiration_timestamp},
        },
    }

    return security


def create_header(message_urn: str, message_id: str):
    header = {
        "a:Action": {
            "@s:mustUnderstand": 1,
            "#text": message_urn,
        },
        "a:RelatesTo": {"#text": message_id},
        # "o:Security": create_security(),
    }
    return header


def create_envelope(header, body):
    envelope = {
        "s:Envelope": {
            "@xmlns:s": "http://www.w3.org/2003/05/soap-envelope",
            "@xmlns:a": "http://www.w3.org/2005/08/addressing",
            "@xmlns:u": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
            "s:Header": header,
            "s:Body": body,
        }
    }
    return envelope


def create_id(root, extension):
    return {"@root": root, "@extension": extension}


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
                                    "given": {"#text": patient["name"][0]["given"][0]},
                                    "family": {"#text": patient["name"][0]["family"]},
                                },
                                "administrativeGenderCode": {"@code": gender},
                                # birthTime is ISO 8601 format
                                "birthTime": {
                                    "@value": patient["birthDate"].replace("-", "")
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
    header = create_header(
        "urn:hl7-org:v3:PRPA_IN201306UV02:CrossGatewayPatientDiscovery", message_id
    )

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
                "queryId": query["queryId"],
                "queryResponseCode": {"@code": "AE"},
                "statusCode": {"@code": "aborted"},
            },
            "queryByParameter": query,
        },
    }
    header = create_header(
        "urn:hl7-org:v3:PRPA_IN201306UV02:CrossGatewayPatientDiscovery", message_id
    )

    return xmltodict.unparse(create_envelope(header, body), pretty=True)


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
                                    "given": {"#text": patient["name"][0]["given"][0]},
                                    "family": {"#text": patient["name"][0]["family"]},
                                },
                                "administrativeGenderCode": {"@code": gender},
                                # birthTime is ISO 8601 format
                                "birthTime": {
                                    "@value": patient["birthDate"].replace("-", "")
                                },
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


async def iti_38_response(
    request: Request, nhsno: int, ceid, queryid: str, saml_attrs: dict
):

    body = {}
    body["AdhocQueryResponse"] = {
        "@status": "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success",
        "@xmlns": "urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0",
    }

    # check the redis cache if there's an existing ccda
    docid = redis_client.get(nhsno)

    if docid is None:
        # no cached ccda
        try:
            r = await gpconnect(nhsno, saml_attrs, request=request)
            logging.info(f"no cached ccda, used internal call for {nhsno}")
            r = json.loads(r)[0]
            print(f"gpconnect response: {r}")
            docid = r["document_id"]
        except Exception as e:
            logging.error(f"Error: {e}")
            print(f"iti_38_error: {e}")
            body["AdhocQueryResponse"][
                "@status"
            ] = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Failure"
            body["AdhocQueryResponse"]["RegistryErrorList"] = {
                "@highestSeverity": "urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error",
                "RegistryError": {
                    "@errorCode": "XDSRegistryError",
                    "@codeContext": f"Unable to locate SCR with NHS number {nhsno}",
                    "@location": "",
                    "@severity": "urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error",
                },
            }

        if not r.get("success"):
            logging.warning(f"gpconnect failed for {nhsno}: {r.get('error')}")
            body["AdhocQueryResponse"][
                "@status"
            ] = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Failure"
            body["AdhocQueryResponse"]["RegistryErrorList"] = {
                "@highestSeverity": "urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error",
                "RegistryError": {
                    "@errorCode": "XDSRegistryError",
                    "@codeContext": r.get("error", "Unknown error"),
                    "@location": "",
                    "@severity": "urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error",
                },
            }
        else:
            docid = r["document_id"]
    
    # make sure docid is a string and not bytes
    if isinstance(docid, bytes):
        docid = docid.decode("utf-8")

    if docid is not None:
        # add the ccda as registry object list
        # object_id = f"CCDA_{docid}"
        object_id = docid
        # create list of slots
        slots = []

        def create_slot(name: str, value) -> dict:
            slot_dict = {"@name": name, "ValueList": {"Value": {"#text": value}}}
            return slot_dict

        def create_classification(
            classification_scheme: str,
            noderep: str,
            value,
            localized_string: str,
        ) -> dict:
            classification = {
                "@classificationScheme": classification_scheme,
                "@classifiedObject": object_id,
                "@id": f"urn:uuid:{uuid.uuid4()}",
                "@nodeRepresentation": noderep,
                "@objectType": "urn:oasis:names:tc:ebxml-regrep:ObjectType:RegistryObject:Classification",
                "Slot": create_slot("codingScheme", value),
                "Name": {"LocalizedString": {"@value": localized_string}},
            }
            return classification

        # slots.append(create_slot("creationTime", str(int(datetime.now().timestamp()))))

        # ceid will be in form \'UHL5MFM2ZLPQCW5^^^&amp;1.2.840.114350.1.13.525.3.7.3.688884.100&amp;ISO\'
        # slots.append(
        #     create_slot(
        #         "sourcePatientId",
        #         f"{ceid}^^^&1.2.840.114350.1.13.525.3.7.3.688884.100&ISO",
        #     )
        # )

        slots.append(
            create_slot(
                "sourcePatientId",
                f"{nhsno}^^^&2.16.840.1.113883.2.1.4.1&ISO",
            )
        )

        slots.append(
            create_slot(
                "sourcePatientInfo",
                f"PID-3|{nhsno}^^^&2.16.840.1.113883.2.1.4.1&ISO;{ceid}^^^&1.2.840.114350.1.13.525.3.7.3.688884.100&ISO",
            )
        )
        slots.append(create_slot("languageCode", "en-GB"))
        # No hash for on demand document
        # slots.append(create_slot("hash", "4cf4f82d78b5e2aac35c31bca8cb79fe6bd6a41e"))
        slots.append(create_slot("size", "1"))
        slots.append(create_slot("repositoryUniqueId", REGISTRY_ID))

        classifications = []
        classifications.append(
            create_classification(
                "urn:uuid:41a5887f-8865-4c09-adf7-e362475b143a",
                "34133-9",
                "2.16.840.1.113883.6.1",
                "XDSDocumentEntry.classCode",
            )
        )
        classifications.append(
            create_classification(
                "urn:uuid:a09d5840-386c-46f2-b5ad-9c3699a4309d",
                "",
                "urn:hl7-org:sdwg:ccda-structuredBody:1.1",
                "XDSDocumentEntry.formatCode",
            )
        )

        body["AdhocQueryResponse"]["RegistryObjectList"] = {
            "@xmlns": "urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0",
            "ExtrinsicObject": {
                "@id": object_id,
                # "@status": "urn:oasis:names:tc:ebxml-regrep:StatusType:Approved",
                "@status": "urn:ihe:iti:2010:StatusType:DeferredCreation",
                "@objectType": "urn:uuid:34268e47-fdf5-41a6-ba33-82133c465248",  # On Demand
                "@mimeType": "text/xml",
                "Slot": slots,
                "Classification": classifications,
                # UNIQUE ID SECTION
                "ExternalIdentifier": [
                    {
                        "@identificationScheme": "urn:uuid:2e82c1f6-a085-4c72-9da3-8640a32e42ab",
                        "@value": docid,
                        # "@id": f"CCDA-{docid}",
                        "@id": docid,
                        "@registryObject": object_id,
                        "@objectType": "urn:oasis:names:tc:ebxml-regrep:ObjectType:RegistryObject:ExternalIdentifier",
                        "Name": {
                            "LocalizedString": {"@value": "XDSDocumentEntry.uniqueId"}
                        },
                    },
                    {
                        "@identificationScheme": "urn:uuid:58a6f841-87b3-4a3e-92fd-a8ffeff98427",
                        "@value": f"{nhsno}^^^&2.16.840.1.113883.2.1.4.99.1&ISO",
                        "@id": f"PID-{nhsno}",
                        "@registryObject": object_id,
                        "@objectType": "urn:oasis:names:tc:ebxml-regrep:ObjectType:RegistryObject:ExternalIdentifier",
                        "Name": {
                            "LocalizedString": {"@value": "XDSDocumentEntry.patientId"}
                        },
                    },
                ],
            },
        }

    else:
        body["AdhocQueryResponse"]["RegistryObjectList"] = {}

    soap_response = create_envelope(
        create_header("urn:ihe:iti:2007:CrossGatewayQueryResponse", queryid), body
    )

    return xmltodict.unparse(soap_response, pretty=True)


async def iti_39_response(message_id: str, document_id: str, document):

    # base64 encode the document
    # base64_bytes = base64.b64encode(document.encode("utf-8")).decode("utf-8")
    # print(type(base64_bytes))
    body = {
        "ns4:RetrieveDocumentSetResponse": {
            "@xmlns:ns4": "urn:ihe:iti:xds-b:2007",
            "@xmlns:ns8": "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0",
            "ns8:RegistryResponse": {
                "@id": uuid.uuid4(),
                "@status": "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success",
                # "@xmlns": "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0",
            },
            "ns4:DocumentResponse": {
                "ns4:HomeCommunityId": {"#text": f"urn:oid:{COMMUNITY_ID}"},
                "ns4:RepositoryUniqueId": {"#text": REGISTRY_ID},
                "ns4:DocumentUniqueId": {"#text": document_id},
                "ns4:mimeType": {"#text": "text/xml"},
                "ns4:Document": document,
            },
        },
    }

    soap_response = create_envelope(
        create_header("urn:ihe:iti:2007:CrossGatewayRetrieveResponse", message_id), body
    )

    print(f"ITI39 response: {soap_response}")

    # soap_response = create_envelope(
    #     create_header("urn:ihe:iti:2007:RetrieveDocumentSetResponse", "test"), body
    # )

    # Verify that all values are serializable
    # TODO DELETE THIS?
    def ensure_serializable(data):
        if isinstance(data, bytes):
            return data.decode("utf-8")  # Decode bytes to string
        elif isinstance(data, dict):
            return {k: ensure_serializable(v) for k, v in data.items()}  # Recurse
        elif isinstance(data, list):
            return [ensure_serializable(item) for item in data]  # Recurse for lists
        else:
            return data  # Return as-is for strings, numbers, etc.

    soap_response = ensure_serializable(soap_response)

    # pprint.pprint(soap_response)
    # print(type(soap_response))

    # with open(f"{document_id}.xml", "w") as output:
    #     output.write(xmltodict.unparse(soap_response, pretty=True))

    return xmltodict.unparse(soap_response, pretty=True)
