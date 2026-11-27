"""Outgoing NIR request models in the existing XmlModel/xmltodict convention.

These describe generated requests only. Incoming signed XML is verified before
any model extraction, and externally supplied registry metadata stays as XML.
Namespace declarations live on the payload that uses them so the shared SOAP
envelope can remain the same for GP and NIR messages.
"""

from uuid import uuid4

from pydantic import Field

from .models import TextElement, XmlModel


class OutgoingReplyTo(XmlModel):
    address: str = Field(default="http://www.w3.org/2005/08/addressing/anonymous", alias="a:Address")


class OutgoingHeader(XmlModel):
    action: TextElement = Field(alias="a:Action")
    message_id: str = Field(default_factory=lambda: f"urn:uuid:{uuid4()}", alias="a:MessageID")
    reply_to: OutgoingReplyTo = Field(default_factory=OutgoingReplyTo, alias="a:ReplyTo")
    destination: TextElement = Field(alias="a:To")


class ResponseOption(XmlModel):
    return_type: str = Field(default="LeafClass", alias="@returnType")
    return_composed_objects: str = Field(default="true", alias="@returnComposedObjects")


class QueryValues(XmlModel):
    values: list[str] = Field(alias="rim:Value")


class QuerySlot(XmlModel):
    name: str = Field(alias="@name")
    values: QueryValues = Field(alias="rim:ValueList")


class FindDocumentsQuery(XmlModel):
    # Fixed IHE stored-query ID, unrelated to the per-request MessageID.
    identifier: str = Field(default="urn:uuid:14d4debf-8f97-4251-9a74-a90016b0af0d", alias="@id")
    slots: list[QuerySlot] = Field(alias="rim:Slot")


class AdhocQueryRequest(XmlModel):
    xmlns_query: str = Field(default="urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0", alias="@xmlns:query")
    xmlns_rim: str = Field(default="urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0", alias="@xmlns:rim")
    response_option: ResponseOption = Field(default_factory=ResponseOption, alias="query:ResponseOption")
    query: FindDocumentsQuery = Field(alias="rim:AdhocQuery")


class QueryBody(XmlModel):
    request: AdhocQueryRequest = Field(alias="query:AdhocQueryRequest")


class DocumentRequest(XmlModel):
    home_community_id: str = Field(alias="xds:HomeCommunityId")
    repository_unique_id: str = Field(alias="xds:RepositoryUniqueId")
    document_unique_id: str = Field(alias="xds:DocumentUniqueId")


class RetrieveDocumentSetRequest(XmlModel):
    xmlns_xds: str = Field(default="urn:ihe:iti:xds-b:2007", alias="@xmlns:xds")
    documents: list[DocumentRequest] = Field(alias="xds:DocumentRequest", min_length=1)


class RetrievalBody(XmlModel):
    request: RetrieveDocumentSetRequest = Field(alias="xds:RetrieveDocumentSetRequest")


class SeriesRequest(XmlModel):
    series_instance_uid: str = Field(alias="@seriesInstanceUID")
    documents: list[DocumentRequest] = Field(alias="xds:DocumentRequest", min_length=1)


class StudyRequest(XmlModel):
    study_instance_uid: str = Field(alias="@studyInstanceUID")
    series: list[SeriesRequest] = Field(alias="xdsi:SeriesRequest", min_length=1)


class TransferSyntaxUIDList(XmlModel):
    uids: list[str] = Field(alias="xdsi:TransferSyntaxUID", min_length=1)


class RetrieveImagingDocumentSetRequest(XmlModel):
    xmlns_xdsi: str = Field(default="urn:ihe:rad:xdsi-b:2009", alias="@xmlns:xdsi")
    xmlns_xds: str = Field(default="urn:ihe:iti:xds-b:2007", alias="@xmlns:xds")
    studies: list[StudyRequest] = Field(alias="xdsi:StudyRequest", min_length=1)
    transfer_syntaxes: TransferSyntaxUIDList = Field(alias="xdsi:TransferSyntaxUIDList")


class ImagingBody(XmlModel):
    request: RetrieveImagingDocumentSetRequest = Field(alias="xdsi:RetrieveImagingDocumentSetRequest")
