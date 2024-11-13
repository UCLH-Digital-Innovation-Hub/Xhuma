import json
import logging
from datetime import timedelta
from uuid import uuid4

import httpx
import xmltodict
from fastapi import APIRouter, HTTPException, Request, Response
from fhirclient.models import bundle
from opentelemetry import trace

from .ccda.convert_mime import convert_mime
from .ccda.fhir2ccda import convert_bundle
from .ccda.helpers import validateNHSnumber
from .pds import pds
from .redis_connect import redis_client
from .security import create_jwt
from .config import get_logger

router = APIRouter()
logger = get_logger("gpconnect")
tracer = trace.get_tracer(__name__)

@router.get("/gpconnect/{nhsno}")
async def gpconnect(nhsno: int):
    """accesses gp connect endpoint for nhs number"""
    with tracer.start_as_current_span("gpconnect_request") as span:
        try:
            # validate nhsnumber
            if validateNHSnumber(nhsno) == False:
                logger.error(f"{nhsno} is not a valid NHS number")
                raise HTTPException(status_code=400, detail="Invalid NHS number")

            # TODO pds search
            pds_search = await pds.lookup_patient(nhsno)
            logger.info("PDS lookup completed", extra={"pds_result": pds_search})

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

            logger.info("Making GP Connect request", extra={"nhs_number": nhsno})
            r = httpx.post(
                "https://orange.testlab.nhs.uk/B82617/STU3/1/gpconnect/structured/fhir/Patient/$gpc.getstructuredrecord",
                json=body,
                headers=headers,
            )

            if not r.is_success:
                logger.error("GP Connect request failed", extra={
                    "status_code": r.status_code,
                    "response": r.text
                })
                raise HTTPException(status_code=r.status_code, detail="GP Connect request failed")

            scr_bundle = json.loads(r.text)
            logger.info("Received FHIR bundle", extra={
                "bundle_type": scr_bundle.get("resourceType"),
                "entry_count": len(scr_bundle.get("entry", [])),
                "nhs_number": nhsno
            })

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
                    logger.debug(f"Indexed resource", extra={
                        "resource_type": entry.resource.resource_type,
                        "resource_id": entry.resource.id
                    })
                except Exception as e:
                    logger.warning("Failed to index resource", extra={"error": str(e)})

            logger.info("Converting FHIR bundle to CCDA", extra={
                "resource_count": len(bundle_index),
                "nhs_number": nhsno
            })
            xml_ccda = await convert_bundle(fhir_bundle, bundle_index)
            xop = convert_mime(xml_ccda)
            doc_uuid = str(uuid4())

            # TODO set this as background task
            redis_client.setex(nhsno, timedelta(minutes=30), doc_uuid)
            redis_client.setex(doc_uuid, timedelta(minutes=30), xop)

            logger.info("CCDA document created", extra={
                "document_id": doc_uuid,
                "nhs_number": nhsno
            })

            # pprint(xml_ccda)
            with open(f"{nhsno}.xml", "w") as output:
                output.write(xmltodict.unparse(xml_ccda, pretty=True))

            return {"document_id": doc_uuid}
            
        except Exception as e:
            logger.error("Error processing GP Connect request", extra={
                "error": str(e),
                "nhs_number": nhsno
            })
            raise
