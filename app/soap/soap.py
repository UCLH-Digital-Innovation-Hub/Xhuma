"""
SOAP Handler Module

This module implements SOAP message handling for IHE ITI transactions:
- ITI-47: Patient Demographics Query
- ITI-38: Cross Gateway Query
- ITI-39: Cross Gateway Retrieve

The module provides FastAPI routes for handling SOAP requests and responses,
integrating with Redis for caching and implementing NHS number validation.
"""

import logging
import re
import uuid
from datetime import datetime
from email import charset
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Callable, Dict
from urllib.request import Request

import httpx
import xmltodict
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.routing import APIRoute
from starlette.background import BackgroundTask

from ..ccda.helpers import clean_soap, extract_soap_request, validateNHSnumber
from ..pds.pds import lookup_patient
from ..redis_connect import redis_connect
from .audit import process_saml_attributes
from .responses import (create_envelope, create_header, iti_38_response,
                        iti_39_response, iti_47_response, iti_55_error,
                        iti_55_response)


def log_info(req_body, res_body, client_ip, method, url, status_code):
    """
    Logs request and response information including metadata.

    Args:
        req_body: The request body content
        res_body: The response body content
        client_ip: The client's IP address
        method: The HTTP method used
        url: The requested URL
        status_code: The response status code
    """
    logging.info(f"Client IP: {client_ip}, Method: {method}, URL: {url}")
    logging.info(f"Request Body: {req_body}")
    logging.info(f"Response Body: {res_body}")
    logging.info(f"Status Code: {status_code}")


