"""Opt-in live scenarios. Raw sandbox responses are evidence, mocks are not."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
import pytest

from app.nir import NIRClient
from app.nir.config import NIRConfig
from app.nir.sandbox import TEST_PATIENTS, _identifiers
from app.soap.nir_models import NIRDocumentReference, NIRImagingSeriesRequest, NIRImagingStudyRequest
from app.soap.nir_queries import RIM_NS, build_iti38_request, build_iti39_request, build_rad75_request
from app.soap.nir_responses import parse_iti38_response, parse_retrieve_response

from .evidence import EvidenceTransport
from .imaging import kos_image_request, validate_mr_image

pytestmark = pytest.mark.skipif(
    os.getenv("NIR_RUN_CONFORMANCE") != "1", reason="Set NIR_RUN_CONFORMANCE=1 for live sandbox evidence"
)
RUN_ID = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
SCENARIOS = [
    ("Q03-Q04", "9694116422", 1),
    ("Q05-Q06", "9898841141", 2),
    ("Q07", "9932549118", 2),
    ("Q08-Q09", "9646337317", 0),
    ("Q10-Q11", "9898841141", None),
    ("Q12", None, None),
    ("Q13", None, None),
    ("Q15-Q16", "9694116422", None),
    ("Q17-Q18", None, None),
    ("Q19-Q20", "9694116422", None),
    ("Q21-Q22", "9694116422", None),
    ("Q23", None, None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("question,patient,count", SCENARIOS, ids=[s[0] for s in SCENARIOS])
async def test_nir_conformance(question, patient, count):
    # Pin the public sandbox: never inherit production URLs or certificates.
    directory = Path(os.getenv("NIR_EVIDENCE_DIR", "nir_evidence")) / RUN_ID / question
    transport = EvidenceTransport(directory)
    result = {"questions": question, "source": "live NIR sandbox", "passed": False}
    try:
        async with NIRClient(NIRConfig(), transport=transport) as client:
            if count is not None:
                response = await client.query_documents(patient)
                result["parsed"] = response.model_dump()
                assert response.success, response.errors
                assert len(response.documents) == count, (
                    "Sandbox count differs from questionnaire; review captured evidence"
                )
                assert set(TEST_PATIENTS.get(patient, ())) <= {_identifiers(d) for d in response.documents}
                if question == "Q07":
                    assert len({d.home_community_id for d in response.documents}) >= 2
            elif question == "Q10-Q11":
                query = await client.query_documents(patient)
                assert query.success and query.documents
                response = await client.retrieve_documents(query.documents)
                result["documents"] = [
                    dict(d.model_dump(exclude={"content"}), byte_length=len(d.content)) for d in response.documents
                ]
                assert response.success, response.errors
                assert {_identifiers(d) for d in query.documents} == {_identifiers(d) for d in response.documents}
                assert all(d.content for d in response.documents)
            elif question == "Q12":
                result.update(
                    display_verified=False,
                    full_questionnaire_requirement_met=False,
                    outstanding=["Receiving-system image display"],
                    scope="KOS retrieval, KOS-derived RAD-75 retrieval and DICOM pixel validation",
                )
                manifest_reference = NIRDocumentReference(
                    home_community_id="urn:oid:1.2.34.567.8.1",
                    repository_unique_id="1.1.4567332.1.7",
                    document_unique_id="2.25.90214658647374166344513344800741000001",
                )
                manifest_response = await client.retrieve_documents([manifest_reference])
                assert manifest_response.success, manifest_response.errors
                assert len(manifest_response.documents) == 1
                manifest = manifest_response.documents[0]
                assert _identifiers(manifest) == _identifiers(manifest_reference)
                (directory / "manifest.dcm").write_bytes(manifest.content)
                study = kos_image_request(manifest.content)
                result["kos_derived_request"] = study.model_dump()
                response = await client.retrieve_imaging_documents([study])
                assert response.success, response.errors
                assert len(response.documents) == 1
                image = response.documents[0]
                assert _identifiers(image) == _identifiers(study.series[0].documents[0])
                (directory / "image.dcm").write_bytes(image.content)
                result["image"] = validate_mr_image(image.content, study)
                result["retrieval_verified"] = True
            elif question == "Q23":
                from lxml import etree

                from .saml_evidence import (
                    NS,
                    UPSTREAM_ISSUER,
                    XHUMA_AUDIENCE,
                    XHUMA_ISSUER,
                    reissue_for_nir,
                    reuse_nir_assertion,
                    synthetic_incoming,
                    test_certificate,
                    verify_context,
                )

                now = datetime.now(UTC)
                upstream_key, upstream_cert = test_certificate("Synthetic upstream - NOT FOR PRODUCTION", now)
                signing_key, signing_cert = test_certificate("Xhuma synthetic issuer - NOT NIR TRUSTED", now)
                incoming = synthetic_incoming(upstream_key, upstream_cert, now, patient="9932549118")
                patient, outgoing = reissue_for_nir(
                    incoming,
                    upstream_cert,
                    signing_key,
                    signing_cert,
                    client.endpoint("iti38"),
                    client.config.base_url,
                    now,
                )
                original = verify_context(incoming, upstream_cert, UPSTREAM_ISSUER, XHUMA_AUDIENCE, now)
                verified = verify_context(outgoing, signing_cert, XHUMA_ISSUER, client.config.base_url, now)
                for path in ("saml:Subject/saml:NameID", "saml:AuthnStatement", "saml:AttributeStatement"):
                    # Compare claim values without namespace-prefix/serialization differences.
                    def values(node):
                        return [(item.tag, dict(item.attrib), item.text) for item in node.iter()]

                    assert values(original.find(path, NS)) == values(verified.find(path, NS))
                assert original.get("ID") != verified.get("ID")
                (directory / "incoming-iti38.xml").write_bytes(incoming)
                (directory / "outgoing-iti38.xml").write_bytes(outgoing)
                (directory / "upstream-test-certificate.pem").write_bytes(upstream_cert)
                (directory / "xhuma-test-certificate.pem").write_bytes(signing_cert)
                result.update(
                    incoming_signature_verified=True,
                    outgoing_signature_verified=True,
                    claims_preserved=True,
                    nir_certificate_trust_verified=False,
                    full_questionnaire_requirement_met=False,
                    incoming_source="Synthetic ITI-38 passed to a test harness; not the FastAPI inbound route",
                    scope="Same verified user context and signed assertion across ITI-38, ITI-39 and RAD-75",
                    soap_body_signed=False,
                    display_verified=False,
                    supports_questions=["Q23", "Q25"],
                    outstanding=[
                        "NIR-trusted signing certificate and INT acceptance",
                        "Production inbound route integration",
                    ],
                )
                response = await client.query_documents(patient, soap_envelope=outgoing)
                result["parsed"] = response.model_dump()
                assert response.success and response.documents, response.errors
                manifests = [
                    doc
                    for doc in response.documents
                    if doc.document_unique_id == "2.25.90214658647374166344513344800741000001"
                    and doc.home_community_id == "urn:oid:1.2.34.567.8.1"
                    and doc.repository_unique_id == "1.1.4567332.1.7"
                ]
                assert len(manifests) == 1, "Expected sandbox KOS missing from discovery"
                outgoing39 = reuse_nir_assertion(
                    build_iti39_request(manifests, client.endpoint("iti39")),
                    outgoing,
                    signing_cert,
                    client.config.base_url,
                    datetime.now(UTC),
                )
                (directory / "outgoing-iti39.xml").write_bytes(outgoing39)
                retrieval = await client.retrieve_documents(manifests, soap_envelope=outgoing39)
                assert retrieval.success and len(retrieval.documents) == 1, retrieval.errors
                manifest = retrieval.documents[0]
                assert _identifiers(manifest) == _identifiers(manifests[0])
                (directory / "manifest.dcm").write_bytes(manifest.content)
                study = kos_image_request(manifest.content)
                outgoing75 = reuse_nir_assertion(
                    build_rad75_request([study], client.endpoint("rad75")),
                    outgoing,
                    signing_cert,
                    client.config.base_url,
                    datetime.now(UTC),
                )
                (directory / "outgoing-rad75.xml").write_bytes(outgoing75)
                images = await client.retrieve_imaging_documents([study], soap_envelope=outgoing75)
                assert images.success and len(images.documents) == 1, images.errors
                image = images.documents[0]
                assert _identifiers(image) == _identifiers(study.series[0].documents[0])
                result["image"] = validate_mr_image(image.content, study)
                result["kos_derived_request"] = study.model_dump()
                (directory / "image.dcm").write_bytes(image.content)
                result["completed_transactions"] = ["ITI-38", "ITI-39", "RAD-75"]
                result["assertion_reused"] = True
                # Capture the signed assertion as sent, including its signature (verification strips it).
                from .saml_evidence import parse_xml

                sent_assertion = parse_xml(outgoing).find("s:Header/wsse:Security/saml:Assertion", NS)
                (directory / "outgoing-assertion.xml").write_bytes(etree.tostring(sent_assertion))
                (directory / "Q23-evidence.txt").write_text(
                    "Q23 DEVELOPMENT EVIDENCE - synthetic test identities only\n"
                    "Incoming ITI-38 is generated and processed by a test harness, not the production inbound route.\n"
                    "Incoming and outgoing assertions use separate ephemeral test signing keys.\n"
                    "Both assertion signatures verified locally against pinned test certificates.\n"
                    "Verified user, authentication and attribute context is preserved in a fresh NIR assertion.\n"
                    "The outgoing assertion is signed with RSA-SHA256/SHA256 and exclusive canonicalization.\n"
                    "The same signed assertion was verified as unexpired and reused for ITI-38, ITI-39 and RAD-75.\n"
                    "All three requests were sent by NIRClient to the public NIR sandbox.\n"
                    "Each envelope has fresh addressing/message ID. Assertion signatures do not sign the SOAP bodies.\n"
                    "This pack supports Q23/Q25 review; no separate message signature is demonstrated.\n"
                    "Sandbox success DOES NOT prove NIR certificate trust or security validation.\n"
                    "NIR-trusted certificate, INT acceptance and production route integration remain outstanding.\n"
                    "No private keys are included. Inbound authenticated transport/patient authorization are assumed by this harness.\n\n"
                    "INCOMING ITI-38\n"
                    + incoming.decode()
                    + "\n\nOUTGOING ITI-38\n"
                    + outgoing.decode()
                    + "\n\nOUTGOING ITI-39\n"
                    + outgoing39.decode()
                    + "\n\nOUTGOING RAD-75\n"
                    + outgoing75.decode()
                )
            elif question == "Q13":
                document = NIRDocumentReference(
                    home_community_id="urn:oid:1.2.34.567.8.1",
                    repository_unique_id="1.3.6.1.4.1.21367.13.71.201.1",
                    document_unique_id="1.3.46.670589.11.0.4.1996082307380000",
                )
                response = await client.retrieve_imaging_documents(
                    [
                        NIRImagingStudyRequest(
                            study_instance_uid="1.3.46.670589.11.0.1.1996082307380000",
                            series=[
                                NIRImagingSeriesRequest(
                                    series_instance_uid="1.3.46.670589.11.0.2.1996082307380000", documents=[document]
                                )
                            ],
                        )
                    ]
                )
                assert not response.success and response.errors and not response.documents
                result["errors"] = response.errors
            elif question == "Q15-Q16":
                root = ET.fromstring(build_iti38_request(patient, client.endpoint("iti38")))
                slot = root.find(
                    f".//{{{RIM_NS}}}Slot[@name='$XDSDocumentEntryStatus']/{{{RIM_NS}}}ValueList/{{{RIM_NS}}}Value"
                )
                slot.text = "('urn:oasis:names:tc:ebxml-regrep:StatusType:Deprecated')"
                try:
                    response = await client.query_documents(patient, soap_envelope=ET.tostring(root))
                except httpx.HTTPStatusError as exc:
                    # The sandbox delivers this registry failure with HTTP 400.
                    # Record the client's exception separately from parsed wire evidence.
                    result["client_exception"] = type(exc).__name__
                    result["http_status"] = exc.response.status_code
                    assert exc.response.status_code == 400
                    response = parse_iti38_response(exc.response.content)
                result["parsed"] = response.model_dump()
                assert not response.success and response.errors and not response.documents
                assert any("XDSRegistryError" in error for error in response.errors)
            elif question == "Q17-Q18":
                reference = TEST_PATIENTS["9694116422"][0]
                response = await client.retrieve_documents(
                    [
                        NIRDocumentReference(
                            home_community_id=reference[0],
                            repository_unique_id="1.1.4567332.1.3",
                            document_unique_id=reference[2],
                        )
                    ]
                )
                result["errors"] = response.errors
                assert not response.success and response.errors and not response.documents
            elif question == "Q19-Q20":
                # Override only this test client's transaction path to the specified invalid URL.
                client._transaction_path = lambda transaction: "/does-not-exist"
                with pytest.raises(httpx.HTTPStatusError) as error:
                    await client.query_documents(patient)
                result["http_status"] = error.value.response.status_code
                assert error.value.response.status_code == 404
            elif question == "Q21-Q22":
                envelope = build_iti38_request(patient, client.endpoint("iti39"))
                try:
                    raw = await client.send_soap("iti39", envelope)
                except httpx.HTTPStatusError as exc:
                    raw = exc.response
                response = parse_retrieve_response(raw.content, raw.headers.get("content-type", ""))
                result["errors"] = response.errors
                assert not response.success and response.errors
                # An arbitrary registry error or HTTP failure is not SOAP fault evidence.
                from app.soap.nir_responses import _body, _fault, _unpack_mtom

                content = raw.content
                if raw.headers.get("content-type", "").lower().startswith("multipart/related"):
                    content, _ = _unpack_mtom(content, raw.headers["content-type"])
                assert _fault(_body(content))
            result["passed"] = True
    except Exception as exc:
        result["failure"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (directory / "result.json").write_text(json.dumps(result, indent=2))


@pytest.mark.parametrize(
    "question,reason",
    [
        (
            "Q14",
            "Parser retains partial results; end-user display and a reproducible live partial-response scenario still required",
        ),
        ("Q24", "Out of scope: Xhuma is not a NIR responding gateway"),
        (
            "Q25",
            "Full envelopes with signed assertions captured by Q23; NIR trust/INT acceptance pending; no separate message signature",
        ),
        ("Q26", "Inbound NIR responder scope requires agreement; no inbound NIR signature verification implemented"),
        ("Q27", "mTLS configuration exists but authenticated INT connection evidence is still required"),
        ("Q28", "Inbound NIR responder scope requires agreement; no inbound NIR mTLS endpoint implemented"),
    ],
)
def test_nir_pending_evidence(question, reason):
    pytest.skip(f"{question}: {reason}")
