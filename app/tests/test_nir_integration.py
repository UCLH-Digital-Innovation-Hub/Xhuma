"""Exercise the application routes with signed fixture identities and fake I/O.

These tests run in the default suite: the new opt-in routes are application code.
Only the existing live questionnaire tests require the separate sandbox opt-in.
"""

import asyncio
import base64
import os
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from lxml import etree as ET

from app.audit.audit import AuditFailureException
from app.nir.client import NIRClient
from app.nir.config import NIRDiscoveryConfig
from app.nir.security import NS, RESOURCE, sign_outgoing, verify_incoming
from app.soap import discovery, soap
from app.soap.faults import SoapFault
from app.soap.nir_models import NIRDocumentReference, NIRRetrievedDocument
from app.soap.nir_queries import ITI38_ACTION, ITI39_ACTION, SOAP_NS, build_iti38_request, build_iti39_request
from app.soap.nir_responses import _unpack_mtom, parse_retrieve_response
from app.soap.responses import iti_38, iti_39
from app.tests.fixtures.saml_attributes import saml
from app.tests.nir.saml_evidence import (
    UPSTREAM_ISSUER,
    XHUMA_AUDIENCE,
    XHUMA_ISSUER,
    assertion,
    attach_signed,
    parse_xml,
    synthetic_incoming,
    verify_context,
)
from app.tests.nir.saml_evidence import test_certificate as make_certificate
from app.tests.nir.test_nir_client import QUERY_RESPONSE

PATIENT = "9932549118"
REFERENCE = NIRDocumentReference(
    home_community_id="urn:oid:1.2.3", repository_unique_id="repository-1", document_unique_id="1.2.3.4"
)
BINARY = b"\x00\xff\x80\r\nPDF or DICOM bytes\x00"


def request_for(app, content, content_type="application/soap+xml"):
    async def receive():
        return {"type": "http.request", "body": content, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "app": app, "headers": [(b"content-type", content_type.encode())]}, receive
    )


def retrieval_response(references, content=BINARY):
    documents = "".join(
        f"<x:DocumentResponse><x:HomeCommunityId>{r.home_community_id}</x:HomeCommunityId>"
        f"<x:RepositoryUniqueId>{r.repository_unique_id}</x:RepositoryUniqueId>"
        f"<x:DocumentUniqueId>{r.document_unique_id}</x:DocumentUniqueId>"
        f"<x:mimeType>application/dicom</x:mimeType><x:Document>{base64.b64encode(content).decode()}</x:Document>"
        "</x:DocumentResponse>"
        for r in references
    )
    return (
        f'<s:Envelope xmlns:s="{SOAP_NS}" xmlns:x="{discovery.XDS_NS}" xmlns:r="{discovery.RS_NS}">'
        f'<s:Body><x:RetrieveDocumentSetResponse><r:RegistryResponse status="{discovery.XDS_SUCCESS_STATUS}"/>'
        f"{documents}</x:RetrieveDocumentSetResponse></s:Body></s:Envelope>"
    ).encode()


