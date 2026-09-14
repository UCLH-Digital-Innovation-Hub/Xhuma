import json
import logging
import uuid

import xmltodict
from fastapi import Request

from ...audit.models import SAMLAttributes
from ...gpconnect import gpconnect
from ...redis_connect import redis_client
from .constants import REGISTRY_ID
from .helpers import create_envelope, create_header


async def iti_38_response(
    request: Request, nhsno: int, ceid, queryid: str, saml_attrs: SAMLAttributes
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
        r = await gpconnect(nhsno, saml_attrs, request=request)
        # print("-" * 40)
        # print(r.body)
        # print("-" * 40)
        try:
            r = await gpconnect(nhsno, saml_attrs, request=request)

            # print("-" * 40)
            logging.info("no cached ccda, used internal call for patient")
            r = json.loads(r.body)
        except Exception as e:
            logging.error(f"Error: {e}")
            # print(f"iti_38_error: {e}")
            r = {
                "success": False,
                "error": f"Internal error retrieving structured record for patient. error: {e}",
            }
            body["AdhocQueryResponse"]["@status"] = (
                "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Failure"
            )
            body["AdhocQueryResponse"]["RegistryErrorList"] = {
                "@highestSeverity": "urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error",
                "RegistryError": {
                    "@errorCode": "XDSRegistryError",
                    "@codeContext": "Unable to locate SCR for patient",
                    "@location": "",
                    "@severity": "urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error",
                },
            }

        if not r.get("success"):
            logging.warning(f"gpconnect failed for patient: {r.get('error')}")
            body["AdhocQueryResponse"]["@status"] = (
                "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Failure"
            )
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
            # print(r)
            docid = r["document_id"]

    if docid is not None:
        # make sure docid is a string and not bytes
        if isinstance(docid, bytes):
            docid = docid.decode("utf-8")

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
