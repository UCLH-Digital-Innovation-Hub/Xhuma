"""Typed IHE identifiers and results used by NIR discovery and retrieval.

These models keep the subset the MVP needs after parsing the SOAP XML. They
remain independent of GP Connect settings and its inbound SOAP models.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# These are statuses inside the registry's SOAP response. An HTTP 200 only
# confirms that the SOAP message arrived; it does not establish registry success.
XDS_SUCCESS_STATUS = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success"
XDS_FAILURE_STATUS = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Failure"


class NIRDocumentReference(BaseModel):
    """The three identifiers needed to route and identify a retrieved document."""

    # The community identifies the responding gateway, and the repository locates
    # the document store within it. Keep both alongside the document's unique ID:
    # the same document ID can appear in different sandbox communities.
    home_community_id: str = Field(min_length=1)
    repository_unique_id: str = Field(min_length=1)
    # XDSDocumentEntry.uniqueId is the retrieval ID, distinct from the registry
    # entry's UUID. For a RAD-75 image request, this is the DICOM SOP Instance UID.
    document_unique_id: str = Field(min_length=1)
    # Discovery may provide a MIME type, but the retrieval identifiers do not
    # require it. Retrieval responses can supply the actual document MIME type.
    mime_type: str | None = None


class NIRImagingSeriesRequest(BaseModel):
    """Individual DICOM object references belonging to one Series Instance UID."""

    series_instance_uid: str = Field(min_length=1)
    documents: list[NIRDocumentReference] = Field(min_length=1)


class NIRImagingStudyRequest(BaseModel):
    """Series grouped under a Study Instance UID for the RAD-75 request."""

    study_instance_uid: str = Field(min_length=1)
    series: list[NIRImagingSeriesRequest] = Field(min_length=1)


class NIRRetrievedDocument(NIRDocumentReference):
    """Document metadata together with the decoded report, manifest or image bytes."""

    # Binary DICOM and PDF payloads must stay as bytes throughout MIME parsing;
    # decoding them as text can replace bytes and corrupt the retrieved document.
    content: bytes


class NIRQueryResponse(BaseModel):
    """ITI-38 metadata and registry status, retaining any errors or warnings."""

    status: str
    documents: list[NIRDocumentReference] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        """Require protocol success and no reported gaps in this MVP's results."""

        # A gateway can return Success with warnings about a failed community.
        # Keep the original status, but do not present that result as fully checked.
        return self.status == XDS_SUCCESS_STATUS and not self.errors


class NIRRetrieveResponse(BaseModel):
    """ITI-39 or RAD-75 content, retaining successful documents and any failures."""

    status: str
    documents: list[NIRRetrievedDocument] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        """Include parsing and missing-attachment errors in the success decision."""

        return self.status == XDS_SUCCESS_STATUS and not self.errors