@pytest.fixture
def integration(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("NIR_"):
            monkeypatch.delenv(name)
    now = datetime.now(UTC)
    upstream_key, upstream_cert = make_certificate("upstream test issuer", now)
    signing_key, signing_cert = make_certificate("Xhuma test issuer", now)
    files = {
        "NIR_INBOUND_CERT_PATH": upstream_cert,
        "NIR_SIGNING_CERT_PATH": signing_cert,
        "NIR_SIGNING_KEY_PATH": signing_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
    }
    for name, data in files.items():
        path = tmp_path / name
        path.write_bytes(data)
        monkeypatch.setenv(name, str(path))
    for name, value in {
        "NIR_DISCOVERY_ENABLED": "true",
        "NIR_INBOUND_ISSUER": UPSTREAM_ISSUER,
        "NIR_ISSUER": XHUMA_ISSUER,
        "NIR_INBOUND_AUDIENCE": XHUMA_AUDIENCE,
        "NIR_BASE_URL": "https://nir.example.test",
        "NIR_TIMEOUT": "2",
    }.items():
        monkeypatch.setenv(name, value)
    config = NIRDiscoveryConfig.from_env()
    app = FastAPI()
    app.state.nir_discovery = config
    incoming = synthetic_incoming(upstream_key, upstream_cert, now, patient=PATIENT)
    values = {PATIENT: b"gp-document"}
    cache = AsyncMock()

    async def set_value(key, value, **options):
        if options.get("nx") and key in values:
            return False
        values[key] = value
        return True

    cache.set.side_effect = set_value
    cache.get.side_effect = values.get
    pipe = MagicMock()
    pipe.__aenter__.return_value = pipe
    pipe.set.side_effect = lambda key, value, **options: values.update({key: value})
    pipe.execute = AsyncMock(return_value=[True])
    cache.pipeline = MagicMock(return_value=pipe)
    cache.hmget.return_value = (base64.b64encode(b"<ClinicalDocument/>"), PATIENT.encode())
    monkeypatch.setattr(discovery, "redis_client", cache)
    monkeypatch.setattr(iti_38, "redis_client", cache)
    audit = AsyncMock()
    monkeypatch.setattr(discovery, "attempt_audit", audit)
    monkeypatch.setattr(iti_38, "attempt_audit", audit)
    monkeypatch.setattr(iti_39, "attempt_audit", audit)
    monkeypatch.setattr("app.gpconnect_stages.patient.audit.attempt_audit", audit)
    patient = {"meta": {"security": [{"code": "U"}]}, "generalPractitioner": [{"identifier": {"value": "GP-ODS"}}]}
    pds = AsyncMock(return_value=patient)
    monkeypatch.setattr("app.gpconnect_stages.patient.lookup_patient", pds)
    wire = []

    async def respond(request):
        wire.append(request)
        if request.url.path.endswith("iti-38"):
            return httpx.Response(200, content=QUERY_RESPONSE)
        return httpx.Response(200, content=retrieval_response([REFERENCE]))

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(discovery, "NIRClient", lambda config: NIRClient(config, transport=transport))
    return SimpleNamespace(
        config=config,
        app=app,
        incoming=incoming,
        now=now,
        upstream_key=upstream_key,
        upstream_cert=upstream_cert,
        signing_cert=signing_cert,
        values=values,
        cache=cache,
        pipe=pipe,
        audit=audit,
        pds=pds,
        patient=patient,
        wire=wire,
        transport=transport,
    )


def signed_retrieval(p, references, patient=PATIENT, reply_to=None):
    root = parse_xml(build_iti39_request(references, XHUMA_AUDIENCE + "/SOAP/iti39"))
    if reply_to:
        root.find("s:Header/a:ReplyTo/a:Address", NS).text = reply_to
    return attach_signed(
        root,
        assertion(UPSTREAM_ISSUER, XHUMA_AUDIENCE, datetime.now(UTC), patient=patient),
        p.upstream_key,
        p.upstream_cert,
    )


def test_disabled_config_does_not_load_keys_or_validate_nir_environment(monkeypatch):
    monkeypatch.setenv("NIR_DISCOVERY_ENABLED", "false")
    monkeypatch.setenv("NIR_ENV", "invalid")
    monkeypatch.setenv("NIR_SIGNING_KEY_PATH", "/does/not/exist")
    assert NIRDiscoveryConfig.from_env() is None


@pytest.mark.parametrize("flag", ["yes", "1", "", "tru"])
def test_invalid_enable_flag_rejected(monkeypatch, flag):
    monkeypatch.setenv("NIR_DISCOVERY_ENABLED", flag)
    with pytest.raises(ValueError, match="NIR_DISCOVERY_ENABLED"):
        NIRDiscoveryConfig.from_env()


@pytest.mark.parametrize("failure", ["missing", "mismatched_key", "expired", "invalid_ttl", "missing_tls"])
def test_enabled_configuration_fails_early(integration, monkeypatch, tmp_path, failure):
    p = integration
    if failure == "missing":
        monkeypatch.delenv("NIR_ISSUER")
    elif failure == "mismatched_key":
        monkeypatch.setenv("NIR_SIGNING_CERT_PATH", os.environ["NIR_INBOUND_CERT_PATH"])
    elif failure == "expired":
        _, cert = make_certificate("expired", p.now - timedelta(days=3))
        path = tmp_path / "expired.pem"
        path.write_bytes(cert)
        monkeypatch.setenv("NIR_INBOUND_CERT_PATH", str(path))
    elif failure == "invalid_ttl":
        monkeypatch.setenv("NIR_RETRIEVAL_TTL", "0")
    else:
        monkeypatch.setenv("NIR_ENV", "int")
    with pytest.raises(ValueError):
        NIRDiscoveryConfig.from_env()


@pytest.mark.asyncio
async def test_disabled_route_keeps_gp_only_path(integration, monkeypatch):
    p = integration
    p.app.state.nir_discovery = None
    monkeypatch.setenv("SAML_TRUSTED_ISSUER", UPSTREAM_ISSUER)
    legacy = AsyncMock(return_value="GP-only XML")
    monkeypatch.setattr(soap, "iti_38_response", legacy)
    result = await soap.iti38(request_for(p.app, p.incoming))
    assert result.body == b"GP-only XML"
    legacy.assert_awaited_once()
    assert p.wire == []
    p.pds.assert_not_awaited()


@pytest.mark.asyncio
async def test_discovery_uses_verified_fixture_claims_and_returns_both_sources(integration):
    p = integration
    result = await soap.iti38(request_for(p.app, p.incoming))
    xml = parse_xml(result.body)
    entries = xml.findall(".//rim:ExtrinsicObject", NS)
    assert len(entries) == 2
    assert {e.get("home") for e in entries} == {f"urn:oid:{discovery.COMMUNITY_ID}", REFERENCE.home_community_id}
    assert xml.findtext("s:Header/a:RelatesTo", namespaces=NS) == parse_xml(p.incoming).findtext(
        "s:Header/a:MessageID", namespaces=NS
    )
    p.pds.assert_awaited_once()  # Includes the cached GP document path.
    assert len(p.wire) == 1
    outgoing = p.wire[0].content
    verified = verify_context(outgoing, p.signing_cert, XHUMA_ISSUER, p.config.client.base_url, datetime.now(UTC))
    original = verify_context(p.incoming, p.upstream_cert, UPSTREAM_ISSUER, XHUMA_AUDIENCE, p.now)
    assert verified.get("ID") != original.get("ID")
    assert verified.findtext("saml:Subject/saml:NameID", namespaces=NS) == saml.subject_id
    assert verified.find("saml:AuthnStatement", NS).get("AuthnInstant") == original.find("saml:AuthnStatement", NS).get(
        "AuthnInstant"
    )
    for tag in ("Role", "PurposeForUse"):
        assert (
            verified.find(f".//{{urn:hl7-org:v3}}{tag}").attrib == original.find(f".//{{urn:hl7-org:v3}}{tag}").attrib
        )
    assert parse_xml(outgoing).findtext("s:Header/a:MessageID", namespaces=NS) != parse_xml(p.incoming).findtext(
        "s:Header/a:MessageID", namespaces=NS
    )
    p.pipe.set.assert_called_once()
    assert p.pipe.set.call_args.kwargs["ex"] == p.config.retrieval_ttl


@pytest.mark.parametrize(
    "failure",
    [
        "tampered",
        "wrong_cert",
        "expired",
        "audience",
        "issuer",
        "duplicate",
        "missing_resource",
        "duplicate_value",
        "wrong_action",
    ],
)
def test_incoming_security_failures_rejected(integration, failure):
    p = integration
    content, config = p.incoming, p.config
    if failure == "wrong_cert":
        config = replace(config, inbound_certificate=config.signing_certificate)
    elif failure == "audience":
        config = replace(config, inbound_audience="https://wrong.test")
    elif failure == "issuer":
        config = replace(config, inbound_issuer="wrong")
    elif failure in ("expired", "missing_resource", "duplicate_value"):
        now = p.now - timedelta(minutes=6) if failure == "expired" else p.now
        claim = assertion(UPSTREAM_ISSUER, XHUMA_AUDIENCE, now, patient=PATIENT)
        attr = claim.find(f"saml:AttributeStatement/saml:Attribute[@Name='{RESOURCE}']", NS)
        if failure == "missing_resource":
            attr.getparent().remove(attr)
        elif failure == "duplicate_value":
            attr.append(deepcopy(attr[0]))
        content = attach_signed(
            parse_xml(build_iti38_request(PATIENT, XHUMA_AUDIENCE)), claim, p.upstream_key, p.upstream_cert
        )
    else:
        root = parse_xml(content)
        if failure == "tampered":
            root.find(".//saml:NameID", NS).text = "forged"
        elif failure == "wrong_action":
            root.find("s:Header/a:Action", NS).text = ITI39_ACTION
        else:
            sec = root.find("s:Header/wsse:Security", NS)
            sec.append(deepcopy(sec[0]))
        content = ET.tostring(root)
    with pytest.raises(SoapFault, match="Invalid NIR caller security context"):
        verify_incoming(content, config, ITI38_ACTION)
    assert not p.wire


def test_model_generated_assertion_preserves_verified_xml_extensions(integration):
    p = integration
    claim = assertion(UPSTREAM_ISSUER, XHUMA_AUDIENCE, p.now, patient=PATIENT)
    name_id = claim.find("saml:Subject/saml:NameID", NS)
    name_id.set("NameQualifier", "Clinic & research")
    auth = claim.find("saml:AuthnStatement", NS)
    auth.set("SessionIndex", "session-123")
    authority = ET.SubElement(auth.find("saml:AuthnContext", NS), f"{{{NS['saml']}}}AuthenticatingAuthority")
    authority.text = "https://identity.example/authority"
    role = claim.find(".//{urn:hl7-org:v3}Role")
    extension = ET.SubElement(
        role, "{urn:example:clinical-extension}Detail", nsmap={"clinical": "urn:example:clinical-extension"}
    )
    extension.text = "Café & imaging <team>"
    incoming = attach_signed(
        parse_xml(build_iti38_request(PATIENT, XHUMA_AUDIENCE)), claim, p.upstream_key, p.upstream_cert
    )
    context = verify_incoming(incoming, p.config, ITI38_ACTION)
    before = ET.tostring(context.assertion)
    outgoing = sign_outgoing(build_iti38_request(PATIENT, p.config.client.base_url), context, p.config)
    verified = verify_context(outgoing, p.signing_cert, XHUMA_ISSUER, p.config.client.base_url, datetime.now(UTC))
    assert ET.tostring(context.assertion) == before  # Reissuance never mutates verified input.
    assert verified.find("saml:Subject/saml:NameID", NS).attrib == name_id.attrib
    assert verified.find("saml:AuthnStatement", NS).attrib == auth.attrib
    assert (
        verified.findtext("saml:AuthnStatement/saml:AuthnContext/saml:AuthenticatingAuthority", namespaces=NS)
        == authority.text
    )
    assert verified.findtext(".//{urn:example:clinical-extension}Detail") == extension.text
    assert [ET.QName(child).localname for child in parse_xml(outgoing).find(".//saml:Assertion", NS)] == [
        "Issuer",
        "Signature",
        "Subject",
        "Conditions",
        "AuthnStatement",
        "AttributeStatement",
    ]


def test_request_models_escape_addresses_and_exclude_document_content():
    document = NIRRetrievedDocument(
        home_community_id="urn:oid:1.2.3",
        repository_unique_id="repo & other",
        document_unique_id="report <two> é",
        mime_type="application/pdf",
        content=BINARY,
    )
    endpoint = "https://nir.example.test/iti39?one=1&two=2"
    wire = build_iti39_request([document, REFERENCE], endpoint)
    root = parse_xml(wire)
    addresses = root.findall(f".//{{{discovery.XDS_NS}}}DocumentRequest")
    assert len(addresses) == 2
    assert root.findtext("s:Header/a:To", namespaces=NS) == endpoint
    assert addresses[0].findtext(f"{{{discovery.XDS_NS}}}RepositoryUniqueId") == document.repository_unique_id
    assert addresses[0].findtext(f"{{{discovery.XDS_NS}}}DocumentUniqueId") == document.document_unique_id
    assert len(addresses[0]) == 3
    assert BINARY not in wire and b"application/pdf" not in wire


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["patient", "filter", "duplicate_query", "duplicate_slot", "return_type"])
async def test_query_mismatch_or_unsupported_filters_never_call_sources(integration, failure):
    p = integration
    root = parse_xml(p.incoming)
    adhoc = root.find("s:Body/query:AdhocQueryRequest/rim:AdhocQuery", NS)
    if failure == "patient":
        adhoc.find(
            "rim:Slot[@name='$XDSDocumentEntryPatientId']/rim:ValueList/rim:Value", NS
        ).text = "'9694116422^^^&2.16.840.1.113883.2.1.4.1&ISO'"
    elif failure == "filter":
        ET.SubElement(adhoc, f"{{{discovery.RIM_NS}}}Slot", name="$XDSDocumentEntryClassCode")
    elif failure == "duplicate_query":
        adhoc.getparent().append(deepcopy(adhoc))
    elif failure == "duplicate_slot":
        adhoc.append(deepcopy(adhoc[0]))
    else:
        root.find("s:Body/query:AdhocQueryRequest/query:ResponseOption", NS).set("returnType", "ObjectRef")
    with pytest.raises(SoapFault):
        await soap.iti38(request_for(p.app, ET.tostring(root)))
    p.pds.assert_not_awaited()
    assert not p.wire


