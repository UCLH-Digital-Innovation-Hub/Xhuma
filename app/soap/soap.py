"""
SOAP Handler Module

This module implements SOAP message handling for IHE ITI transactions:
- ITI-38: Cross Gateway Query
- ITI-39: Cross Gateway Retrieve

The module provides FastAPI routes for handling SOAP requests and responses,
integrating with Redis for caching and implementing NHS number validation.
"""

import hashlib
import logging
import re
from datetime import datetime
from email.message import Message
from typing import Callable

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.routing import APIRoute
from opentelemetry import trace
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.audit.audit import attempt_audit
from app.audit.models import AuditOutcome
from app.telemetry import measure, record_cache

from ..ccda.helpers import clean_soap, extract_soap_request, validateNHSnumber
from ..pds.pds import lookup_patient
from ..redis_connect import redis_connect
from .faults import SoapFault
from .models import ITI38Request, ITI39Request, ITI55Request
from .responses import (
    deliver_iti39_response,
    iti_38_error,
    iti_38_response,
    iti_39_error,
    iti_39_response,
    iti_55_error,
    iti_55_response,
)
from .saml_helper import extract_saml_attributes


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
    logging.info("Request Body: [REDACTED FOR PHI SECURITY]")
    logging.info("Response Body: [REDACTED FOR PHI SECURITY]")
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
            client_ip = request.headers.get("x-forwarded-for") or request.client.host
            method = request.method
            logging.info(f"Time: {datetime.now()}")
            logging.info(f"Method: {method}")
            logging.info(f"Client IP: {client_ip}")
            logging.info("Request Body: [REDACTED FOR PHI SECURITY]")
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
    """Signal an ITI-55 application error returned as application/soap+xml."""

    def __init__(self, message_id: str, reason: str, query_params: dict, http_status: int = 200):
        self.message_id = message_id
        self.reason = reason
        self.query_params = query_params
        self.http_status = http_status
        super().__init__(reason)


def register_handlers(app: FastAPI):
    @app.exception_handler(SoapFault)
    async def soap_fault_handler(request: Request, exc: SoapFault):
        return exc.response()

    @app.exception_handler(SoapError)
    async def soap_error_handler(request: Request, exc: SoapError):
        xml = await iti_55_error(exc.message_id, exc.query_params, exc.reason)
        return Response(content=xml, media_type="application/soap+xml", status_code=exc.http_status)


def _message_id(envelope: dict) -> str | None:
    header = envelope.get("Header")
    value = header.get("MessageID") if isinstance(header, dict) else None
    return value if isinstance(value, str) else None


def _accepts_soap(content_type: str, *, multipart: bool = False) -> bool:
    header = Message()
    header["Content-Type"] = content_type
    if header.get_content_type() == "application/soap+xml":
        return True
    return (
        multipart
        and header.get_content_type() == "multipart/related"
        and header.get_param("type") == "application/xop+xml"
        and (header.get_param("start-info") or "").partition(";")[0] == "application/soap+xml"
    )


def _parse_envelope(body: bytes, *, mime_wrapped: bool = False) -> dict:
    try:
        if mime_wrapped and not body.lstrip().startswith(b"<"):
            body = extract_soap_request(body.decode("utf-8"))
        envelope = clean_soap(body)
    except (HTTPException, ValueError):
        raise SoapFault("Malformed XML in request body or missing SOAP envelope") from None
    if not isinstance(envelope, dict):
        raise SoapFault("Missing SOAP envelope content")
    return envelope


@router.post(
    "/iti55",
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        415: {"description": "Unsupported Media Type"},
    },
)
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
    if _accepts_soap(content_type):
        body = await request.body()
        envelope = _parse_envelope(body)
        saml_attrs = extract_saml_attributes(envelope)

        try:
            soap_request = ITI55Request.model_validate(envelope)
            query = soap_request.query
            query_data = query.to_xml_dict() if query else {}
            nhsno = soap_request.patient_identifier("2.16.840.1.113883.2.1.4.1")
            message_id = soap_request.header.message_id
        except ValidationError:
            raise SoapFault("Malformed ITI-55 request", message_id=_message_id(envelope)) from None
        if query is None:
            raise SoapFault("Missing ITI-55 queryByParameter", message_id=message_id)

        # OpenTelemetry trace propagation
        span = trace.get_current_span()
        if span and span.is_recording():
            if message_id:
                span.set_attribute("soap.message_id", message_id)
            if nhsno:
                hashed_nhs = hashlib.sha256(str(nhsno).encode("utf-8")).hexdigest()
                span.set_attribute("patient.nhs_number_hashed", hashed_nhs)

        if not nhsno:
            data = await iti_55_error(
                message_id=message_id or "Unknown",
                error_text="No NHS number found in request",
                query=query_data,
            )
            return Response(content=data, media_type="application/soap+xml")

        try:
            patient = await lookup_patient(nhsno, request=request, saml=saml_attrs)
        except HTTPException as e:
            if e.status_code == 404:
                patient = None
            else:
                raise SoapFault("Unable to locate patient in PDS", code="Receiver", message_id=message_id) from None

        if (not patient) or ("resourceType" in patient and patient["resourceType"] == "OperationOutcome"):
            data = await iti_55_error(
                message_id=message_id,
                error_text=f"Patient with NHS number {nhsno} not found",
                query=query_data,
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
            await attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="iti55_patient_discovery",
                outcome=AuditOutcome.deny,
                message_id=envelope["Header"]["MessageID"],
                detail={"error": "Patient record has restricted access"},
            )
            data = await iti_55_error(
                message_id=message_id,
                error_text="Patient record has restricted access",
                query=query_data,
            )
            return Response(content=data, media_type="application/soap+xml")

        data = await iti_55_response(
            message_id,
            patient,
            query_data,
        )
        return Response(content=data, media_type="application/soap+xml")
    else:
        raise HTTPException(status_code=415, detail=f"Content type {content_type} not supported")


