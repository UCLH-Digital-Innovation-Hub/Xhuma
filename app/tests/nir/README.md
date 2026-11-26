# NIR questionnaire evidence and consolidation review (16 November 2026)

The current implementation is an outbound consumer. Not publishing images from
our PACS does **not** exclude consumer image retrieval (Q12/Q13) if retrieving
images is in the agreed product scope. Inbound NIR responder requirements
(Q24/Q26/Q28) appear outside this implementation; confirm their applicability
with onboarding rather than recording them as passed. Existing GP Connect
responses to another consumer do not demonstrate receiving requests from NIR.

## Scenario evidence

Run the opt-in scenario suite, patterned after the SCAL tests:

```bash
NIR_RUN_CONFORMANCE=1 uv run --locked pytest app/tests/nir/test_nir_conformance.py -v -rs
```

The suite pins the public sandbox and uses only questionnaire test patients and
identifiers. It does not read environment credentials or endpoint overrides.
Evidence is written to `nir_evidence/<UTC-run-id>/<question>/` (gitignored).
`NIR_EVIDENCE_DIR` can select another output directory. Each exchange contains
numbered `.request.xml` / `.response.xml` files for XML and `.http.json`
metadata containing Content-Type, URL and HTTP status. Multipart exchanges retain
exact `.mime` wire bytes and expose decoded XML, PDF and DICOM parts separately;
`.parts.json` maps filenames to Content-IDs. Other content remains `.bin`. XML
bytes are preserved without reformatting. `result.json` records assertion success
or failure. Failed exchanges are retained, including transport errors, but are
not successful conformance evidence.
Zip each question directory for the questionnaire's single-file upload after
reviewing it. The timestamps distinguish runs and existing evidence is never
overwritten.

| Questions | Automated evidence / remaining requirement |
| --- | --- |
| Q3/Q4, Q5/Q6, Q7 | ITI-38 wire requests/responses, exact 1/2/2 counts and published retrieval addresses; Q7 also checks multiple communities. |
| Q8/Q9 | Successful ITI-38 with exactly zero documents for 9646337317. |
| Q10/Q11 | ITI-38 discovery followed by ITI-39; all discovered addresses and nonempty payloads required. Binary payloads remain in the raw responses. |
| Q12 (both entries) | Retrieves the specified KOS via ITI-39, extracts its study/series/SOP and repository/community IDs, then retrieves one referenced MR image via RAD-75. Checks returned identifiers, MR SOP class, file metadata and decodable pixels. Saves manifest, image and both wire exchanges. Display remains outstanding. |
| Q13 (both entries) | Published invalid RAD-75 identifiers; require parsed errors and no returned objects. User-facing error display still needs receiving-system evidence. |
| Q14 | Explicitly skipped: partial-result parsing exists; live partial-query and UI evidence remain outstanding. |
| Q15/Q16 | Deprecated availability status; require parsed registry failure and no documents. |
| Q17/Q18 | Invalid repository 1.1.4567332.1.3; require parsed retrieval failure and no documents. |
| Q19/Q20 | POST to `/does-not-exist`; require HTTPStatusError carrying 404. This is client handling evidence, not a new inbound Xhuma HTTP response. |
| Q21/Q22 | ITI-38 envelope sent to ITI-39; require an actual SOAP Fault, including when HTTP status is non-2xx. This captures the NIR fault, not proof Xhuma generated an inbound fault. |
| Q23 | Synthetic incoming ITI-38 → verified SAML context → newly issued, locally signed assertion → outbound sandbox ITI-38, ITI-39 and RAD-75 using the same signed assertion. Test certificates only; NIR trust, INT acceptance and production inbound integration remain outstanding. |
| Q25 | Complete ITI-38/ITI-39/RAD-75 envelopes are included in Q23 evidence, with signed SAML inside WS-Security. No separate body/timestamp signature; certificate trust and INT acceptance remain pending. |
| Q24 | Out of scope: Xhuma is not a NIR responding gateway. |
| Q26/Q28 | Explicitly skipped pending agreement on NIR responder scope. |
| Q27 | Explicitly skipped: configured client certificate support is not proof of successful INT mTLS authentication. |

