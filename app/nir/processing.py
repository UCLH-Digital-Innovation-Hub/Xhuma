"""Process NIR discovery references and combine per-community retrieval results.

These helpers describe the small workflow between SOAP calls. IHE message
construction and parsing remain in app.soap so they can be used independently.
"""

from app.soap.nir_models import XDS_FAILURE_STATUS, XDS_SUCCESS_STATUS, NIRDocumentReference, NIRRetrieveResponse

# A combined result can contain useful documents and errors from another home.
# Give that result an explicit partial status so callers can inspect both.
XDS_PARTIAL_SUCCESS_STATUS = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:PartialSuccess"


def group_documents(documents: list[NIRDocumentReference]) -> list[list[NIRDocumentReference]]:
    """Group discovery references by home community for NIR gateway routing.

    Sandbox testing needed separate requests for different homes. Preserve the
    discovery order within each group, and the order in which homes first appear.
    """

    # Route using HomeCommunityId, rather than the document's unique ID. The
    # same document ID can appear in more than one home, so retain every full
    # reference and its repository instead of deduplicating by ID alone.
    groups: dict[str, list[NIRDocumentReference]] = {}
    for document in documents:
        groups.setdefault(document.home_community_id, []).append(document)
    return list(groups.values())


def combine_retrievals(responses: list[NIRRetrieveResponse]) -> NIRRetrieveResponse:
    """Combine parsed gateway responses while retaining content and errors.

    Each document keeps its original community/repository identifiers and bytes.
    A failed gateway does not discard documents successfully returned elsewhere.
    """

    # Keep the returned document models intact. Concatenating the lists avoids
    # decoding binary content and preserves documents with the same unique ID
    # in different communities. Collect errors alongside those useful results.
    documents = [document for response in responses for document in response.documents]
    errors = [error for response in responses for error in response.errors]

    # The response.success property checks both IHE status and parsed errors.
    # Full success requires every group to succeed; otherwise distinguish a
    # partial result with available documents from failure without any content.
    if all(response.success for response in responses):
        status = XDS_SUCCESS_STATUS
    else:
        status = XDS_PARTIAL_SUCCESS_STATUS if documents else XDS_FAILURE_STATUS
    return NIRRetrieveResponse(status=status, documents=documents, errors=errors)