@pytest.mark.asyncio
async def test_restricted_patient_blocks_both_sources_even_on_cache_hit(integration):
    p = integration
    p.patient["meta"]["security"][0]["code"] = "R"
    with pytest.raises(SoapFault, match="Patient access"):
        await soap.iti38(request_for(p.app, p.incoming))
    assert not p.wire
    p.cache.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_replayed_message_is_rejected(integration):
    p = integration
    await soap.iti38(request_for(p.app, p.incoming))
    with pytest.raises(SoapFault, match="already been used"):
        await soap.iti38(request_for(p.app, p.incoming))
    assert len(p.wire) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "http500", "empty", "partial", "security"])
async def test_nir_failure_policy_keeps_gp_only_for_availability_failures(integration, failure):
    p = integration

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private upstream detail")
        if failure in ("http500", "security"):
            return httpx.Response(403 if failure == "security" else 500)
        root = parse_xml(QUERY_RESPONSE)
        registry = root.find("s:Body/query:AdhocQueryResponse/rim:RegistryObjectList", NS)
        if failure == "empty":
            registry.clear()
        else:
            result = registry.getparent()
            result.set("status", discovery.PARTIAL)
            errors = ET.SubElement(result, f"{{{discovery.RS_NS}}}RegistryErrorList")
            errors.append(discovery.registry_error("One community failed", "urn:oid:other"))
        return httpx.Response(200, content=ET.tostring(root))

    p.transport.handler = respond
    if failure == "security":
        with pytest.raises(SoapFault, match="denied access"):
            await soap.iti38(request_for(p.app, p.incoming))
        return
    result = await soap.iti38(request_for(p.app, p.incoming))
    root = parse_xml(result.body)
    assert root.find("s:Body/query:AdhocQueryResponse", NS).get("status") == (
        discovery.XDS_SUCCESS_STATUS if failure == "empty" else discovery.PARTIAL
    )
    assert len(root.findall(".//rim:ExtrinsicObject", NS)) == (2 if failure == "partial" else 1)
    assert b"private upstream detail" not in result.body


