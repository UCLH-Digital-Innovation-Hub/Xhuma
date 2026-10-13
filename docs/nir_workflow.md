# National Imaging Registry workflow

## Overview

The National Imaging Registry (NIR) consumer discovers a patient's imaging
documents and retrieves reports, DICOM manifests or individual images. NIR
queries participating imaging networks and returns their document references;
Xhuma uses those references for the next request.

The MVP provides an async Python client in `app/nir`. Its IHE models, SOAP
builders and response parsers live in `app/soap/nir_*`. It can run independently
of the existing GP Connect routes, PDS lookups, Redis and relay. There is no new
inbound FastAPI route or automatic C-CDA conversion in this workflow.

## Basic flow

```mermaid
flowchart TD
    A[Patient NHS number] --> B[ITI-38 document discovery]
    B --> C[Document references and MIME types]
    C --> D[ITI-39 report or manifest retrieval]
    D --> E[Report or DICOM manifest bytes]
    E --> F{DICOM manifest?}
    F -->|No| G[Caller uses the report bytes]
    F -->|Yes| H[Caller reads the imaging identifiers from the manifest]
    H --> I[RAD-75 image retrieval]
    I --> J[DICOM image bytes]
```

ITI-38 finds documents but does not return their content. ITI-39 retrieves
selected documents, which may include a report or a DICOM manifest. RAD-75
retrieves individual images using the study, series and image identifiers from
the manifest. Manifest decoding is handled by the caller in this MVP.

## Document discovery with ITI-38

`NIRClient.query_documents(nhs_number)` builds a SOAP request and sends it to
`/xca/iti-38`. The sandbox builder checks that the NHS number contains ten
digits and has a valid check digit. This validates the identifier's format;
it does not perform a PDS demographics lookup.

The stored query is `FindDocuments`. It requests approved entries and uses
`LeafClass` so the response includes document metadata. The patient identifier
is sent in IHE CX format, including the NHS number assigning authority:

```text
'9694116422^^^&2.16.840.1.113883.2.1.4.1&ISO'
```

The XML serializer escapes the ampersands. Discovery leaves the query's target
home community unset so NIR can query the participating networks.

Each result becomes a `NIRDocumentReference` with the following fields:

| Field | Purpose |
| --- | --- |
| `home_community_id` | Identifies the imaging community holding the document. |
| `repository_unique_id` | Identifies the repository within that community. |
| `document_unique_id` | Identifies the document to retrieve. |
| `mime_type` | Describes the content, such as `application/pdf` or `application/dicom`. |

The three identifiers together form the retrieval address. The registry entry's
UUID is a separate metadata identifier and is not used as the document UID.
A document UID may occur in more than one community, so the client retains the
community and repository identifiers throughout the workflow.

## Document retrieval with ITI-39

`NIRClient.retrieve_documents(query.documents)` uses the discovery references
to request content from `/xca/iti-39`. Generated sandbox requests are grouped
by home community. During sandbox verification, a mixed-community request
returned routing errors; sending each community's documents separately worked.
This grouping reflects the observed NIR behavior.

```mermaid
flowchart TD
    A[ITI-38 document references] --> B[Group by home community]
    B --> C[Build an ITI-39 SOAP request for each group]
    C --> D[Wrap the SOAP envelope in MTOM]
    D --> E[Send to NIR and parse the response]
    E --> F{All community responses successful?}
    F -->|Yes| G[Return combined document bytes]
    F -->|No, some documents returned| H[Return PartialSuccess with documents and errors]
    F -->|No documents returned| I[Return Failure with errors]
```

MTOM carries a SOAP envelope and binary attachments in a MIME multipart
message. The SOAP root identifies each document and refers to its attachment
through an XOP `cid:` reference. The parser matches that reference to the
attachment's `Content-ID` and preserves the decoded payload as `bytes`.
It also accepts inline base64 document content.

Each returned `NIRRetrievedDocument` contains the retrieval identifiers,
MIME type and binary `content`. A PDF and a DICOM object remain binary; callers
choose how to store or interpret them. Missing attachments, unexpected XML,
invalid base64 and malformed MIME messages are rejected or reported as errors.

If a community returns a registry failure, the combined result retains
documents returned by other communities and reports partial success. HTTP
or transport exceptions raise immediately rather than returning that combined
result. Supplied signed envelopes are sent as supplied, without rebuilding or
splitting their signed content.

## Image retrieval with RAD-75

