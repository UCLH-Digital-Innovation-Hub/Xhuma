import json
import logging
import os
import pprint
from datetime import timedelta
from uuid import uuid4

import httpx
import xmltodict
from fastapi import APIRouter, HTTPException, Request, Response
from fhirclient.models import bundle

from .ccda.convert_mime import base64_xml, convert_mime
from .ccda.fhir2ccda import convert_bundle
from .ccda.helpers import validateNHSnumber
from .pds.pds import lookup_patient, sds_trace
from .redis_connect import redis_client
from .security import create_jwt

router = APIRouter()

client = httpx.AsyncClient(
    cert=("keys/nhs_certs/client_cert.pem", "keys/nhs_certs/client_key.pem"),
    verify="keys/nhs_certs/nhs_bundle.pem",
)
logging.basicConfig(level=logging.DEBUG)
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.DEBUG)


@router.get("/gpconnect/{nhsno}")
async def gpconnect(nhsno: int, saml_attrs: dict, log_dir: str = None):
    """accesses gp connect endpoint for nhs number"""

    # validate nhsnumber
    if validateNHSnumber(nhsno) == False:
        logging.error(f"{nhsno} is not a valid NHS number")
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "w") as f:
                f.write(f"{nhsno} is not a valid NHS number\n")
        raise HTTPException(status_code=400, detail="Invalid NHS number")

    # TODO add in caching
    pds_search = await lookup_patient(nhsno)

    # make sure patient is unrestricted
    security_code = pds_search["meta"]["security"][0]["code"]

    if security_code != "U":
        logging.error(
            f"{nhsno} is not an unrestricted patient, GP connect access not permitted"
        )
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "w") as f:
                f.write(f"{nhsno} is restricted\n")

        return Response({"success": False, "error": "Patient is not unrestricted, access to GP connect is not permitted"})
        raise HTTPException(
            status_code=403,
            detail="Patient is not unrestricted, access to GP connect is not permitted",
        )

    # print(pds_search)
    gp_ods = pds_search["generalPractitioner"][0]["identifier"]["value"]

    asid_trace = await sds_trace(gp_ods)
    for item in asid_trace["entry"][0]["resource"]["identifier"]:
        if item["system"] == "https://fhir.nhs.uk/Id/nhsSpineASID":
            asid = item["value"]
        elif item["system"] == "https://fhir.nhs.uk/Id/nhsMhsPartyKey":
            nhsmhsparty = item["value"]

    # log error if unable to find ASID or nhsMhsPartyKey
    if not asid or not nhsmhsparty:
        logging.error(f"Unable to find ASID or nhsMhsPartyKey for ODS code {gp_ods}")
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "w") as f:
                f.write(f"Unable to find ASID or nhsMhsPartyKey for ODS code {gp_ods}\n")
        raise HTTPException(
            status_code=500,
            detail="Unable to find ASID or nhsMhsPartyKey for the provided ODS code",
        )
    
    endpoint_trace = await sds_trace(gp_ods, endpoint=True, mhsparty=nhsmhsparty)

    # if unable to find fhirendpoint, log error and raise exception
    if "entry" not in endpoint_trace or len(endpoint_trace["entry"]) == 0:
        logging.error(f"Unable to find FHIR endpoint for ODS code {gp_ods}")
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "w") as f:
                f.write(f"Unable to find FHIR endpoint for ODS code {gp_ods}\n")
        raise HTTPException(
            status_code=500,
            detail="Unable to find FHIR endpoint for the provided ODS code",
        )
    
    fhir_endpoint_url = endpoint_trace["entry"][0]["fullUrl"]


    token = create_jwt(
        saml_attrs, audience=fhir_endpoint_url
    )

    headers = {
        # unique uuid per request (TODO maybe use the correlation id?)
        "Ssp-TraceID": str(uuid4()),
        # ASID for originating organisation e.g. Hospital not Xhuma
        "Ssp-From": "200000002574",
        "Ssp-To": asid,
        "Ssp-InteractionID": "urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1",
        "Authorization": f"Bearer {token}",
        "accept": "application/fhir+json",
        "Content-Type": "application/fhir+json",
    }

    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "patientNHSNumber",
                "valueIdentifier": {
                    "system": "https://fhir.nhs.uk/Id/nhs-number",
                    "value": f"{nhsno}",
                },
            },
            {
                "name": "includeAllergies",
                "part": [{"name": "includeResolvedAllergies", "valueBoolean": False}],
            },
            {
                "name": "includeMedication",
                "part": [{"name": "includePrescriptionIssues", "valueBoolean": False}],
            },
            {"name": "includeProblems"},
            {"name": "includeImmunisations"},
            {"name": "includeInvestigations"},
        ],
    }
    if log_dir:
        with open(os.path.join(log_dir, "request_headers.json"), "w") as f:
            json.dump(headers, f, indent=2)
        with open(os.path.join(log_dir, "request_body.json"), "w") as f:
            json.dump(body, f, indent=2)

    url = f"https://msg.intspineservices.nhs.uk/{fhir_endpoint_url}"
    
    try:
        r = await client.post(url, json=body, headers=headers)
        print(r.status_code)
        print(r.text)
    except httpx.ReadError as e:
        print("❌ ReadError: server closed connection before responding")
    except Exception as e:
        print("❌ Unexpected error:", e)
    

    if log_dir:
        with open(os.path.join(log_dir, "response.json"), "w") as f:
            f.write(r.text)
    logging.info(r.text)
    scr_bundle = json.loads(r.text)

    # get rid of fhir_comments
    comment_index = None
    for j, i in enumerate(scr_bundle["entry"]):
        if "fhir_comments" in i.keys():
            comment_index = j
    if comment_index is not None:
        scr_bundle["entry"].pop(comment_index)

    fhir_bundle = bundle.Bundle(scr_bundle)

    # index resources to allow for resolution
    bundle_index = {}
    for entry in fhir_bundle.entry:
        try:
            address = f"{entry.resource.resource_type}/{entry.resource.id}"
            bundle_index[address] = entry.resource
        except:
            pass

    xml_ccda = await convert_bundle(fhir_bundle, bundle_index)
    if log_dir:
        with open(os.path.join(log_dir, f"{nhsno}.xml"), "w") as output:
            output.write(xmltodict.unparse(xml_ccda, pretty=True))
    # xop = convert_mime(xml_ccda)
    xop = base64_xml(xml_ccda)
    # print(xop)
    doc_uuid = str(uuid4())

    # TODO set this as background task
    redis_client.setex(nhsno, timedelta(minutes=60), doc_uuid)
    redis_client.setex(doc_uuid, timedelta(minutes=60), xop)

    # pprint(xml_ccda)
    with open(f"{nhsno}.xml", "w") as output:
        output.write(xmltodict.unparse(xml_ccda, pretty=True))

    return {"success": True, "document_id": doc_uuid}


if __name__ == "__main__":
    import asyncio

    audit_dict = {
        "subject_id": "CONE, Stephen",
        "organization": "UCLH - University College London Hospitals - TST",
        "organization_id": "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
        "home_community_id": "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
        "role": {
            "Role": {
                "@codeSystem": "2.16.840.1.113883.6.96",
                "@code": "224608005",
                "@codeSystemName": "SNOMED_CT",
                "@displayName": "Administrative healthcare staff",
                "@xmlns": "urn:hl7-org:v3",
                "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
            }
        },
        "purpose_of_use": {
            "PurposeForUse": {
                "@xsi:type": "CE",
                "@code": "TREATMENT",
                "@codeSystem": "2.16.840.1.113883.3.18.7.1",
                "@codeSystemName": "nhin-purpose",
                "@displayName": "Treatment",
                "@xmlns": "urn:hl7-org:v3",
                "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
            },
        },
        "resource_id": "9690937278^^^&2.16.840.1.113883.2.1.4.1&ISO",
    }

    # result = await gpconnect(9690937278, audit_dict)
    result = asyncio.run(gpconnect(9690937286, audit_dict))
    assert result["resourceType"] == "Patient"
