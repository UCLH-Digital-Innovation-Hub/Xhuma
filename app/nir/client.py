"""Call NIR's IHE endpoints and return metadata or the retrieved document bytes.

Request construction and response parsing live under app.soap. This module owns
the NIR connection and the small amount of processing needed between calls.
"""

from typing import Literal
from urllib.parse import quote

import httpx

from app.soap.nir_models import (
    NIRDocumentReference,
    NIRImagingStudyRequest,
    NIRQueryResponse,
    NIRRetrieveResponse,
)
from app.soap.nir_queries import (
    ITI38_ACTION,
    ITI39_ACTION,
    RAD75_ACTION,
    build_iti38_request,
    build_iti39_request,
    build_rad75_request,
    wrap_mtom_request,
)
from app.soap.nir_responses import parse_iti38_response, parse_retrieve_response

from .config import NIRConfig
from .processing import combine_retrievals, group_documents

NIRTransaction = Literal["iti38", "iti39", "rad75"]
# Keep the published transaction paths and SOAP actions together. These calls
# use IHE SOAP messages; the /health endpoint is the separate JSON health check.
TRANSACTION_PATHS = {"iti38": "/xca/iti-38", "iti39": "/xca/iti-39", "rad75": "/xcai/rad-75"}
TRANSACTION_ACTIONS = {"iti38": ITI38_ACTION, "iti39": ITI39_ACTION, "rad75": RAD75_ACTION}