`NIRClient.retrieve_imaging_documents(studies)` sends an MTOM request to
`/xcai/rad-75`. `NIRImagingStudyRequest` groups series by their study instance
UID, and `NIRImagingSeriesRequest` groups document references by their series
instance UID. The document UID identifies the requested image instance.

The request also lists acceptable DICOM transfer syntaxes. The default is
`1.2.840.10008.1.2.1`; callers can supply `transfer_syntax_uids` when they need
another supported syntax. The response uses the same binary document model and
parser as ITI-39.

## Environment configuration

`NIRConfig.from_env()` reads only `NIR_*` settings. For example, GP Connect can
use `ENV=int` while NIR uses `NIR_ENV=sandbox`. NIR does not borrow GP Connect
credentials, certificates or relay settings.

| Setting | Purpose |
| --- | --- |
| `NIR_ENV` | Selects `sandbox`, `int` or `production`; `dev` selects sandbox. |
| `NIR_BASE_URL` | Optionally overrides the URL selected by the NIR environment. |
| `NIR_TIMEOUT` | Sets the HTTP timeout in seconds; defaults to 30. |
| `NIR_TLS_CERT_PATH` and `NIR_TLS_KEY_PATH` | Configure NIR client authentication with mTLS. |
| `NIR_CA_BUNDLE_PATH` | Optionally supplies the server trust bundle; otherwise system CAs are used. |

The open sandbox accepts the generated unsigned requests. INT and production
require dedicated NIR mTLS credentials and a caller-prepared signed WS-Security
envelope through `soap_envelope=...`. The client does not generate or validate
XUA signatures. INT access also requires the NIR onboarding arrangements,
including certificate trust and IP allow listing.

Compose loads the settings from `.env`. Local Python commands use exported
environment variables; the NIR client does not load `.env` itself.

## Calling the client

```python
from app.nir import NIRClient


async def retrieve_patient_documents(nhs_number: str):
    # Reuse one HTTP client and close its connections when the work finishes.
    async with NIRClient() as client:
        query = await client.query_documents(nhs_number)
        if not query.success:
            raise RuntimeError(query.errors)
        if not query.documents:
            return []

        retrieval = await client.retrieve_documents(query.documents)
        if not retrieval.success:
            # Partial results remain available in retrieval.documents.
            raise RuntimeError(retrieval.errors)
        return retrieval.documents
```

HTTP status and IHE status are checked separately. An HTTP 200 response can
contain a SOAP fault or a registry failure. Callers should inspect `.success`
and `.errors` before treating returned documents as a complete successful
result. An empty successful discovery result is valid; retrieval requires at
least one selected document.

`health()`, `get_wsdl(transaction)` and `get_schema(transaction, schema_file)`
provide the supporting endpoints. The transaction names are `iti38`, `iti39`
and `rad75`. Schema paths may include directories such as `ebRS/rs.xsd`.

## Risks and mitigations

A successful query establishes that NIR processed the request without reported
protocol errors.
It does not establish a complete imaging history or clinical acceptance of the
retrieved content. An empty response and a partial response need to remain
distinct from a confirmed absence of imaging records.

The MVP validates request identifiers, parses protocol failures and preserves
binary content. Patient demographics, access decisions, audit integration and
receiving-system interpretation need to be supplied and validated by the
application using this client.

