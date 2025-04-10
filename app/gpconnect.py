import json
import logging
from datetime import timedelta
from uuid import uuid4

import httpx
import xmltodict
from fastapi import APIRouter, HTTPException, Request, Response
from fhirclient.models import bundle

from .ccda.convert_mime import base64_xml, convert_mime
from .ccda.fhir2ccda import convert_bundle
from .ccda.helpers import validateNHSnumber
from .pds import pds
from .redis_connect import redis_client
from .security import create_jwt

router = APIRouter()

logger = logging.getLogger("httpx_logger")
logger.setLevel(logging.INFO)
# Create file handler to write logs to a file
file_handler = logging.FileHandler("httpx_logs.log")
file_handler.setLevel(logging.INFO)

# Create formatter and add it to the handler
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)

# Add the handler to the logger
logger.addHandler(file_handler)


async def log_request(request: httpx.Request):
    logger.info("Outgoing Request:")
    logger.info(f"{request.method} {request.url}")
    logger.info(f"Headers: {dict(request.headers)}")
    if request.content:
        try:
            logger.info(f"Body: {request.content.decode('utf-8')}")
        except UnicodeDecodeError:
            logger.info("Body: [Binary Content]")
    logger.info("-----")


async def log_response(response: httpx.Response):
    content_bytes = await response.aread()
    try:
        content_str = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content_str = "[Binary Content]"
    logger.info("Incoming Response:")
    logger.info(f"Status Code: {response.status_code}")
    logger.info(f"Headers: {dict(response.headers)}")
    logger.info(f"Body: {content_str}")
    logger.info("=====")


@router.get("/gpconnect/{nhsno}")
async def gpconnect(nhsno: int):
    """accesses gp connect endpoint for nhs number"""

    # validate nhsnumber
    if validateNHSnumber(nhsno) == False:
        logging.error(f"{nhsno} is not a valid NHS number")
        raise HTTPException(status_code=400, detail="Invalid NHS number")

    # TODO pds search
    pds_search = await pds.lookup_patient(nhsno)
    print(pds_search)

    # TODO sds search

    token = create_jwt()

    headers = {
        "Ssp-TraceID": "09a01679-2564-0fb4-5129-aecc81ea2706",
        "Ssp-From": "200000000359",
        "Ssp-To": "918999198738",
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
        "https://orange.testlab.nhs.uk/B82617/STU3/1/gpconnect/structured/fhir/Patient/$gpc.getstructuredrecord",
        json=body,
        headers=headers,
    )
    # log the request and response
    async with httpx.AsyncClient(event_hooks={
        "request": [log_request],
        "response": [log_response]
    }) as client:
        r = await client.post(
            "https://orange.testlab.nhs.uk/B82617/STU3/1/gpconnect/structured/fhir/Patient/$gpc.getstructuredrecord",
            json=body,
            headers=headers,
        )

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
