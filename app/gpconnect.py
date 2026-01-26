import asyncio
import json
import logging
import os
import pprint
import ssl
from datetime import timedelta
from uuid import uuid4

import httpx
import xmltodict
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fhirclient.models import bundle

from .ccda.convert_mime import base64_xml, convert_mime
from .ccda.fhir2ccda import convert_bundle
from .ccda.helpers import validateNHSnumber
from .pds.pds import lookup_patient, sds_trace
from .redis_connect import redis_client
from .security import create_jwt

router = APIRouter()

# client = httpx.AsyncClient(
#     cert=("keys/nhs_certs/client_cert.pem", "keys/nhs_certs/client_key.pem"),
#     verify="keys/nhs_certs/nhs_bundle.pem",
# )


def create_nhs_ssl_context(cert_path, key_path, ca_path):
    ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_context.load_cert_chain(cert_path, key_path)
    ssl_context.load_verify_locations(ca_path)
    return ssl_context


ssl_context = create_nhs_ssl_context(
    "keys/nhs_certs/client_cert.pem",
    "keys/nhs_certs/client_key.pem",
    "keys/nhs_certs/nhs_bundle.pem",
)

logging.basicConfig(level=logging.DEBUG)
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.DEBUG)

client = httpx.AsyncClient(
    cert=("keys/nhs_certs/client_cert.pem", "keys/nhs_certs/client_key.pem"),
    verify=ssl_context,  # This fixes the silent failures!
    timeout=httpx.Timeout(30.0),
    http2=False,
)