class LoggingRoute(APIRoute):
    """
    Custom route class that implements request logging.
    Extends FastAPI's APIRoute to add logging of request details.
    """

    def get_route_handler(self) -> Callable:
        """
        Returns a custom route handler that includes logging functionality.

        Returns:
            Callable: The modified route handler with logging
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

client = redis_connect  # Use the redis_connect instance directly

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


class SoapError(Exception):
    """Signal an ITI-55 SOAP fault that should be returned as application/soap+xml."""

    def __init__(
        self, message_id: str, reason: str, query_params: dict, http_status: int = 200
    ):
        self.message_id = message_id
        self.reason = reason
        self.query_params = query_params
        self.http_status = http_status
        super().__init__(reason)


def register_handlers(app: FastAPI):
    @app.exception_handler(SoapError)
    async def soap_error_handler(request: Request, exc: SoapError):
        xml = await iti_55_error(exc.message_id, exc.query_params, exc.reason)
        return Response(
            content=xml, media_type="application/soap+xml", status_code=exc.http_status
        )


@router.post("/iti55")
async def iti55(request: Request):
    """
    Handles ITI-55 (Cross Gateway Patient Discovery) requests.

    This endpoint processes PDQ requests by:
    1. Extracting NHS number from the request
    2. Performing PDS lookup
    3. Returning demographics in ITI-55 response format

    Args:
        request (Request): The incoming SOAP request

    Returns:
        Response: SOAP response containing patient demographics

    Raises:
        HTTPException: For invalid content type, missing NHS number, or missing CEID
    """
    content_type = request.headers.get("Content-Type", "")
    if "application/soap+xml" in content_type:
        body = await request.body()
        envelope = clean_soap(body)
        query_params = envelope["Body"]["PRPA_IN201305UV02"]["controlActProcess"][
            "queryByParameter"
        ]["parameterList"]

        nhsno = None
        try:
            for param in query_params["livingSubjectId"]["value"]:
                if param["@root"] == "2.16.840.1.113883.2.1.4.1":
                    nhsno = param["@extension"]
                    # print(f"NHSNO: {nhsno}")

        except Exception:
            nhsno = None

        if not nhsno:
            data = await iti_55_error(
                envelope["Header"]["MessageID"],
                "No NHS number found in request",
                envelope["Body"]["PRPA_IN201305UV02"]["controlActProcess"][
                    "queryByParameter"
                ],
            )
            return Response(content=data, media_type="application/soap+xml")

        patient = await lookup_patient(nhsno)
        # TODO implement checking of demographics

        if (not patient) or (
            "resourceType" in patient and patient["resourceType"] == "OperationOutcome"
        ):
            data = await iti_55_error(
                envelope["Header"]["MessageID"],
                f"Patient with NHS number {nhsno} not found",
                envelope["Body"]["PRPA_IN201305UV02"]["controlActProcess"][
                    "queryByParameter"
                ],
            )
            return Response(content=data, media_type="application/soap+xml")

        security_code = None
        if (
            patient
            and "meta" in patient
            and "security" in patient["meta"]
            and isinstance(patient["meta"]["security"], list)
            and len(patient["meta"]["security"]) > 0
            and "code" in patient["meta"]["security"][0]
        ):
            security_code = patient["meta"]["security"][0]["code"]

        if security_code != "U":
            data = await iti_55_error(
                envelope["Header"]["MessageID"],
                "Patient record has restricted access",
                envelope["Body"]["PRPA_IN201305UV02"]["controlActProcess"][
                    "queryByParameter"
                ],
            )
            return Response(content=data, media_type="application/soap+xml")

        data = await iti_55_response(
            envelope["Header"]["MessageID"],
            patient,
            envelope["Body"]["PRPA_IN201305UV02"]["controlActProcess"][
                "queryByParameter"
            ],
        )
        return Response(content=data, media_type="application/soap+xml")
    else:
        raise HTTPException(
            status_code=400, detail=f"Content type {content_type} not supported"
        )


@router.post("/iti47")
async def iti47(request: Request):
    """
    Handles ITI-47 (Patient Demographics Query) requests.

    This endpoint processes PDQ requests by:
    1. Extracting NHS number and CEID from the request
    2. Mapping NHS number to CEID in Redis
    3. Performing PDS lookup
    4. Returning demographics in ITI-47 response format

    Args:
        request (Request): The incoming SOAP request

    Returns:
        Response: SOAP response containing patient demographics

    Raises:
        HTTPException: For invalid content type, missing NHS number, or missing CEID
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
        # TODO add audit stuff here too
        patient = await lookup_patient(nhsno)
        print(f"Patient: {patient}")
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
async def iti38(request: Request):
    """
    Handles ITI-38 (Cross Gateway Query) requests.

    This endpoint processes document query requests by:
    1. Extracting and validating patient identifier
    2. Handling various ID formats (NHS number, CEID)
    3. Retrieving document metadata
    4. Returning metadata in ITI-38 response format

    Args:
        request (Request): The incoming SOAP request

    Returns:
        Response: SOAP response containing document metadata

    Raises:
        HTTPException: For invalid content type
    """
    content_type = request.headers["Content-Type"]
    if "application/soap+xml" in content_type:
        body = await request.body()
        print("-" * 40)
        # print(f"Received body: {body}")
        envelope = clean_soap(body)
        # print(f"Envelope: {envelope["Header"]["Security"]["Assertion"]}")
        # for key, value in envelope["Header"]["Security"]["Assertion"].items():
        #     print(f"{key}: {value}")

        # for item in envelope["Header"]["Security"]["Assertion"].items():
        #     print(f"{item[0]}: {item[1]}")
        saml_attrs = process_saml_attributes(
            envelope["Header"]["Security"]["Assertion"]["AttributeStatement"]
            # envelope["Header"]["Security"]["AttributeStatement"]
        )

        print(f"SAML Attributes: {saml_attrs}")

        soap_body = envelope["Body"]
        slots = soap_body["AdhocQueryRequest"]["AdhocQuery"]["Slot"]
        query_id = soap_body["AdhocQueryRequest"]["AdhocQuery"]["@id"]

        patient_id = next(
            x["ValueList"]["Value"]
            for x in slots
            if x["@name"] == "$XDSDocumentEntryPatientId"
        )

        print(f"Patient ID: {patient_id}")
        # TODO rewrite this pattern if we don't need to map CEID to NHSNO
        if not validateNHSnumber(patient_id):
            try:
                pattern = r"[0-9]{10}"
                poss_nhs = re.search(pattern, patient_id).group(0)
                print(f"Possible NHS number: {poss_nhs}")
                print(validateNHSnumber(poss_nhs))
                if validateNHSnumber(poss_nhs):
                    patient_id = poss_nhs
                    data = await iti_38_response(
                        request, patient_id, "NOCEID", query_id, saml_attrs
                    )
            except AttributeError:
                print(f"No valid NHS number found in patient ID's {patient_id}")
                logging.info(f"No valid NHS number found in patient ID's {patient_id}")
        else:
            data = await iti_38_response(
                request, patient_id, "NOCEID", query_id, saml_attrs
            )
        return Response(content=data, media_type="application/soap+xml")
    else:
        raise HTTPException(
            status_code=400, detail=f"Content type {content_type} not supported"
        )


