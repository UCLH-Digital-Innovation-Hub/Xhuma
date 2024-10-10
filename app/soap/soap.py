import json
import logging
import re
from datetime import datetime
from typing import Any, Callable, Dict
from urllib.request import Request

import xmltodict
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.routing import APIRoute
from starlette.background import BackgroundTask

from ..ccda.helpers import clean_soap, validateNHSnumber
from ..pds.pds import lookup_patient
from ..redis_connect import redis_connect
from .responses import iti_38_response, iti_39_response, iti_47_response


# Function to log request and response bodies, including IP and metadata
def log_info(req_body, res_body, client_ip, method, url, status_code):
    logging.info(f"Client IP: {client_ip}, Method: {method}, URL: {url}")
    logging.info(f"Request Body: {req_body}")
    logging.info(f"Response Body: {res_body}")
    logging.info(f"Status Code: {status_code}")


class LoggingRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            logging.info(f"Handling request for {request.url}")
            # Log request body
            req_body = await request.body()

            # Get client IP from headers
            client_ip = request.headers.get("x-forwarded-for") or request.client.host

            # log time
            logging.info(f"Time: {datetime.now()}")

            method = request.method
            logging.info(f"Method: {method}")

            logging.info(f"Client IP: {client_ip}")
            logging.info(f"Request Body: {req_body}")

            return await original_route_handler(request)

        return custom_route_handler


router = APIRouter(prefix="/SOAP", route_class=LoggingRoute)

logging.basicConfig(filename="info.log", level=logging.INFO)

client = redis_connect()

NAMESPACES = (
    {
        "http://www.w3.org/2003/05/soap-envelope": None,
        "http://www.w3.org/2005/08/addressing": None,
        "urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0": None,
        "urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0": None,
        "urn:ihe:iti:xds-b:2007": None,
        "soap": None,
    },
)


@router.post("/iti47")
async def iti47(request: Request):
    content_type = request.headers["Content-Type"]
    if "application/soap+xml" in content_type:
        body = await request.body()
        envelope = clean_soap(body)
        query_params = envelope["Body"]["PRPA_IN201305UV02"]["controlActProcess"][
            "queryByParameter"
        ]["parameterList"]
        # for each query parameter fir the patient id with the root for nhsno
        for param in query_params["livingSubjectId"]:
            # print(param)
            if param["value"]["@root"] == "2.16.840.1.113883.2.1.4.1":
                nhsno = param["value"]["@extension"]
            if param["value"]["@root"] == "1.2.840.114350.1.13.525.3.7.3.688884.100":
                ceid = param["value"]["@extension"]
        # if theres no nhsno then raise an error
        if not nhsno:
            raise HTTPException(
                status_code=400, detail=f"Invalid request, no nhs number found"
            )

        if not ceid:
            raise HTTPException(
                status_code=400, detail=f"Invalid request, no care everywhere id found"
            )

        # map nhsno to ceid in redis
        client.set(ceid, nhsno)

        patient = await lookup_patient(nhsno)
        # if the patient is not found then raise an error
        if not patient:
            print("Patient not found")
        else:
            pass
            # print(patient)

        data = await iti_47_response(
            envelope["Header"]["MessageID"],
            patient,
            ceid,
            envelope["Body"]["PRPA_IN201305UV02"]["controlActProcess"][
                "queryByParameter"
            ],
        )
        return Response(content=data, media_type="application/soap+xml")
    else:
        raise HTTPException(
            status_code=400, detail=f"Content type {content_type} not supported"
        )


@router.post("/iti38")
async def iti38(request: Request):
    content_type = request.headers["Content-Type"]
    if "application/soap+xml" in content_type:
        body = await request.body()
        envelope = clean_soap(body)
        soap_body = envelope["Body"]
        slots = soap_body["AdhocQueryRequest"]["AdhocQuery"]["Slot"]
        query_id = soap_body["AdhocQueryRequest"]["AdhocQuery"]["@id"]

        # TODO test this for cases when multiple id's come through
        patient_id = next(
            x["ValueList"]["Value"]
            for x in slots
            if x["@name"] == "$XDSDocumentEntryPatientId"
        )

        # check if patient id is valid nhs number
        if not validateNHSnumber(patient_id):
            # assume patient id is ceid
            # ceid will be in form \'UHL5MFM2ZLPQCW5^^^&amp;1.2.840.114350.1.13.525.3.7.3.688884.100&amp;ISO\'
            pattern = r"[A-Z0-9]{15}"
            # patient_id = patient_id.split("^^^")[0]
            # patient_id = patient_id.replace(patient_id[15:], "")
            ceid = re.search(pattern, patient_id).group(0)

            print(f"CEID: {ceid}")
            logging.info(f"Patient ID is CEID: {ceid}")

            # retrieve ceid/nhsno mapping from redis
            patient_id = client.get(ceid)
            print(f"NHS no for CEID is: {patient_id}")
            logging.info(f"Mapped NHSNO is: {patient_id}")

        data = await iti_38_response(patient_id, ceid, query_id)
        return Response(content=data, media_type="application/soap+xml")
    else:
        raise HTTPException(
            status_code=400, detail=f"Content type {content_type} not supported"
        )


@router.post("/iti39")
async def iti39(request: Request):
    content_type = request.headers["Content-Type"]
    if "application/soap+xml" in content_type:
        body = await request.body()
        envelope = clean_soap(body)
        try:
            document_id = envelope["Body"]["RetrieveDocumentSetRequest"][
                "DocumentRequest"
            ]["DocumentUniqueId"]
        except:
            raise HTTPException(status_code=404, detail=f"DocumentUniqueId not found")

        document = client.get(document_id)

        if document is not None:
            # return ITI39 response
            message_id = envelope["Header"]["MessageID"]
            data = await iti_39_response(message_id, document_id, document)
            return Response(content=data, media_type="application/soap+xml")
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Document with Id {document_id} not found or is empty",
            )
    else:
        raise HTTPException(
            status_code=400, detail=f"Content type {content_type} not supported"
        )