class NIRClient:
    """Reuse one NIR HTTP client within an async context manager.

    This client has its own environment, TLS context and connection lifetime.
    It does not use GP Connect's credentials, HTTP client or relay configuration.
    """

    def __init__(self, config: NIRConfig | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
        # Read environment variables at client creation unless explicit settings
        # were supplied. Injecting a transport lets tests exercise these same
        # request paths with MockTransport, without reaching a live NIR service.
        self.config = config or NIRConfig.from_env()

        # Keep connections available across discovery and retrieval calls, then
        # close them when the caller exits the context. Redirects are disabled
        # so a transaction stays on the configured endpoint.
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url.rstrip("/"),
            verify=self.config.ssl_context(),
            timeout=self.config.timeout,
            transport=transport,
            follow_redirects=False,
        )

    async def __aenter__(self) -> "NIRClient":
        """Start the shared client context used by this sequence of NIR calls."""

        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args):
        """Release HTTP resources even when discovery or retrieval raises."""

        await self._client.__aexit__(*args)

    async def aclose(self):
        """Close the client explicitly when it is used without an async context."""

        await self._client.aclose()

    def endpoint(self, transaction: NIRTransaction) -> str:
        """Return the absolute URL used in the SOAP WS-Addressing To header."""

        return self.config.base_url.rstrip("/") + self._transaction_path(transaction)

    @staticmethod
    def _transaction_path(transaction: NIRTransaction) -> str:
        """Resolve only the three NIR transactions supported by this MVP."""

        if transaction not in TRANSACTION_PATHS:
            raise ValueError("NIR transaction must be iti38, iti39 or rad75")
        return TRANSACTION_PATHS[transaction]

    async def health(self) -> dict:
        """Read NIR's JSON health response without sending a patient query."""

        response = await self._client.get("/health")
        response.raise_for_status()
        return response.json()

    async def get_wsdl(self, transaction: NIRTransaction) -> str:
        """Fetch the service contract for ITI-38, ITI-39 or RAD-75 as XML text."""

        # NIR publishes service.wsdl beneath each transaction's own endpoint.
        # Returning the text lets callers inspect the contract without adding
        # a WSDL client or generating code as part of this MVP.
        response = await self._client.get(self._transaction_path(transaction) + "/service.wsdl")
        response.raise_for_status()
        return response.text

    async def get_schema(self, transaction: NIRTransaction, schema_file: str) -> str:
        """Fetch an XSD relative to a transaction's schema directory."""

        # WSDL/XSD imports include nested paths such as ebRS/rs.xsd. Keep those
        # directory separators, but reject traversal, URL suffixes and encoded
        # paths so the requested file stays beneath this transaction's schema.
        parts = schema_file.split("/")
        if any(not part or part in (".", "..") for part in parts) or any(char in schema_file for char in "\\?#%:"):
            raise ValueError("schema_file must be a relative schema path")
        path = self._transaction_path(transaction) + "/schema/" + quote(schema_file, safe="/")
        response = await self._client.get(path)
        response.raise_for_status()
        return response.text

    async def send_soap(self, transaction: NIRTransaction, envelope: bytes | str) -> httpx.Response:
        """Send a prepared SOAP envelope (including signed WS-Security for INT/live).

        Callers supply any required signatures; this method does not create or
        validate them. Retrieval envelopes are wrapped in MTOM automatically.
        HTTP errors raise httpx.HTTPStatusError with the raw response available.
        """
        path = self._transaction_path(transaction)

        # Encode textual input once, and leave supplied bytes intact. Passing
        # bytes preserves the exact prepared envelope; rebuilding signed XML
        # here could invalidate its signature or remove security headers.
        if isinstance(envelope, str):
            envelope = envelope.encode("utf-8")

        # ITI-38 sends plain SOAP 1.2. ITI-39 and RAD-75 use an MTOM MIME root
        # even when the outgoing request contains no binary attachments. The
        # wrapper contains the original envelope without changing its XML.
        if transaction == "iti38":
            content = envelope
            content_type = f'application/soap+xml; charset=utf-8; action="{ITI38_ACTION}"'
        else:
            content, content_type = wrap_mtom_request(envelope, TRANSACTION_ACTIONS[transaction])
        response = await self._client.post(path, content=content, headers={"Content-Type": content_type})

        # HTTP success only confirms delivery. An HTTP 200 response can still
        # contain a SOAP fault or IHE registry error; the high-level methods
        # parse those into result.errors and result.success for callers to check.
        response.raise_for_status()
        return response

    def _require_prepared_envelope(self, soap_envelope: bytes | str | None):
        """Reserve unsigned generated requests for the public NIR sandbox."""

        # INT/production need signed WS-Security in addition to mTLS. This MVP
        # does not build those signatures, so require the caller's prepared
        # envelope before generating or sending any high-level transaction.
        if self.config.environment != "sandbox" and soap_envelope is None:
            raise ValueError("NIR INT and production require a caller-supplied signed WS-Security SOAP envelope")

    async def query_documents(self, nhs_number: str, *, soap_envelope: bytes | str | None = None) -> NIRQueryResponse:
        """ITI-38: discover metadata and identifiers for a patient's documents.

        The returned documents are references, not report/image content. Pass
        those references to retrieve_documents() after checking result.success.
        """
        self._require_prepared_envelope(soap_envelope)

        # Sandbox callers only need an NHS number. For INT/production, keep the
        # caller's prepared envelope instead of rebuilding its patient query.
        envelope = (
            soap_envelope if soap_envelope is not None else build_iti38_request(nhs_number, self.endpoint("iti38"))
        )
        response = await self.send_soap("iti38", envelope)
        # Metadata parsing collects the community, repository and unique ID
        # needed to route the next ITI-39 retrieval request.
        return parse_iti38_response(response.content)

    async def retrieve_documents(
        self, documents: list[NIRDocumentReference], *, soap_envelope: bytes | str | None = None
    ) -> NIRRetrieveResponse:
        """ITI-39: retrieve reports or DICOM manifests using identifiers from ITI-38.

        Generated requests are grouped by community for NIR's sandbox routing.
        Returned content remains bytes, including any DICOM manifest; callers
        decide how to inspect it. Check result.success and result.errors before
        treating a partial set of retrieved documents as complete.
        """
        self._require_prepared_envelope(soap_envelope)
        if soap_envelope is not None:
            # A prepared envelope may have a message signature covering its exact
            # SOAP body. Send it once; splitting it into community groups could
            # invalidate that signature or change the caller's security context.
            response = await self.send_soap("iti39", soap_envelope)
            return parse_retrieve_response(response.content, response.headers.get("Content-Type", ""))
        if not documents:
            raise ValueError("At least one document is required")

        # Sandbox testing found that a mixed-community batch returned routing
        # errors while the same documents succeeded when requested per home.
        # This grouping addresses that observed behaviour, not an IHE rule that
        # all RetrieveDocumentSet requests must use one community.
        responses = []
        for group in group_documents(documents):
            response = await self.send_soap("iti39", build_iti39_request(group, self.endpoint("iti39")))
            responses.append(parse_retrieve_response(response.content, response.headers.get("Content-Type", "")))

        # Retain successful bytes and registry errors from every parsed group.
        # HTTP failures still raise from send_soap; they are not converted into
        # an apparently successful registry response here.
        return combine_retrievals(responses)

    async def retrieve_imaging_documents(
        self,
        studies: list[NIRImagingStudyRequest],
        *,
        transfer_syntax_uids: list[str] | None = None,
        soap_envelope: bytes | str | None = None,
    ) -> NIRRetrieveResponse:
        """RAD-75: retrieve image bytes using study/series/SOP IDs from a manifest.

        Callers supply the manifest's identifiers and desired transfer syntaxes.
        This MVP does not decode DICOM manifests or image pixels automatically.
        """
        self._require_prepared_envelope(soap_envelope)

        # A manifest obtained via ITI-39 supplies the nested study, series and
        # document references used by RAD-75. The SOAP builder serializes those
        # identifiers; any supplied signed envelope bypasses that construction.
        envelope = (
            soap_envelope
            if soap_envelope is not None
            else build_rad75_request(studies, self.endpoint("rad75"), transfer_syntax_uids)
        )
        response = await self.send_soap("rad75", envelope)
        return parse_retrieve_response(response.content, response.headers.get("Content-Type", ""))