@pytest.mark.asyncio
async def test_sources_run_concurrently_and_audit_failure_cancels_sibling(integration, monkeypatch):
    p = integration
    nir_started, gp_started, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def gp(*args):
        gp_started.set()
        await nir_started.wait()
        raise AuditFailureException("test persistence failure")

    async def nir(*args):
        nir_started.set()
        try:
            await gp_started.wait()
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(discovery, "query_gp", gp)
    monkeypatch.setattr(discovery, "query_nir", nir)
    with pytest.raises(AuditFailureException):
        await asyncio.wait_for(soap.iti38(request_for(p.app, p.incoming)), timeout=1)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_mixed_retrieval_through_xhuma_preserves_binary_and_addresses(integration):
    p = integration
    await soap.iti38(request_for(p.app, p.incoming))
    local = NIRDocumentReference(
        home_community_id=f"urn:oid:{discovery.COMMUNITY_ID}",
        repository_unique_id=discovery.REGISTRY_ID,
        document_unique_id="gp-document",
    )
    incoming = signed_retrieval(p, [local, REFERENCE])
    result = await soap.iti39(request_for(p.app, incoming))
    parsed = parse_retrieve_response(result.body, result.headers["content-type"])
    assert parsed.success
    assert [d.content for d in parsed.documents] == [b"<ClinicalDocument/>", BINARY]
    assert parsed.documents[1].home_community_id == REFERENCE.home_community_id
    outgoing, _ = _unpack_mtom(p.wire[-1].content, p.wire[-1].headers["content-type"])
    verify_context(outgoing, p.signing_cert, XHUMA_ISSUER, p.config.client.base_url, datetime.now(UTC))
    soap_root, _ = _unpack_mtom(result.body, result.headers["content-type"])
    assert parse_xml(soap_root).findtext("s:Header/a:RelatesTo", namespaces=NS) == parse_xml(incoming).findtext(
        "s:Header/a:MessageID", namespaces=NS
    )
    assert p.audit.call_args.kwargs["action"] == "iti39_document_retrieve"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["undiscovered", "expired_mapping", "wrong_patient", "wrong_repository", "duplicate"]
)
async def test_retrieval_requires_authorised_complete_address(integration, failure):
    p = integration
    if failure != "undiscovered":
        await soap.iti38(request_for(p.app, p.incoming))
    if failure == "expired_mapping":
        for key in list(p.values):
            if key.startswith("nir:document:"):
                del p.values[key]
    reference = (
        REFERENCE.model_copy(update={"repository_unique_id": "other"}) if failure == "wrong_repository" else REFERENCE
    )
    patient = "9694116422" if failure == "wrong_patient" else PATIENT
    refs = [reference, reference] if failure == "duplicate" else [reference]
    before = len(p.wire)
    with pytest.raises(SoapFault):
        await soap.iti39(request_for(p.app, signed_retrieval(p, refs, patient)))
    assert len(p.wire) == before


