import json
import logging
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


@router.get("/gpconnect/{nhsno}")
async def gpconnect(nhsno: int, saml_attrs: dict):
    """accesses gp connect endpoint for nhs number"""

    # validate nhsnumber
    if validateNHSnumber(nhsno) == False:
        logging.error(f"{nhsno} is not a valid NHS number")
        raise HTTPException(status_code=400, detail="Invalid NHS number")

    pds_search = await lookup_patient(nhsno)
    pprint.pprint(pds_search)
    gp_ods = pds_search["generalPractitioner"][0]["identifier"]["value"]

    # TODO sds search
    asid_trace = await sds_trace(gp_ods)
    print("Device")
    # pprint.pprint(asid_trace["entry"]["resource"]["identifier"])
    for item in asid_trace["entry"][0]["resource"]["identifier"]:
        if item["system"] == "https://fhir.nhs.uk/Id/nhsSpineASID":
            asid = item["value"]
        elif item["system"] == "https://fhir.nhs.uk/Id/nhsMhsPartyKey":
            nhsmhsparty = item["value"]

    print("ASID:", asid)
    print("NHS MHS Party Key:", nhsmhsparty)
    print("-" * 20)

    print("Endpoint")
    endpoint_trace = await sds_trace(gp_ods, endpoint=True, mhsparty=nhsmhsparty)
    fhir_endpoint_url = endpoint_trace["entry"][0]["fullUrl"]
    print(fhir_endpoint_url)
    print("-" * 20)

    token = create_jwt(saml_attrs)

    headers = {
        # unique uuid per request (TODO maybe use the correlation id?)
        "Ssp-TraceID": str(uuid4()),
        # ASID for originating organisation e.g. Hospital not Xhuma
        "Ssp-From": asid,
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
    r = httpx.post(
        # "https://orange.testlab.nhs.uk/B82617/STU3/1/gpconnect/structured/fhir/Patient/$gpc.getstructuredrecord",
        f"https://testspineproxy.nhs.domain.uk/{fhir_endpoint_url}",
        json=body,
        headers=headers,
    )
    logging.info(r)
    print(r.text)
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
    # xop = convert_mime(xml_ccda)
    xop = base64_xml(xml_ccda)
    print(xop)
    doc_uuid = str(uuid4())

    # TODO set this as background task
    redis_client.setex(nhsno, timedelta(minutes=60), doc_uuid)
    redis_client.setex(doc_uuid, timedelta(minutes=60), xop)

    # pprint(xml_ccda)
    with open(f"{nhsno}.xml", "w") as output:
        output.write(xmltodict.unparse(xml_ccda, pretty=True))

    return {"document_id": doc_uuid}