Paired question IDs share the same raw exchange. For questions asking for a
response **from Xhuma**, assess whether the outbound client result is accepted;
if a responding gateway is required, the raw NIR response is not a substitute.
No test here establishes user-facing display or clinical use. Q12 checks DICOM identity
and pixel decoding; it is not a complete DICOM IOD conformance validator.
The questionnaire repeats Q12 and Q13; both meanings are retained above.

Q7 deliberately requires the supplied questionnaire's two results. The older
sandbox smoke check allows extras and previously observed four. Do not silently
relax the conformance assertion: retain the actual response and resolve the
questionnaire/sandbox discrepancy with NHS onboarding.

The [current NHS security requirements](https://digital.nhs.uk/developer/api-catalogue/national-imaging-registry-api/xca-saml-requirements-guide/xca-saml-and-ws-security-requirements)
require an issuer-signed assertion, and describe the separate message signature
as optional (with exact signing requirements when supplied). Q25 explicitly asks
for signed-request evidence. Confirm the applicable questionnaire version before
changing the existing stricter client guard; this work does not claim security
conformance or implement signing.

## Overlapping functions: recommended boundaries

Reviewed after merging `origin/dev` into `nir` at `0771f26`. No production
refactoring is bundled with this evidence work.

| Functions / location | Recommendation |
| --- | --- |
| `nir_queries.build_iti38_request` NHS checksum and `ccda.helpers.validateNHSnumber` | Strongest shared-helper candidate. Extract a dependency-light NHS identifier validator used by both. Preserve NIR's strict ASCII/string validation and useful exceptions; retain the legacy bool/int interface as a wrapper. Importing all CCDA helpers into NIR would create unnecessary coupling. Add cross-interface edge cases before switching callers. |
| `NIRClient.retrieve_documents` and `retrieve_imaging_documents` | Extract a private send-and-parse retrieval helper: three call sites repeat `send_soap` plus Content-Type-aware `parse_retrieve_response`. Keep community grouping and supplied signed-envelope handling in their current methods. |
| `NIRQueryResponse.success` and `NIRRetrieveResponse.success` | Identical status/error predicate; a small shared result base is possible, but low benefit until more response types exist. Keep typed document lists distinct. |
| `nir_queries._envelope` and `soap.responses.helpers.create_header/create_envelope` | Share namespace/action constants first. Outgoing request addressing and correlated incoming responses have different responsibilities and serializers; do not replace them with one generic envelope function yet. |
| `nir_queries.wrap_mtom_request` and `soap.responses.iti_39.deliver_iti39_response` | Potential shared binary-safe MIME encoder once response attachment support is designed. Keep auditing, callbacks and ReplyTo validation outside encoding. Signed request bytes must remain unchanged. |
| `nir_responses` and inbound SOAP parsing | Keep strict outbound metadata/attachment parsing distinct from inbound authentication, validation and fault generation. Similar XML does not imply interchangeable trust boundaries. |
| SCAL `capture_test_logs` and NIR `EvidenceTransport` | Share directory/manifest conventions if useful; logger output alone cannot preserve SOAP/MTOM bodies. Capture at the HTTP transport boundary for NIR. |

Already appropriately shared: ITI-39 and RAD-75 document-request elements,
retrieval response parser and binary document model; per-community retrieval
combination is isolated in `app/nir/processing.py`.

## Evidence from this run

On 16 November 2026 the latest live executions passed 9 of the 10 executable
scenarios. Q7 returned four documents instead of the questionnaire's two, so its
assertion remains failed. Q10/Q11 initially timed out and passed on a separate
retry; both runs remain available. Q15/Q16 returned HTTP 400 containing an
`XDSRegistryError`: the client raises `HTTPStatusError`, and the scenario records
that exception before parsing its retained response body. This does not claim
that the high-level client automatically returns a parsed registry failure on
non-2xx responses.

Per-question ZIPs and a source-run manifest are in the local gitignored
`nir_evidence/submission-review/` directory. They contain the latest execution
for each question, including the failed Q7 evidence, and require review before
submission. Skipped requirements are listed above, not represented as passes.

Validation: 76 local NIR tests passed; 19 live/pending cases skipped in the
normal offline run. Ruff and whitespace checks passed. A broader existing SOAP
regression invocation stalled after eight tests and was interrupted; it must
not be treated as a completed regression pass.

The prepared `nir_evidence/submission-review/` set contains readable XML and
per-question ZIPs with no transport failures. Failed DNS/timeout attempts have
been moved to `nir_evidence/diagnostics/`; original completed wire captures
remain in timestamped directories. Q7 still fails its expected count and is
retained for review. Expected HTTP/registry/SOAP errors are valid negative-test
evidence and are not removed. Future runs still record network failures honestly;
review `result.json` before selecting evidence for submission.

## Q12 retrieval evidence

Run just Q12 from the repository root:

```bash
NIR_RUN_CONFORMANCE=1 uv run --locked pytest app/tests/nir/test_nir_conformance.py -k Q12 -v
```

The live Q12 test passed on 16 November 2026. It saves `manifest.dcm`,
`image.dcm`, the ITI-39/RAD-75 XML and MIME exchanges, and a `result.json`
containing the KOS-derived request and decoded pixel dimensions. `passed` covers
only the automated retrieval and validation scope; `display_verified` and
`full_questionnaire_requirement_met` remain false until display is demonstrated.
Both questionnaire Q12 entries use this evidence. The prepared submission-review
folder and ZIP now include Q12 with this limitation recorded.

`imaging.py` is a sandbox test helper, not a production decoder. It reads the
[Current Requested Procedure Evidence Sequence](https://dicom.nema.org/Medical/Dicom/current/output/chtml/part03/sect_C.17.2.3.html)
and requires the explicit XDS routing fields present in this sandbox KOS. It
never substitutes the manifest repository for the image repository. Pixel
validation uses [pydicom's pixel decoding](https://pydicom.github.io/pydicom/stable/reference/generated/pydicom.pixels.pixel_array.html).
`pydicom` and NumPy are development dependencies; the application client is unchanged.

## Q23 synthetic incoming-to-outgoing assertion evidence

```bash
NIR_RUN_CONFORMANCE=1 uv run --locked pytest app/tests/nir/test_nir_conformance.py -k Q23 -v
```

This scenario creates a synthetic incoming ITI-38, signed by an ephemeral
upstream test issuer. The harness verifies that assertion against a separately
supplied trusted test certificate, checks its issuer, audience, lifetime and
required claims, and extracts context from the verified assertion. It creates
a fresh assertion with Xhuma's test issuer, a new ID and validity window, the NIR
sandbox audience, and the original user/authentication/role/purpose/community
context. The outgoing assertion is signed with a separate Xhuma test key using
RSA-SHA256, SHA256 and exclusive canonicalization. Both signatures are verified
locally. The patient ID is read from the incoming query and used to build the
outgoing query. The test uses patient 9932549118, selects the published KOS
from discovery, retrieves it with ITI-39, and derives the RAD-75 request from
its contents. The same signed assertion is reused unchanged for each request,
with signature and validity checks before transmission. Each envelope has a
new MessageID and transaction-specific addressing. An expired assertion fails
the harness; production renewal must obtain fresh authorized context.
NIRClient sends all three requests to the public sandbox; returned MR image
identity and pixel decoding are checked. Display remains unverified.

Files include `incoming-iti38.xml`, `outgoing-iti38.xml`, `outgoing-iti39.xml`,
`outgoing-rad75.xml`, the retrieved manifest and image, and the signed
`outgoing-assertion.xml`, public test certificates, captured HTTP request/response,
`result.json` and a combined `Q23-evidence.txt` suitable for reviewing as one text
file. Private keys exist only in memory and are not included in evidence.

This is **development evidence**, not full Q23 conformance. The incoming message
is generated and passed to a harness, not received by the production FastAPI
route. The harness assumes authenticated upstream transport and patient access
authorization; its incoming assertion signature does not cover the SOAP body.
It is not a production replay/authorization or general-purpose SAML validator.
The self-signed test certificate is not claimed to be trusted by NIR. Sandbox
success cannot demonstrate NIR signature enforcement or INT acceptance.
`full_questionnaire_requirement_met` and `nir_certificate_trust_verified` remain
false until the actual certificate, integration and INT checks are completed.
Q24 remains out of scope. The combined text file supports both Q23 and Q25
review, showing complete envelopes with signed assertions. It does not
demonstrate the optional separate message signature covering
Body/Timestamp/Assertion. This limit is explicit in the evidence.

The actual certificate can replace the ephemeral signing credential in a future
production signer without changing the intended claim mapping. The user context
must still originate from the agreed trusted upstream interface. Do not reuse
these synthetic issuer/home-community values for deployment.