@router.post("/iti39")
async def iti39(request: Request):
    """
    Handles ITI-39 (Cross Gateway Retrieve) requests.

    This endpoint processes document retrieval requests by:
    1. Extracting document unique identifier
    2. Retrieving document from Redis cache
    3. Returning document in ITI-39 response format

    Args:
        request (Request): The incoming SOAP request

    Returns:
        Response: SOAP response containing requested document

    Raises:
        HTTPException: For invalid content type, missing document ID, or document not found
    """
    content_type = request.headers["Content-Type"]
    if "application/soap+xml" in content_type:
        body = await request.body()
        soap = extract_soap_request(body.decode("utf-8"))
        envelope = clean_soap(soap)
        message_id = envelope["Header"]["MessageID"]
        try:
            document_id = envelope["Body"]["RetrieveDocumentSetRequest"][
                "DocumentRequest"
            ]["DocumentUniqueId"]
        except:
            raise HTTPException(status_code=404, detail=f"DocumentUniqueId not found")

        document = client.get(document_id)

        if document is not None:
            data = await iti_39_response(message_id, document_id, document)
            # mime encode the data
            boundary = f"uuid:{uuid.uuid4()}"
            mime_message = MIMEMultipart(
                "related", boundary=boundary, type="application/xop+xml"
            )

            # specify 8bit encoding so it doesn't 64bit encode everything
            ch = charset.Charset("utf-8")
            ch.body_encoding = "8bit"

            soap_mime = MIMEText("")
            soap_mime.set_charset(ch)
            # add the data after specifing the charset
            soap_mime.set_payload(data)
            soap_mime.replace_header("Content-Transfer-Encoding", "8bit")
            soap_mime.add_header("Content-Id", "<http://tempuri.org/0>")
            soap_mime.add_header(
                "Content-Type",
                'application/xop+xml; charset="utf-8"; type="application/soap+xml"',
            )
            mime_message.attach(soap_mime)

            mime_string = mime_message.as_string()
            headers = {"Content-Type": f'multipart/related; boundary="{boundary}"'}

            # if there's not an anonymous address in the reply to header, send the response to that address
            if (
                envelope["Header"]["ReplyTo"]["Address"]
                and envelope["Header"]["ReplyTo"]["Address"]
                != "http://www.w3.org/2005/08/addressing/anonymous"
            ):
                print(
                    f"Sending response to: {envelope['Header']['ReplyTo']['Address']}"
                )
                return Response(
                    content=mime_string.encode("utf-8"),
                    headers=headers,
                    background=BackgroundTask(
                        lambda: httpx.post(
                            envelope["Header"]["ReplyTo"]["Address"],
                            data=mime_string.encode("utf-8"),
                            headers=headers,
                        )
                    ),
                )

            return Response(content=data, media_type="application/soap+xml")
        else:
            # return iti39 error
            body = {
                "ns4:RetrieveDocumentSetResponse": {
                    "@xmlns:ns4": "urn:ihe:iti:xds-b:2007",
                    "@xmlns:ns8": "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0",
                    "ns8:RegistryResponse": {
                        "@id": uuid.uuid4(),
                        "@status": "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Failure",
                        # "@xmlns": "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0",
                        "RegistryErrorList": {
                            "RegistryError": {
                                "@errorCode": "XDSDocumentUniqueIdError",
                                "@codeContext": f"Document with Id {document_id} not found",
                                "@severity": "urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error",
                            }
                        },
                    },
                },
            }
            soap_response = create_envelope(
                create_header(
                    "urn:ihe:iti:2007:CrossGatewayRetrieveResponse", message_id
                ),
                body,
            )
            error_response = xmltodict.unparse(
                soap_response, full_document=False, pretty=True
            )
            return Response(
                content=error_response,
                media_type="application/soap+xml",
            )
            # raise HTTPException(
            #     status_code=404,
            #     detail=f"Document with Id {document_id} not found or is empty",
            # )
    else:
        raise HTTPException(
            status_code=400, detail=f"Content type {content_type} not supported"
        )
