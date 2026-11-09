import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Response
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from starlette.requests import Request

from app.audit.models import SAMLAttributes
from app.ccda.models.datatypes import CD
from app.tests.gpconnect_helpers import fake_sds_device_trace, fake_sds_endpoint_trace


@pytest.fixture
def observed_pipeline(monkeypatch, tmp_path):
    """Observe real orchestration, validation, conversion and audit building with synthetic I/O.

    Only dependency import targets should need updating after extraction. The PDS
    patient cache is warm; the document cache is cold. No clinical fixtures,
    network, database or Redis connections are used by this fixture.
    """
    gp = importlib.import_module("app.gpconnect")
    pds = importlib.import_module("app.pds.pds")
    iti38 = importlib.import_module("app.soap.responses.iti_38")
    audit_build = importlib.import_module("app.audit.build")
    audit_store = importlib.import_module("app.audit.store")
    telemetry = importlib.import_module("app.telemetry")
    probe = SimpleNamespace(
        gp=gp,
        patient_stage=importlib.import_module("app.gpconnect_stages.patient"),
        request_stage=importlib.import_module("app.gpconnect_stages.request"),
        routing=importlib.import_module("app.gpconnect_stages.routing"),
        document=importlib.import_module("app.gpconnect_stages.document"),
        transport=importlib.import_module("app.gpconnect_stages.transport"),
        nhs="9999999999",  # Fabricated checksum-valid identifier, never sent externally.
        events=[],
        event_spans=[],
        failures={},
        audits=[],
        persisted=[],
        fail_audit=None,
        commands=[],
        published=False,
        ttl=1.5,
        endpoint=fake_sds_endpoint_trace(),
        device=fake_sds_device_trace(),
        saml=SAMLAttributes(
            subject_id="Synthetic, Clinician",
            organization="Synthetic Organisation",
            organization_id="urn:oid:1.2.826.0.1.3680043.10.999",
            role=CD(code="synthetic-role", codeSystemName="synthetic-system"),
        ),
    )

    def note(event):
        probe.events.append(event)
        probe.event_spans.append((event, trace.get_current_span().get_span_context().span_id))
        if event in probe.failures:
            raise probe.failures[event]

    probe.note = note
    patient = {
        "resourceType": "Patient",
        "id": "synthetic-patient",
        "identifier": [{"system": "https://fhir.nhs.uk/Id/nhs-number", "value": probe.nhs}],
        "name": [{"family": "Synthetic", "given": ["Patient"]}],
        "birthDate": "2000-01-01",
    }
    probe.patient = {
        **patient,
        "meta": {"security": [{"code": "U"}]},
        "generalPractitioner": [{"identifier": {"value": "SYN001"}}],
    }
    probe.http_response = Response(
        200, json={"resourceType": "Bundle", "type": "collection", "entry": [{"resource": patient}]}
    )
    probe.exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(probe.exporter))
    probe.tracer = provider.get_tracer("synthetic-pipeline-test")
    monkeypatch.setattr(telemetry, "tracer", probe.tracer)
    monkeypatch.setattr(telemetry, "stage_duration", MagicMock())
    monkeypatch.setattr(telemetry, "cache_lookups", MagicMock())
    monkeypatch.setenv("ENV", "int")  # Suppress the optional local XML write unless explicitly tested.
    monkeypatch.setenv("ORG_ASID", "synthetic-consumer-asid")
    monkeypatch.setenv("CCDA_EXPIRY_HOURS", "9")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gp, "USE_RELAY", False)
    monkeypatch.setattr(gp, "environment", "int")
    monkeypatch.setattr(gp, "OVER_INTERNET_PATH", "https://proxy.example.invalid")
    monkeypatch.setattr(
        probe.transport, "get_nhs_cert_paths", lambda: ("synthetic-cert", "synthetic-key", "synthetic-ca")
    )
    monkeypatch.setattr(probe.transport, "create_nhs_ssl_context", MagicMock())

    # Exercise the real attempt_audit/build_audit_event, replacing only persistence.
    session = SimpleNamespace(commit=AsyncMock())
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_factory = MagicMock(return_value=session_context)
    sequence = iter(range(1, 100))
    monkeypatch.setattr(audit_build, "next_audit_sequence", AsyncMock(side_effect=lambda _: next(sequence)))

    async def insert(_session, event):
        if event.event.action == probe.fail_audit:
            raise RuntimeError("synthetic persistence failure")
        probe.persisted.append(event)

    monkeypatch.setattr(audit_store, "insert_audit_event", insert)
    original_audit = gp.attempt_audit

    async def audit(**kwargs):
        note(f"audit:{kwargs['action']}:{kwargs['outcome'].value}")
        probe.audits.append(kwargs)
        await original_audit(**kwargs)

    for module in (importlib.import_module("app.audit.audit"), pds, iti38):
        monkeypatch.setattr(module, "attempt_audit", audit)

    class ExpiryState(SimpleNamespace):
        @property
        def ccda_expiry_hours(self):
            # hasattr and the value read both access this property. Observe the
            # configuration boundary, without constraining that access count.
            if "ttl" not in probe.events:
                note("ttl")
            return probe.ttl

    probe.state = ExpiryState(SessionLocal=session_factory)
    probe.request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/SOAP/iti38",
            "headers": [(b"x-request-id", b"synthetic-inbound-request")],
            "client": ("127.0.0.1", 12345),
            "app": SimpleNamespace(state=probe.state),
        }
    )
    original_validate = probe.patient_stage.validateNHSnumber

    def validate(value):
        note("nhs.validate")
        return original_validate(value)

    monkeypatch.setattr(probe.patient_stage, "validateNHSnumber", validate)
    original_patient = probe.patient_stage.lookup_patient

    async def lookup(*args, **kwargs):
        note("pds")
        return await original_patient(*args, **kwargs)

    async def patient_cache(_key):
        return json.dumps(probe.patient).encode()

    monkeypatch.setattr(probe.patient_stage, "lookup_patient", lookup)
    monkeypatch.setattr(pds, "redis_client", SimpleNamespace(get=AsyncMock(side_effect=patient_cache)))

    async def sds(ods, **kwargs):
        assert ods == "SYN001"
        if kwargs == {"endpoint": True}:
            note("sds.endpoint")
            return probe.endpoint
        assert kwargs == {"mhsparty": "FAKE-PARTY-KEY"}
        note("sds.device")
        return probe.device

    async def issuer():
        note("issuer")
        return "https://consumer.example.invalid/spine"

    monkeypatch.setattr(probe.routing, "sds_trace", sds)
    monkeypatch.setattr(probe.request_stage, "lookup_self_issuer", issuer)
    original_jwt = probe.request_stage.create_jwt

    def token(*args, **kwargs):
        note("jwt")
        return original_jwt(*args, **kwargs)

    monkeypatch.setattr(probe.request_stage, "create_jwt", token)
    original_inject = probe.request_stage.inject

    def inject(headers):
        note("inject")
        original_inject(headers)

    monkeypatch.setattr(probe.request_stage, "inject", inject)

    async def post(url, *, json, headers):
        note("http.post")
        probe.sent = SimpleNamespace(url=url, body=json, headers=dict(headers))
        note("http.response")
        return probe.http_response

    client = AsyncMock()
    client.post.side_effect = post
    monkeypatch.setattr(probe.transport.httpx, "AsyncClient", MagicMock(return_value=client))
    client.__aenter__.return_value = client
    original_bundle = probe.document.bundle.Bundle

    def model(payload):
        note("fhir.models")
        return original_bundle(payload)

    monkeypatch.setattr(probe.document, "bundle", SimpleNamespace(Bundle=model))
    original_convert = probe.document.convert_bundle

    async def convert(bundle, index):
        note("convert")
        return await original_convert(bundle, index)

    monkeypatch.setattr(probe.document, "convert_bundle", convert)
    original_serialize = probe.document.base64_xml

    def serialize(document):
        note("serialize")
        return original_serialize(document)

    monkeypatch.setattr(probe.document, "base64_xml", serialize)

    class Pipeline:
        async def __aenter__(self):
            note("redis.enter")
            return self

        async def __aexit__(self, *_):
            note("redis.exit")

        def set(self, key, value, *, ex):
            note("redis.set")
            probe.commands.append(("set", key, value, ex))

        def hset(self, key, *, mapping):
            note("redis.hset")
            probe.commands.append(("hset", key, mapping))

        def expire(self, key, ttl):
            note("redis.expire")
            probe.commands.append(("expire", key, ttl))

        async def execute(self):
            note("redis.execute")
            probe.published = True

    monkeypatch.setattr(probe.document, "redis_client", SimpleNamespace(pipeline=Pipeline))
    monkeypatch.setattr(iti38, "redis_client", SimpleNamespace(get=AsyncMock(return_value=None)))
    original_response = gp.JSONResponse

    def response(*args, **kwargs):
        note("json.response")
        return original_response(*args, **kwargs)

    monkeypatch.setattr(gp, "JSONResponse", response)
    monkeypatch.setattr(probe.patient_stage, "JSONResponse", response)
    monkeypatch.setattr(probe.request_stage, "JSONResponse", response)
    monkeypatch.setattr(probe.routing, "JSONResponse", response)
    monkeypatch.setattr(probe.document, "JSONResponse", response)
    try:
        yield probe
    finally:
        provider.shutdown()