| Risk/Problem | Handling or validation requirement |
| ------------ | ---------------------------------- |
| Valid NHS number used for the wrong patient | The builder checks format and checksum only. Validate patient identity and demographics before querying, and verify the returned document's patient association before use; this MVP does not perform those checks. |
| Empty discovery mistaken for no imaging history | Treat an empty successful response as no documents returned by this query. Do not infer that the patient has never had imaging or that every imaging network was searched successfully. |
| HTTP 200 mistaken for complete success | Inspect `.success` and `.errors`. SOAP faults, registry errors and warnings make the parsed result unsuccessful even when transport succeeded. HTTP and transport errors raise exceptions. |
| Some communities fail while others return documents | Generated ITI-39 requests are grouped by community. Registry failures retain successful documents with partial status and errors; HTTP or transport failures raise immediately. The caller must distinguish available content from a complete retrieval. |
| Registry UUID or duplicate document UID used to retrieve the wrong object | Use the discovery entry's document unique ID with its home community and repository IDs. Preserve the full three-part address; do not deduplicate by document UID alone. |
| Missing or malformed payload accepted as a valid report or image | The parser reports missing attachment references and rejects malformed MIME, invalid base64 and unexpected document XML. The sandbox checker also requires every discovered reference and a nonempty payload. Normal client results do not independently verify expected document counts, content hashes or DICOM validity. |
| DICOM manifest mistaken for the requested images | ITI-39 may return a manifest. The caller must decode it and use its study, series and image instance identifiers for RAD-75; automatic manifest decoding is outside this MVP. |
| Sandbox success mistaken for INT authentication readiness | NIR settings and certificates are separate from GP Connect. INT requires its own mTLS setup, onboarding and prepared signed WS-Security envelopes. High-level INT calls require a supplied envelope, but the client does not validate the caller's signature or security claims. |
| Existing GP Connect access controls or audit assumed to apply to NIR | The NIR consumer is independent of those routes and does not call their access or audit functions. Integrate authenticated user context, patient access checks and audit recording before clinical use. |
| Published test data differs from the current sandbox | Require the full published reference addresses and report additional documents separately. Record discrepancies rather than replacing returned identifiers with expected values; sandbox documents can change. |
| Receiving system cannot interpret a report, image or transfer syntax | Preserve the supplied MIME type and binary content. Verify document identity, supported DICOM transfer syntaxes, display and interpretation in the receiving system; successful retrieval alone does not demonstrate that support. |
| NIR regressions missed by deployment checks | NIR tests are deliberately excluded from default pytest collection while the MVP develops. Run `pytest app/tests/nir` explicitly and the live sandbox checker when changing the integration; reintroduce deployment test coverage when the integration stabilizes. |

## Validation

The separate NIR unit tests cover environment isolation, request construction,
endpoint routing, full retrieval identifiers, community grouping, protocol
errors and preservation of binary payloads. They also check that missing or
malformed content is not reported as a complete success.

The sandbox checker verifies the published patient references and retrieves
nonempty payloads when `--retrieve` is supplied. The recorded sandbox exercise
also covered the RAD-75 sample and the health, WSDL and schema endpoint families.
These checks establish the exercised sandbox behavior. INT authentication,
patient association, audit recording and receiving-system import and display
still need their own integration validation.

## Sandbox patient checks

```bash
NIR_ENV=sandbox uv run --locked python -m app.nir.sandbox --health --retrieve
```

The checker queries all three published patients, compares their full retrieval
addresses and optionally retrieves every discovered document. It requires all
published references and nonempty retrieved payloads, reports extra documents,
and returns a nonzero exit status if a check fails. Output includes metadata and
byte counts; document contents are not printed or saved.

| NHS number | Published documents | Retrieved in the sandbox on 13 October 2026 |
| --- | --- | --- |
| `9694116422` | One PDF. | One document. |
| `9898841141` | One DICOM manifest and one PDF. | Two documents. |
| `9932549118` | One text document and one DICOM document in separate communities. | Four documents, including two extras. |

The additional documents do not fail the check as long as the published
references are present. Comparing the full retrieval address also prevents a
document with the same UID in another community from satisfying an expectation.

## Code responsibilities

| File | Responsibility |
| --- | --- |
| `app/nir/config.py` | Read and validate independent NIR environment and TLS settings. |
| `app/nir/client.py` | Call the discovery, retrieval and supporting endpoints. |
| `app/nir/processing.py` | Group document references and combine retrieval results. |
| `app/nir/sandbox.py` | Run the published patient checks and summarize results. |
| `app/soap/nir_models.py` | Represent document references, imaging requests and parsed responses. |
| `app/soap/nir_queries.py` | Build SOAP envelopes and MTOM request wrappers. |
| `app/soap/nir_responses.py` | Parse registry metadata, SOAP faults and document payloads. |
| `app/tests/nir/` | Keep the NIR unit tests separate from the default deployment test run. |

## Development tests

The NIR tests live in `app/tests/nir/`. `pytest.ini` excludes this folder from
default collection while the integration is under development, so failures in
these tests do not block the CI and deployment test jobs. The Docker test service
mounts the same configuration to apply the exclusion there too.

Run the NIR tests explicitly when working on the integration:

```bash
uv run --locked pytest app/tests/nir
```

These tests use mocked HTTP responses and synthetic document data. The sandbox
command above exercises the live NHS sandbox separately.

## Future development

The client above is the standalone MVP. The following work is proposed to
complete its integration into Xhuma; these routes, security controls and image
delivery workers are not implemented yet. Development and NHS onboarding can
progress together, but INT testing and production access have separate gates.

