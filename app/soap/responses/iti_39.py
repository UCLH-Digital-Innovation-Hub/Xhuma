import uuid

import xmltodict

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


async def iti_39_error(message_id: str, document_id: str) -> str:
    """Generate an ITI-39 missing-document registry error response."""

    body = ITI39ErrorResponseBody(
        response=ITI39ErrorRetrieveDocumentSetResponse(
            registry_response=ITI39RegistryResponse(
                status=XDS_FAILURE_STATUS,
                registry_error_list=ITI39RegistryErrorList(
                    highest_severity=XDS_ERROR_SEVERITY,
                    error=RegistryError(
                        error_code="XDSDocumentUniqueIdError",
                        code_context=f"Document with Id {document_id} not found",
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
