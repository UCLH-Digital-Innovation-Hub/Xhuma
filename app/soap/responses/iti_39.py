import logging
import os
import urllib.parse
import uuid
from email import charset
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
import xmltodict
from fastapi import Response
from starlette.background import BackgroundTask

from ...audit.audit import attempt_audit
from ...audit.models import AuditOutcome
from ..faults import SoapFault
from ..models import (
    XDS_ERROR_SEVERITY,
    XDS_FAILURE_STATUS,
    ITI39DocumentResponse,
    ITI39ErrorResponseBody,
    ITI39ErrorRetrieveDocumentSetResponse,
    ITI39RegistryErrorList,
    ITI39RegistryResponse,
    ITI39ResponseBody,
    RegistryError,
    ResponseHeader,
    RetrieveDocumentSetResponse,
    SoapEnvelope,
    TextElement,
)
from .constants import COMMUNITY_ID, REGISTRY_ID


async def iti_39_response(message_id: str, document_id: str, document):
    """Generate a successful ITI-39 document retrieval response."""

    body = ITI39ResponseBody(
        response=RetrieveDocumentSetResponse(
            registry_response=ITI39RegistryResponse(
                identifier=str(uuid.uuid4()),
            ),
            document_response=ITI39DocumentResponse(
                home_community_id=TextElement(text=f"urn:oid:{COMMUNITY_ID}"),
                repository_unique_id=TextElement(text=REGISTRY_ID),
                document_unique_id=TextElement(text=document_id),
                document=document,
            ),
        )
    )
    soap_response = SoapEnvelope.create(
        ResponseHeader.create("urn:ihe:iti:2007:CrossGatewayRetrieveResponse", message_id),
        body,
    )
    return xmltodict.unparse(soap_response.to_xml_dict(), pretty=True)


async def iti_39_error(
    message_id: str,
    document_id: str,
    *,
    error_code: str = "XDSDocumentUniqueIdError",
    error_text: str | None = None,
) -> str:
    """Generate an ITI-39 registry error, defaulting to document unavailable."""

    body = ITI39ErrorResponseBody(
        response=ITI39ErrorRetrieveDocumentSetResponse(
            registry_response=ITI39RegistryResponse(
                status=XDS_FAILURE_STATUS,
                registry_error_list=ITI39RegistryErrorList(
                    highest_severity=XDS_ERROR_SEVERITY,
                    error=RegistryError(
                        error_code=error_code,
                        code_context=error_text or f"Document with Id {document_id} not found",
                        # Temporarily omit location to check the client's retry behaviour.
                        # location=f"urn:oid:{COMMUNITY_ID}",
                        severity=XDS_ERROR_SEVERITY,
                    ),
                ),
            )
        ),
    )
    soap_response = SoapEnvelope.create(
        ResponseHeader.create("urn:ihe:iti:2007:CrossGatewayRetrieveResponse", message_id),
        body,
    )
    return xmltodict.unparse(soap_response.to_xml_dict(), full_document=False, pretty=True)


async def deliver_iti39_response(
    request,
    soap_request,
    saml_attrs,
    document_id,
    nhs_number,
    data,
    *,
    outcome,
    error_code,
    detail,
):
    """Audit before releasing either a direct response or an asynchronous acknowledgement."""
    message_id = soap_request.header.message_id
    reply_to = soap_request.header.reply_to.address if soap_request.header.reply_to else None
    asynchronous = bool(reply_to and reply_to != "http://www.w3.org/2005/08/addressing/anonymous")
    if asynchronous:
        try:
            parsed_url = urllib.parse.urlparse(reply_to)
            hostname = parsed_url.hostname
        except ValueError:
            parsed_url, hostname = None, None
        rejection = None
        if parsed_url is None or parsed_url.scheme != "https":
            rejection = "ReplyTo must use https"
        else:
            allowed_domains = [
                d.strip().lower() for d in os.getenv("ALLOWED_REPLY_TO_DOMAINS", ".nhs.uk").split(",") if d.strip()
            ]
            if not any(hostname and hostname.endswith(domain) for domain in allowed_domains):
                rejection = "ReplyTo domain not allowed"
        if rejection:
            await attempt_audit(
                request=request,
                nhs_number=nhs_number,
                saml=saml_attrs,
                action="iti39_document_retrieve",
                outcome=AuditOutcome.deny,
                message_id=message_id,
                document_id=document_id,
                detail={"error": rejection, "reply_to": reply_to},
            )
            raise SoapFault(
                rejection,
                subcode="wsa:InvalidAddressingHeader",
                message_id=message_id,
                problem_header="wsa:ReplyTo",
            )

    delivery_detail = {**detail, "delivery_mode": "async" if asynchronous else "sync"}
    if asynchronous:
        delivery_detail["reply_to"] = reply_to
    await attempt_audit(
        request=request,
        nhs_number=nhs_number,
        saml=saml_attrs,
        action="iti39_document_retrieve",
        outcome=outcome,
        error_code=error_code,
        message_id=message_id,
        document_id=document_id,
        detail=delivery_detail,
    )
    if not asynchronous:
        return Response(content=data, media_type="application/soap+xml")

    boundary = f"uuid:{uuid.uuid4()}"
    mime_message = MIMEMultipart("related", boundary=boundary, type="application/xop+xml")
    encoding = charset.Charset("utf-8")
    encoding.body_encoding = "8bit"
    soap_mime = MIMEText("")
    soap_mime.set_charset(encoding)
    soap_mime.set_payload(data)
    soap_mime.replace_header("Content-Transfer-Encoding", "8bit")
    soap_mime.add_header("Content-Id", "<http://tempuri.org/0>")
    soap_mime.add_header("Content-Type", 'application/xop+xml; charset="utf-8"; type="application/soap+xml"')
    mime_message.attach(soap_mime)
    payload = mime_message.as_string().encode("utf-8")
    headers = {"Content-Type": f'multipart/related; boundary="{boundary}"'}

    def send_post():
        try:
            response = httpx.post(reply_to, data=payload, headers=headers, timeout=10.0)
            response.raise_for_status()
        except Exception as e:
            logging.error("ITI-39 asynchronous response delivery failed (%s)", type(e).__name__)

    return Response(status_code=202, background=BackgroundTask(send_post))