@router.post(
    "/iti38",
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        415: {"description": "Unsupported Media Type"},
    },
)
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
    content_type = request.headers.get("Content-Type", "")
    if _accepts_soap(content_type):
        body = await request.body()
        envelope = _parse_envelope(body)
        saml_attrs = extract_saml_attributes(envelope)

        try:
            soap_request = ITI38Request.model_validate(envelope)
            adhoc_query = soap_request.body.query
            query_id = adhoc_query.query_id if adhoc_query else "unknown"
            patient_id = adhoc_query.slot_value("$XDSDocumentEntryPatientId") if adhoc_query else None
            message_id = soap_request.header.message_id
        except ValidationError:
            raise SoapFault("Malformed ITI-38 request", message_id=_message_id(envelope)) from None
        if adhoc_query is None:
            raise SoapFault("Missing ITI-38 AdhocQuery", message_id=message_id)
        if not patient_id:
            data = await iti_38_error(
                message_id or query_id,
                "Required parameter $XDSDocumentEntryPatientId is missing",
                error_code="XDSStoredQueryMissingParam",
            )
            return Response(content=data, media_type="application/soap+xml")

        # OpenTelemetry trace propagation
        span = trace.get_current_span()
        if span and span.is_recording():
            if message_id:
                span.set_attribute("soap.message_id", message_id)
            if query_id:
                span.set_attribute("soap.query_id", query_id)
            try:
                poss_nhs = re.search(r"[0-9]{10}", patient_id).group(0)
                if poss_nhs:
                    hashed_nhs = hashlib.sha256(poss_nhs.encode("utf-8")).hexdigest()
                    span.set_attribute("patient.nhs_number_hashed", hashed_nhs)
            except Exception:
                pass
        if not validateNHSnumber(patient_id):
            try:
                pattern = r"[0-9]{10}"
                poss_nhs = re.search(pattern, str(patient_id)).group(0)
                if validateNHSnumber(poss_nhs):
                    patient_id = poss_nhs
                else:
                    raise AttributeError("Invalid NHS number checksum")
            except (AttributeError, TypeError):
                # print("No valid NHS number found in patient ID field")
                logging.info("No valid NHS number found in patient ID field")
                data = await iti_38_error(message_id or query_id, "Invalid NHS number format in request")
                return Response(content=data, media_type="application/soap+xml")
        data = await iti_38_response(request, patient_id, "NOCEID", query_id, saml_attrs, message_id=message_id)
        return Response(content=data, media_type="application/soap+xml")
    else:
        raise HTTPException(status_code=415, detail=f"Content type {content_type} not supported")


@router.post(
    "/iti39",
    responses={
        202: {"description": "Accepted for asynchronous delivery"},
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        415: {"description": "Unsupported Media Type"},
    },
)
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
    content_type = request.headers.get("Content-Type", "")
    if _accepts_soap(content_type, multipart=True):
        body = await request.body()
        envelope = _parse_envelope(body, mime_wrapped=True)
        saml_attrs = extract_saml_attributes(envelope)

        try:
            soap_request = ITI39Request.model_validate(envelope)
        except ValidationError:
            raise SoapFault("Malformed ITI-39 request", message_id=_message_id(envelope)) from None
        message_id = soap_request.header.message_id
        retrieve_request = soap_request.body.retrieve_document_set_request
        document_request = retrieve_request.first_document if retrieve_request else None
        document_id = document_request.document_unique_id if document_request else None
        if not document_id:
            raise SoapFault("DocumentUniqueId not found", message_id=message_id)

        # OpenTelemetry trace propagation
        span = trace.get_current_span()
        if span and span.is_recording():
            if message_id:
                span.set_attribute("soap.message_id", message_id)
            if document_id:
                span.set_attribute("soap.document_id", document_id)

        error_code = None
        error_text = None
        try:
            with measure("document.cache.retrieve") as span:
                document, doc_nhsno_bytes = await client.hmget(f"document:{document_id}", "content", "nhs_number")
                span.set_attribute("cache.hit", document is not None)
            record_cache("retrieval", document is not None)
        except RedisError:
            document, doc_nhsno_bytes = None, None
            error_code = "XDSRepositoryError"
            error_text = "Unable to retrieve document from cache"
        doc_nhsno = doc_nhsno_bytes.decode("utf-8") if doc_nhsno_bytes else None

        if document is not None:
            data = await iti_39_response(message_id, document_id, document)
            outcome = AuditOutcome.ok
            detail = {}
        else:
            error_code = error_code or "XDSDocumentUniqueIdError"
            data = await iti_39_error(message_id, document_id, error_code=error_code, error_text=error_text)
            outcome = AuditOutcome.fail
            detail = {"error": error_text or "Document not found"}

        return await deliver_iti39_response(
            request,
            soap_request,
            saml_attrs,
            document_id,
            doc_nhsno,
            data,
            outcome=outcome,
            error_code=error_code,
            detail=detail,
        )
    else:
        raise HTTPException(status_code=415, detail=f"Content type {content_type} not supported")