@pytest.fixture
def observed_relay(observed_pipeline, monkeypatch):
    """Capture the relay envelope and reconstructed response at existing I/O boundaries."""
    p = observed_pipeline
    settings = importlib.import_module("app.settings")
    monkeypatch.setattr(p.gp, "USE_RELAY", True)
    monkeypatch.setattr(p.gp, "RELAY_BASE_PATH", "https://relay.example.invalid/")
    monkeypatch.setattr(settings, "EXTERNAL_RELAY_URL", "")
    monkeypatch.setattr(settings, "EXTERNAL_RELAY_CLIENT_ID", "synthetic-client")
    monkeypatch.setattr(settings, "EXTERNAL_RELAY_TOKEN", "synthetic-token")
    p.relay_payload = {
        "status_code": "200",
        "body": p.http_response.json(),
        "headers": {"content-encoding": "gzip", "x-upstream": "discard"},
    }
    p.state.relay_hub = SimpleNamespace(send=AsyncMock(side_effect=lambda _: p.relay_payload))
    p.rebuilt = []
    real_response = Response

    def rebuild(*args, **kwargs):
        response = real_response(*args, **kwargs)
        p.rebuilt.append(response)
        return response

    monkeypatch.setattr(p.transport.httpx, "Response", rebuild)
    p.settings = settings
    return p