### 1. Agree the receiving-system contract

Keep ITI-55 and the existing patient discovery workflow. For an incoming ITI-38,
validate the patient and caller, then run the GP Connect lookup and an outgoing
NIR ITI-38 in parallel. Return the GP document metadata and the NIR document
metadata in one ITI-38 response. Retrieval remains a separate request.

Agree with the requesting organisation how it will retrieve documents belonging
to external home communities through Xhuma. Preserve the original home
community, repository and document unique IDs. If the receiver requires local
identifiers, agree and persist a reversible mapping before implementing it.

Confirm which reports and DICOM objects the receiver can import and display.
For image retrieval, agree Xhuma's IHE actor role and whether the receiving
interface uses RAD-69 or RAD-75. The existing ITI-39 document interface alone
does not provide an imaging viewer; images need a compatible viewer or PACS.

### 2. Connect identity, access checks and NIR security

Apply shared patient and access validation before either downstream request,
including when the GP document is already cached. Validate the identifier's
assigning authority as well as its digits, and carry trusted user, organisation,
patient and request context through discovery, retrieval and any worker job.
An identity or access denial must stop the request, rather than becoming a
partial result from the other source.

Implement the NIR issuer-signed SAML assertion and WS-Security envelope using
the authenticated user's context, with the required audience, role, purpose
of use and originating home community. Rebuild addressing and security for
each SOAP hop. Queued work needs a valid assertion at dispatch, rather than
reusing an expired incoming envelope. Keep NIR credentials, certificates and
environment settings independent of GP Connect.
Follow the [NIR XUA and WS-Security requirements](https://digital.nhs.uk/developer/api-catalogue/national-imaging-registry-api/xca-saml-requirements-guide/xca-saml-and-ws-security-requirements).

### 3. Merge discovery responses without losing metadata

Extend the current single-document GP response and the reduced NIR reference
parser to support complete registry entries. Preserve slots, classifications,
external identifiers, associations and source provenance, including the
retrieval address. Returning document IDs alone is insufficient for the
receiving system to select and retrieve the correct content.

Support the incoming query type, return type, filters and target communities
that the agreed interface allows. Use bounded downstream deadlines and define
how source failures produce registry errors and partial results. Distinguish
an empty successful query from an unavailable source, and retain usable
documents when an availability failure permits partial success. Validate the
merged response against the [ITI-38 transaction requirements](https://profiles.ihe.net/ITI/TF/Volume2/ITI-38.html).

### 4. Route ITI-39 to the correct source

Handle every document request in an incoming ITI-39, rather than only the first.
Route local GP documents to the existing cache and NIR documents to outgoing
ITI-39 requests, grouped by home community where necessary. Persist an
authorised patient association for the full home/repository/document address
so that retrieval and audit do not depend on the document UID alone.

Return PDF, text and DICOM payloads with their correct MIME types and MTOM
attachment references. Build a response addressed and correlated to the
original requester; NIR's response headers relate to Xhuma's outbound request
and cannot simply be forwarded. Check requested and returned identifiers,
attachment completeness and available size or hash metadata before recording
successful retrieval and delivery.

### 5. Decode manifests and build image delivery

Identify DICOM Key Object Selection manifests by SOP Class UID, rather than
assuming every `application/dicom` object is a manifest. Extract the study,
series and referenced SOP instance UIDs, together with the retrieval location
or configured AE-title mapping needed for RAD-75. The image repository may
differ from the repository that supplied the manifest. Check patient association
using identifier issuers: a DICOM Patient ID may be local and differ from the
NHS number.

Use a durable worker queue for large image retrievals. Add streaming support to
the current client, which buffers complete responses, and stage content in
protected temporary files or object storage. Bound study size, batch size,
concurrency and storage use; validate the retrieved instances and transfer
syntaxes, then stream the final MTOM response to the receiving organisation.
Define retries, cancellation, expiry and cleanup for failed or abandoned jobs.

Agree delivery semantics before building the worker interface. A synchronous
IHE request needs its connection held open for the response. An agreed IHE
asynchronous exchange needs the appropriate acknowledgement and a correlated
SOAP callback to a configured endpoint, with delivery retries; a JSON job ID
would require a separately agreed API. Do not assume NIR supports an upstream
callback: a worker can make ordinary request/response calls to NIR. See the
[IHE asynchronous exchange rules](https://profiles.ihe.net/ITI/TF/Volume2/ch-V.html#V.5).

Determine final retrieval status before emitting the response's SOAP root.
Once streaming has begun, a later failure cannot change an already-sent success
status. Record interrupted transfers and distinguish successful retrieval from
successful delivery in the audit trail. Agree what evidence establishes delivery,
including any receiver acknowledgement, and record import/display acceptance
separately from completion of the server's stream.

### 6. Complete NHS onboarding and obtain INT access

Use the [NIR onboarding guidance](https://digital.nhs.uk/services/national-imaging-registry/nir-onboarding-guidance)
to agree Xhuma's supplier role and the deploying organisation's responsibilities:

1. Register with the Developer and Integration Hub and National Service Desk,
   and submit the appropriate use-case application. Confirm the supplier/data
   processor and end-user organisation/data controller roles and the permitted
   direct-care use case.
2. Prepare the supplier's clinical safety evidence and DCB0129 sign-off. The
   deploying organisation needs its own DCB0160 assurance and an approved local
   DPIA. Both parties need an active ODS code and acceptable in-year DSPT status,
   alongside their information governance work. Confirm which agreements apply
   to each party using the
   [NIR governance guidance](https://digital.nhs.uk/services/national-imaging-registry/governance).
3. Complete product-version Digital Onboarding Service accreditation and
   technical conformance, then sign the NHS England Connection Agreement.
   Supplier conformance can progress before a deploying organisation commissions
   the product; the
   [supplier onboarding pack](https://digital.nhs.uk/binaries/content/assets/website-assets/services/national-imaging-registry/onboarding-pack-v4-suppliers.pdf)
   explains the supplier route.
4. Obtain formal commissioning and a Home Community ID from an end-user
   organisation that has completed its governance assurance before requesting
   INT access. DOS approval alone does not grant INT access. Arrange static
   source IP allow listing and public certificate trust, then exercise mTLS and
   signed XUA/WS-Security requests using synthetic patients. These access
   requirements are described in the
   [NIR INT testing guidance](https://digital.nhs.uk/developer/api-catalogue/national-imaging-registry-api/test-data).
5. If the scope expands to publishing imaging into NIR, complete the additional
   provider onboarding, responding endpoint registration and inbound network
   requirements. Acting as a consumer for Xhuma's requesting organisations does
   not itself make Xhuma an imaging provider. Agree that scope with NIR before
   adding provider functionality.

### 7. Prove the complete workflow in INT

Test ITI-55 through combined ITI-38 and subsequent retrieval with the actual
receiving system. Include report display, manifest interpretation, image
import/display, patient association and audit correlation. Sandbox placeholder
content cannot establish that clinical documents will display correctly.

Cover authentication and access failures, empty and partial results, multiple
communities, repeated UIDs, query filters, multiple requested documents and
malformed attachments. For workers, exercise process restarts, retries, slow
receivers, disconnected clients, expired assertions and storage limits. Confirm
that no outcome is recorded as complete when content or delivery is incomplete.

Keep development tests in `app/tests/nir/` and run them explicitly while this
work remains experimental. Once the integration is stable, agree and restore
NIR release-gate coverage so production changes cannot bypass it. Continue the
existing GP Connect regression checks throughout integration.

### 8. Prepare and approve production rollout

Complete the applicable supplier and organisation agreements, local assurance,
operating procedures, user training and support arrangements. Agree monitoring,
retention, incident handling, business continuity and rollback, including a way
to disable NIR independently while retaining the GP Connect service. Obtain the
end-user organisation's go/no-go sign-off and an agreed activation date before
production access and rollout; arrange the NIR hypercare period described in
the [onboarding guidance](https://digital.nhs.uk/services/national-imaging-registry/nir-onboarding-guidance).

Configure the production NIR endpoint, credentials and network access
independently of GP Connect. Verify delivery and audit in a limited rollout,
and monitor errors, partial results, latency, interrupted transfers, queue depth
and temporary storage use before widening access.

This line of work is complete when the agreed discovery and retrieval paths
work in INT with the receiving organisation, patient access and delivery are
auditable, report and image interpretation have been accepted, release checks
cover NIR, and the required NHS and local approvals permit production use.

## References

- [NIR API specification](https://digital.nhs.uk/developer/api-catalogue/national-imaging-registry-api)
- [NIR test data](https://digital.nhs.uk/developer/api-catalogue/national-imaging-registry-api/test-data)
- [dm+d mapping workflow](dm+d_mapping.md)