@pytest.mark.asyncio
async def test_retrieval_audit_failure_prevents_release(integration):
    p = integration
    await soap.iti38(request_for(p.app, p.incoming))

    async def audit(**kwargs):
        if kwargs["action"] == "iti39_document_retrieve":
            raise AuditFailureException("test persistence failure")

    p.audit.side_effect = audit
    with pytest.raises(AuditFailureException):
        await soap.iti39(request_for(p.app, signed_retrieval(p, [REFERENCE])))


@pytest.mark.asyncio
async def test_async_delivery_forwards_original_binary_mtom(integration, monkeypatch):
    p = integration
    await soap.iti38(request_for(p.app, p.incoming))
    post = MagicMock(return_value=httpx.Response(200, request=httpx.Request("POST", "https://receiver.nhs.uk")))
    monkeypatch.setattr(iti_39.httpx, "post", post)
    result = await soap.iti39(
        request_for(p.app, signed_retrieval(p, [REFERENCE], reply_to="https://receiver.nhs.uk/soap"))
    )
    assert result.status_code == 202
    await result.background()
    sent = post.call_args.kwargs
    parsed = parse_retrieve_response(sent["data"], sent["headers"]["Content-Type"])
    assert parsed.success and parsed.documents[0].content == BINARY


def test_expired_context_cannot_be_signed_for_later_retrieval(integration):
    p = integration
    context = verify_incoming(p.incoming, p.config, ITI38_ACTION)
    context = replace(context, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(SoapFault, match="expired"):
        sign_outgoing(build_iti39_request([REFERENCE], p.config.client.base_url), context, p.config)


@pytest.mark.asyncio
async def test_gp_cache_miss_reuses_preflight_patient(integration, monkeypatch):
    p = integration
    p.values.pop(PATIENT)
    fetch = AsyncMock(return_value=JSONResponse({"success": True, "document_id": "new-gp-document"}))
    monkeypatch.setattr(iti_38, "_fetch_gpconnect_record", fetch)
    result = await soap.iti38(request_for(p.app, p.incoming))
    assert len(parse_xml(result.body).findall(".//rim:ExtrinsicObject", NS)) == 2
    assert fetch.call_args.kwargs["patient"] is p.patient
    p.pds.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("nir_fails", [False, True])
async def test_gp_failure_does_not_hide_available_nir_documents(integration, monkeypatch, nir_fails):
    p = integration
    p.values.pop(PATIENT)
    # No GP registration can fail the GP branch after shared access succeeds.
    monkeypatch.setattr(
        iti_38,
        "_fetch_gpconnect_record",
        AsyncMock(return_value=JSONResponse({"success": False, "error": "No GP registration"}, status_code=502)),
    )
    if nir_fails:
        p.transport.handler = lambda request: httpx.Response(503)
    result = await soap.iti38(request_for(p.app, p.incoming))
    root = parse_xml(result.body)
    assert root.find("s:Body/query:AdhocQueryResponse", NS).get("status") == (
        discovery.XDS_FAILURE_STATUS if nir_fails else discovery.PARTIAL
    )
    assert len(root.findall(".//rim:ExtrinsicObject", NS)) == (0 if nir_fails else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("transaction", ["iti38", "iti39"])
@pytest.mark.parametrize("http_status", [200, 500])
async def test_soap_security_fault_stops_entire_response(integration, transaction, http_status):
    p = integration
    if transaction == "iti39":
        await soap.iti38(request_for(p.app, p.incoming))
    fault = SoapFault("Upstream security failure", subcode="wsse:FailedAuthentication").response().body
    p.transport.handler = lambda request: httpx.Response(http_status, content=fault)
    content = p.incoming if transaction == "iti38" else signed_retrieval(p, [REFERENCE])
    with pytest.raises(SoapFault, match="security context"):
        await getattr(soap, transaction)(request_for(p.app, content))


@pytest.mark.asyncio
async def test_metadata_and_associations_survive_registry_id_collision(integration):
    p = integration
    root = parse_xml(QUERY_RESPONSE)
    registry = root.find("s:Body/query:AdhocQueryResponse/rim:RegistryObjectList", NS)
    entry = registry[0]
    entry.set("id", "gp-document")  # Collides with the local registry node ID.
    extension = ET.SubElement(
        entry, "{urn:example:metadata-extension}Details", nsmap={"vendor": "urn:example:metadata-extension"}
    )
    for label in ("first & café", "second <value>"):
        ET.SubElement(extension, "{urn:example:metadata-extension}Item").text = label
    name = ET.SubElement(entry, f"{{{discovery.RIM_NS}}}Name")
    ET.SubElement(name, f"{{{discovery.RIM_NS}}}LocalizedString", value="Test scan report")
    ET.SubElement(
        entry,
        f"{{{discovery.RIM_NS}}}Classification",
        id="classification-1",
        classifiedObject="gp-document",
        nodeRepresentation="test-code",
    )
    ET.SubElement(
        registry,
        f"{{{discovery.RIM_NS}}}Association",
        id="association-1",
        sourceObject="gp-document",
        targetObject="other-entry",
    )
    p.transport.handler = lambda request: httpx.Response(200, content=ET.tostring(root))
    result = parse_xml((await soap.iti38(request_for(p.app, p.incoming))).body)
    entries = result.findall(".//rim:ExtrinsicObject", NS)
    remote = next(e for e in entries if e.get("home") == REFERENCE.home_community_id)
    assert remote.get("id") != "gp-document"
    assert remote.find("rim:Classification", NS).get("classifiedObject") == remote.get("id")
    assert result.find(".//rim:Association", NS).get("sourceObject") == remote.get("id")
    assert remote.find("rim:Name/rim:LocalizedString", NS).get("value") == "Test scan report"
    assert remote.find("rim:ExternalIdentifier", NS).get("value") == REFERENCE.document_unique_id
    assert [node.text for node in remote.findall(".//{urn:example:metadata-extension}Item")] == [
        "first & café",
        "second <value>",
    ]


@pytest.mark.asyncio
async def test_same_document_uid_in_two_communities_is_routed_separately(integration):
    p = integration
    other = REFERENCE.model_copy(update={"home_community_id": "urn:oid:other"})
    root = parse_xml(QUERY_RESPONSE)
    registry = root.find("s:Body/query:AdhocQueryResponse/rim:RegistryObjectList", NS)
    entry = deepcopy(registry[0])
    entry.set("id", "second-registry-entry")
    entry.set("home", other.home_community_id)
    registry.append(entry)
    queried_homes = []

    def respond(request):
        if request.url.path.endswith("iti-38"):
            return httpx.Response(200, content=ET.tostring(root))
        envelope, _ = _unpack_mtom(request.content, request.headers["content-type"])
        home = parse_xml(envelope).findtext(f".//{{{discovery.XDS_NS}}}HomeCommunityId")
        queried_homes.append(home)
        reference = REFERENCE if home == REFERENCE.home_community_id else other
        return httpx.Response(200, content=retrieval_response([reference]))

    p.transport.handler = respond
    await soap.iti38(request_for(p.app, p.incoming))
    result = await soap.iti39(request_for(p.app, signed_retrieval(p, [REFERENCE, other])))
    parsed = parse_retrieve_response(result.body, result.headers["content-type"])
    assert parsed.success and len(parsed.documents) == 2
    assert queried_homes == [REFERENCE.home_community_id, other.home_community_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unrequested", "missing", "http500", "invalid_status", "malformed"])
async def test_retrieval_incomplete_keeps_local_document(integration, failure):
    p = integration
    await soap.iti38(request_for(p.app, p.incoming))
    local = NIRDocumentReference(
        home_community_id=f"urn:oid:{discovery.COMMUNITY_ID}",
        repository_unique_id=discovery.REGISTRY_ID,
        document_unique_id="gp-document",
    )
    bad = REFERENCE.model_copy(update={"document_unique_id": "unrequested"})
    content = retrieval_response([bad] if failure == "unrequested" else [])
    if failure == "invalid_status":
        content = retrieval_response([REFERENCE]).replace(discovery.XDS_SUCCESS_STATUS.encode(), b"unknown-status")
    elif failure == "malformed":
        content = b"<not-complete"
    p.transport.handler = lambda request: httpx.Response(500 if failure == "http500" else 200, content=content)
    result = await soap.iti39(request_for(p.app, signed_retrieval(p, [local, REFERENCE])))
    parsed = parse_retrieve_response(result.body, result.headers["content-type"])
    assert parsed.status == discovery.PARTIAL and parsed.errors
    assert len(parsed.documents) == 1 and parsed.documents[0].content == b"<ClinicalDocument/>"


@pytest.mark.asyncio
async def test_nir_cannot_advertise_a_local_cache_address(integration):
    p = integration
    root = parse_xml(QUERY_RESPONSE)
    entry = root.find(".//rim:ExtrinsicObject", NS)
    entry.set("home", f"urn:oid:{discovery.COMMUNITY_ID}")
    entry.find("rim:Slot[@name='repositoryUniqueId']/rim:ValueList/rim:Value", NS).text = discovery.REGISTRY_ID
    p.transport.handler = lambda request: httpx.Response(200, content=ET.tostring(root))
    result = await soap.iti38(request_for(p.app, p.incoming))
    combined = parse_xml(result.body)
    assert combined.find("s:Body/query:AdhocQueryResponse", NS).get("status") == discovery.PARTIAL
    assert len(combined.findall(".//rim:ExtrinsicObject", NS)) == 1


@pytest.mark.asyncio
async def test_discovery_audit_failure_releases_no_response(integration):
    p = integration

    async def audit(**kwargs):
        if kwargs["action"] == "iti38_document_query":
            raise AuditFailureException("test persistence failure")

    p.audit.side_effect = audit
    with pytest.raises(AuditFailureException):
        await soap.iti38(request_for(p.app, p.incoming))


@pytest.mark.asyncio
async def test_invalid_nir_config_stops_startup_before_infrastructure(integration, monkeypatch):
    from app.bootstrap import lifespan as lifecycle

    monkeypatch.setenv("NIR_DISCOVERY_ENABLED", "typo")
    engine = MagicMock()
    monkeypatch.setattr(lifecycle, "make_engine", engine)
    with pytest.raises(ValueError, match="NIR_DISCOVERY_ENABLED"):
        async with lifecycle.lifespan(FastAPI()):
            pytest.fail("Invalid NIR settings must prevent startup")
    engine.assert_not_called()