@router.get("/gpconnect/{nhsno}")
async def gpconnect(nhsno: int, saml_attrs: dict, log_dir: str = None) -> JSONResponse:
    """accesses gp connect endpoint for nhs number"""

    # 1) Validate NHS number
    if validateNHSnumber(nhsno) is False:
        msg = f"{nhsno} is not a valid NHS number"
        logging.error(msg)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(msg + "\n")
        return JSONResponse(status_code=400, content={"success": False, "error": msg})

    # 2) PDS lookup (consider caching in future)
    try:
        pds_search = await lookup_patient(nhsno)
    except Exception as e:
        msg = f"PDS lookup failed: {e}"
        logging.exception(msg)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(msg + "\n")
        return JSONResponse(status_code=502, content={"success": False, "error": msg})

    # 3) Security/U flag
    try:
        security_code = pds_search["meta"]["security"][0]["code"]
    except Exception:
        security_code = None

    if security_code != "U":
        msg = "Patient is not unrestricted, access to GP Connect is not permitted"
        logging.error(f"{nhsno} is restricted")
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(f"{nhsno} is restricted\n")
        return JSONResponse(status_code=403, content={"success": False, "error": msg})

    # 4) Resolve ODS → ASID + PartyKey
    try:
        gp_ods = pds_search["generalPractitioner"][0]["identifier"]["value"]
    except Exception as e:
        msg = f"Unable to read GP ODS from PDS response: {e}"
        logging.exception(msg)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(msg + "\n")
        return JSONResponse(status_code=500, content={"success": False, "error": msg})

    try:
        asid_trace = await sds_trace(gp_ods)
    except Exception as e:
        msg = f"SDS trace failed: {e}"
        logging.exception(msg)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(msg + "\n")
        return JSONResponse(status_code=502, content={"success": False, "error": msg})

    asid = None
    nhsmhsparty = None
    for item in (
        asid_trace.get("entry", [{}])[0].get("resource", {}).get("identifier", [])
    ):
        if item.get("system") == "https://fhir.nhs.uk/Id/nhsSpineASID":
            asid = item.get("value")
        elif item.get("system") == "https://fhir.nhs.uk/Id/nhsMhsPartyKey":
            nhsmhsparty = item.get("value")

    if not asid or not nhsmhsparty:
        msg = f"Unable to find ASID or nhsMhsPartyKey for ODS code {gp_ods}"
        logging.error(msg)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(msg + "\n")
        return JSONResponse(status_code=500, content={"success": False, "error": msg})

    # 5) Endpoint lookup
    try:
        endpoint_trace = await sds_trace(gp_ods, endpoint=True, mhsparty=nhsmhsparty)
    except Exception as e:
        msg = f"SDS endpoint trace failed: {e}"
        logging.exception(msg)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(msg + "\n")
        return JSONResponse(status_code=502, content={"success": False, "error": msg})

    if "entry" not in endpoint_trace or len(endpoint_trace["entry"]) == 0:
        msg = f"Unable to find FHIR endpoint for ODS code {gp_ods}"
        logging.error(msg)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(msg + "\n")
        return JSONResponse(status_code=500, content={"success": False, "error": msg})

    fhir_endpoint_url = endpoint_trace["entry"][0]["resource"]["address"]

    # 6) Build request
    token = create_jwt(saml_attrs, audience=f"{fhir_endpoint_url}")
    headers = {
        "Ssp-TraceID": str(uuid4()),
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
            # {
            #     "name": "includeAllergies",
            #     "part": [{"name": "includeResolvedAllergies", "valueBoolean": False}],
            # },
            {
                "name": "includeMedication",
                "part": [{"name": "includePrescriptionIssues", "valueBoolean": False}],
            },
            # {"name": "includeProblems"},
            # {"name": "includeInvestigations"},
        ],
    }
    if log_dir:
        with open(os.path.join(log_dir, "request_headers.json"), "w") as f:
            json.dump(headers, f, indent=2)
        with open(os.path.join(log_dir, "request_body.json"), "w") as f:
            json.dump(body, f, indent=2)

    url = f"https://proxy.intspineservices.nhs.uk/{fhir_endpoint_url}/Patient/$gpc.getstructuredrecord"

    # 7) Make request with a per-call client (avoid leaking across event loops)
    resp = None
    try:
        async with httpx.AsyncClient(
            cert=("keys/nhs_certs/client_cert.pem", "keys/nhs_certs/client_key.pem"),
            verify=create_nhs_ssl_context(
                "keys/nhs_certs/client_cert.pem",
                "keys/nhs_certs/client_key.pem",
                "keys/nhs_certs/nhs_bundle.pem",
            ),
            timeout=httpx.Timeout(30.0),
            http2=False,
        ) as session:
            resp = await session.post(url, json=body, headers=headers)

        if log_dir:
            with open(
                os.path.join(log_dir, f"{resp.status_code}_response.json"), "w"
            ) as f:
                f.write(resp.text)
        logging.info(resp.text)

    except httpx.ReadError as e:
        msg = "ReadError: server closed connection before responding"
        print("❌", msg)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(msg + "\n")
        return JSONResponse(status_code=502, content={"success": False, "error": msg})

    except Exception as e:
        msg = f"Unexpected error during request: {e}"
        print("❌", msg)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(msg + "\n")
        return JSONResponse(status_code=502, content={"success": False, "error": msg})

    # 8) Non-200 handling
    if resp.status_code != 200:
        msg = f"Error from GP Connect endpoint {resp.status_code}"
        logging.error(msg)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(msg + "\n")
        return JSONResponse(
            status_code=resp.status_code, content={"success": False, "error": msg}
        )

    # 9) Convert to CCDA, store in Redis, return JSONResponse
    scr_bundle = json.loads(resp.text)
    # remove any single 'fhir_comments' entry to keep fhirclient happy
    comment_index = None
    for j, i in enumerate(scr_bundle.get("entry", [])):
        if "fhir_comments" in i:
            comment_index = j
            break
    if comment_index is not None:
        scr_bundle["entry"].pop(comment_index)

    fhir_bundle = bundle.Bundle(scr_bundle)

    # index resources for resolution
    bundle_index = {}
    for entry in fhir_bundle.entry or []:
        try:
            addr = f"{entry.resource.resource_type}/{entry.resource.id}"
            bundle_index[addr] = entry.resource
        except Exception:
            pass

    xml_ccda = await convert_bundle(fhir_bundle, bundle_index)
    if log_dir:
        with open(os.path.join(log_dir, f"{nhsno}.xml"), "w") as output:
            output.write(xmltodict.unparse(xml_ccda, pretty=True))

    xop = base64_xml(xml_ccda)
    doc_uuid = str(uuid4())
    redis_client.setex(nhsno, timedelta(minutes=60), doc_uuid)
    redis_client.setex(doc_uuid, timedelta(minutes=60), xop)

    with open(f"{nhsno}.xml", "w") as output:
        output.write(xmltodict.unparse(xml_ccda, pretty=True))

    return JSONResponse(
        status_code=200, content={"success": True, "document_id": doc_uuid}
    )


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
    result = asyncio.run(gpconnect(9658218873, audit_dict))
    print(result.body.decode())
    print(result.status_code)
    assert "error" in result.body.decode()
    body = json.loads(result.body)
    assert body["success"] is False
    # assert result["resourceType"] == "Patient"
