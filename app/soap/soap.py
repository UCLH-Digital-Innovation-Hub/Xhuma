"""
SOAP Handler Module
==================

This module implements SOAP message handling for IHE ITI transactions:

* ITI-47: Patient Demographics Query
* ITI-38: Cross Gateway Query
* ITI-39: Cross Gateway Retrieve

The module provides FastAPI routes for handling SOAP requests and responses,
integrating with Redis for caching and implementing NHS number validation.

Dependencies
-----------
* xmltodict: For XML processing
* fastapi: For HTTP routing
* redis: For caching

Configuration
------------
The module requires:

* Redis connection details
* SOAP namespace configurations
* Logging setup
"""

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


def log_info(req_body: str, res_body: str, client_ip: str, method: str, url: str, status_code: int) -> None:
    """
    Logs request and response information including metadata.

    :param req_body: The request body content
    :type req_body: str
    :param res_body: The response body content
    :type res_body: str
    :param client_ip: The client's IP address
    :type client_ip: str
    :param method: The HTTP method used
    :type method: str
    :param url: The requested URL
    :type url: str
    :param status_code: The response status code
    :type status_code: int
    """
    logging.info(f"Client IP: {client_ip}, Method: {method}, URL: {url}")
    logging.info(f"Request Body: {req_body}")
    logging.info(f"Response Body: {res_body}")
    logging.info(f"Status Code: {status_code}")


class LoggingRoute(APIRoute):
    """
    Custom route class that implements request logging.
    
    Extends FastAPI's APIRoute to add logging of request details.
    Captures and logs:
    
    * Request timestamp
    * Client IP
    * Request method
    * Request body
    * Response details
    """

    def get_route_handler(self) -> Callable:
        """
        Returns a custom route handler that includes logging functionality.

        :return: The modified route handler with logging
        :rtype: Callable
        """
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            logging.info(f"Handling request for {request.url}")
            req_body = await request.body()
            client_ip = request.headers.get("x-forwarded-for") or request.client.host
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

# SOAP namespace definitions
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
async def iti47(request: Request) -> Response:
    """
    Handles ITI-47 (Patient Demographics Query) requests.

    This endpoint processes PDQ requests by:
    1. Extracting NHS number and CEID from the request
    2. Mapping NHS number to CEID in Redis
    3. Performing PDS lookup
    4. Returning demographics in ITI-47 response format

    :param request: The incoming SOAP request
    :type request: Request
    :return: SOAP response containing patient demographics
    :rtype: Response
    :raises HTTPException: For invalid content type, missing NHS number, or missing CEID
    
    Example Request Body::

        <soap:Envelope>
            <soap:Header>
                <MessageID>uuid:example</MessageID>
            </soap:Header>
            <soap:Body>
                <PRPA_IN201305UV02>
                    <controlActProcess>
                        <queryByParameter>
                            <parameterList>
                                <livingSubjectId>
                                    <value root="2.16.840.1.113883.2.1.4.1" extension="9876543210"/>
                                </livingSubjectId>
                            </parameterList>
                        </queryByParameter>
                    </controlActProcess>
                </PRPA_IN201305UV02>
            </soap:Body>
        </soap:Envelope>
    """
    content_type = request.headers["Content-Type"]
    if "application/soap+xml" in content_type:
        body = await request.body()
        envelope = clean_soap(body)
        query_params = envelope["Body"]["PRPA_IN201305UV02"]["controlActProcess"][
            "queryByParameter"
        ]["parameterList"]
        for param in query_params["livingSubjectId"]:
            if param["value"]["@root"] == "2.16.840.1.113883.2.1.4.1":
                nhsno = param["value"]["@extension"]
            if param["value"]["@root"] == "1.2.840.114350.1.13.525.3.7.3.688884.100":
                ceid = param["value"]["@extension"]
        if not nhsno:
            raise HTTPException(
                status_code=400, detail=f"Invalid request, no nhs number found"
            )
        if not ceid:
            raise HTTPException(
                status_code=400, detail=f"Invalid request, no care everywhere id found"
            )
        print(f"Mapping NHSNO to CEID: {nhsno} -> {ceid}")
        client.set(ceid, nhsno)
        patient = await lookup_patient(nhsno)
        if not patient:
            print("Patient not found")
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
async def iti38(request: Request) -> Response:
    """
    Handles ITI-38 (Cross Gateway Query) requests.

    This endpoint processes document query requests by:
    1. Extracting and validating patient identifier
    2. Handling various ID formats (NHS number, CEID)
    3. Retrieving document metadata
    4. Returning metadata in ITI-38 response format

    :param request: The incoming SOAP request
    :type request: Request
    :return: SOAP response containing document metadata
    :rtype: Response
    :raises HTTPException: For invalid content type
    
    Example Request Body::

        <soap:Envelope>
            <soap:Body>
                <AdhocQueryRequest>
                    <AdhocQuery id="urn:uuid:14d4debf-8f97-4251-9a74-a90016b0af0d">
                        <Slot name="$XDSDocumentEntryPatientId">
                            <ValueList>
                                <Value>9876543210</Value>
                            </ValueList>
                        </Slot>
                    </AdhocQuery>
                </AdhocQueryRequest>
            </soap:Body>
        </soap:Envelope>
    """
    content_type = request.headers["Content-Type"]
    if "application/soap+xml" in content_type:
        body = await request.body()
        envelope = clean_soap(body)
        soap_body = envelope["Body"]
        slots = soap_body["AdhocQueryRequest"]["AdhocQuery"]["Slot"]
        query_id = soap_body["AdhocQueryRequest"]["AdhocQuery"]["@id"]

        patient_id = next(
            x["ValueList"]["Value"]
            for x in slots
            if x["@name"] == "$XDSDocumentEntryPatientId"
        )

        if not validateNHSnumber(patient_id):
            try:
                pattern = r"[0-9]{10}"
                poss_nhs = re.search(pattern, patient_id).group(0)
                if validateNHSnumber(poss_nhs):
                    patient_id = poss_nhs
            except:
                pattern = r"[A-Z0-9]{15}"
                ceid = re.search(pattern, patient_id).group(0)
                print(f"CEID: {ceid}")
                logging.info(f"Patient ID is CEID: {ceid}")
                patient_id = client.get(ceid)
                print(f"NHS no for CEID is: {patient_id}")
                logging.info(f"Mapped NHSNO is: {patient_id} from {ceid}")

        data = await iti_38_response(patient_id, ceid, query_id)
        return Response(content=data, media_type="application/soap+xml")
    else:
        raise HTTPException(
            status_code=400, detail=f"Content type {content_type} not supported"
        )


@router.post("/iti39")
async def iti39(request: Request) -> Response:
    """
    Handles ITI-39 (Cross Gateway Retrieve) requests.

    This endpoint processes document retrieval requests by:
    1. Extracting document unique identifier
    2. Retrieving document from Redis cache
    3. Returning document in ITI-39 response format

    :param request: The incoming SOAP request
    :type request: Request
    :return: SOAP response containing requested document
    :rtype: Response
    :raises HTTPException: For invalid content type, missing document ID, or document not found
    
    Example Request Body::

        <soap:Envelope>
            <soap:Header>
                <MessageID>uuid:example</MessageID>
            </soap:Header>
            <soap:Body>
                <RetrieveDocumentSetRequest>
                    <DocumentRequest>
                        <DocumentUniqueId>1234567890</DocumentUniqueId>
                    </DocumentRequest>
                </RetrieveDocumentSetRequest>
            </soap:Body>
        </soap:Envelope>
    """
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
