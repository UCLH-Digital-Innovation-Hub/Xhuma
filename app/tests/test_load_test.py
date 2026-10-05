"""Exercise the loadtest against real SOAP handlers, with all I/O mocked."""

import base64
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.soap import soap
from app.soap.responses import iti_38
from app.tests.test_soap_requests import _request

NHS = "9692136744"
DOCUMENT = "synthetic-document"


@pytest.fixture
def loadtest(monkeypatch):
    # Locust's normal gevent monkey patching must not change the asyncio test runner.
    monkeypatch.setenv("LOCUST_SKIP_MONKEY_PATCH", "1")
    monkeypatch.setenv("LOCUST_SKIP_URLLIB3_PATCH", "1")
    monkeypatch.setenv("LOCUST_PATIENTS", NHS)
    monkeypatch.delenv("KEY_VAULT_URL", raising=False)
    monkeypatch.delenv("LOCUST_CERT_FILE", raising=False)
    monkeypatch.setenv("SAML_TRUSTED_ISSUER", "urn:nhs:names:services:spine")
    monkeypatch.setenv("LOCUST_SAML_ISSUER", "urn:nhs:names:services:spine")
    path = Path(__file__).parents[2] / "tests/load_tests/locustfile.py"
    spec = importlib.util.spec_from_file_location("loadtest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.EpicClientUser.host = "http://localhost"
    return module


def as_client_response(response):
    return SimpleNamespace(status_code=response.status_code, headers=response.headers, content=response.body)


@pytest.fixture
def mock_io(monkeypatch):
    patient = {
        "id": NHS,
        "meta": {"security": [{"code": "U"}]},
        "name": [{"use": "usual", "given": ["Test"], "family": "Patient"}],
        "gender": "other",
        "birthDate": "1985-06-07",
        "generalPractitioner": [{"identifier": {"value": "TEST"}}],
    }
    monkeypatch.setattr(soap, "lookup_patient", AsyncMock(return_value=patient))
    monkeypatch.setattr(soap, "attempt_audit", AsyncMock())
    monkeypatch.setattr(iti_38, "attempt_audit", AsyncMock())
    monkeypatch.setattr(iti_38, "redis_client", SimpleNamespace(get=AsyncMock(return_value=DOCUMENT.encode())))
    clinical = (
        '<ClinicalDocument xmlns="urn:hl7-org:v3"><recordTarget><patientRole>'
        f'<id root="2.16.840.1.113883.2.1.4.1" extension="{NHS}"/>'
        "</patientRole></recordTarget></ClinicalDocument>"
    ).encode()
    values = {DOCUMENT: base64.b64encode(clinical), f"doc_patient:{DOCUMENT}": NHS.encode()}
    monkeypatch.setattr(soap, "client", SimpleNamespace(get=AsyncMock(side_effect=values.get)))
    return clinical


@pytest.mark.asyncio
async def test_benchmark_journey_matches_real_handlers(loadtest, mock_io):
    # No SAML parsing or response builders are mocked: this checks the actual wire contract.
    response = await soap.iti55(_request(loadtest.discovery_payload(NHS)))
    root = loadtest.response_envelope(as_client_response(response))
    community = loadtest.validate_discovery(root, NHS)

    response = await soap.iti38(_request(loadtest.query_payload(NHS)))
    root = loadtest.response_envelope(as_client_response(response))
    repository, document = loadtest.validate_query(root)
    assert document == DOCUMENT

    payload, content_type = loadtest.retrieval_payload(NHS, community, repository, document)
    assert "\r\n" in payload and "\\r\\n" not in payload
    response = await soap.iti39(_request(payload, content_type))
    root = loadtest.response_envelope(as_client_response(response))
    assert loadtest.validate_retrieval(root, NHS, DOCUMENT) == len(mock_io)
    with pytest.raises(loadtest.JourneyFailure, match="patient mismatch"):
        loadtest.validate_retrieval(root, "9999999999", DOCUMENT)
    with pytest.raises(loadtest.JourneyFailure, match="identifier mismatch"):
        loadtest.validate_retrieval(root, NHS, "wrong-document")


@pytest.mark.asyncio
async def test_200_registry_error_is_a_failure(loadtest, mock_io):
    soap.client.get.side_effect = lambda key: NHS.encode() if key.startswith("doc_patient:") else None
    payload, content_type = loadtest.retrieval_payload(NHS, "1.2.3", "1.2.4", DOCUMENT)
    response = await soap.iti39(_request(payload, content_type))
    assert response.status_code == 200
    with pytest.raises(loadtest.JourneyFailure, match="registry retrieval failed"):
        loadtest.validate_retrieval(loadtest.response_envelope(as_client_response(response)), NHS, DOCUMENT)


@pytest.mark.parametrize("include_headers", [True, False])
def test_multipart_response(loadtest, include_headers):
    xml = f'<s:Envelope xmlns:s="{loadtest.NS["s"]}"><s:Body/></s:Envelope>'
    body = (
        f"--boundary\r\nContent-Type: text/plain\r\nContent-Type: application/xop+xml\r\n\r\n{xml}\r\n--boundary--\r\n"
    )
    content_type = 'multipart/related; boundary="boundary"'
    if include_headers:
        body = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n{body}"
    response = SimpleNamespace(status_code=200, headers={"Content-Type": content_type}, content=body.encode())
    assert loadtest.response_envelope(response).tag.endswith("Envelope")


@pytest.mark.parametrize("content,status", [(b"not XML", 200), (b"", 503), (b"", 0)])
def test_invalid_response_fails(loadtest, content, status):
    with pytest.raises(loadtest.JourneyFailure):
        loadtest.response_envelope(SimpleNamespace(status_code=status, content=content, headers={}))


@pytest.mark.parametrize("fail_query", [False, True])
def test_locust_records_flow_and_stops_on_failure(loadtest, monkeypatch, fail_query):
    from locust.env import Environment

    env = Environment(host="http://localhost")
    user = loadtest.EpicClientUser(env)
    user.on_start()
    events = []
    env.events.request.add_listener(lambda **event: events.append(event))
    values = iter(["1.2.3", ("repository", DOCUMENT), 123])
    calls = []

    def post(endpoint, payload, validate, trace_id, *args):
        calls.append((endpoint, trace_id, payload))
        if fail_query and endpoint == "iti38":
            raise loadtest.JourneyFailure("ITI-38 registry query failed")
        return next(values)

    monkeypatch.setattr(user, "post_soap", post)
    user.document_journey()
    assert [call[0] for call in calls] == (["iti55", "iti38"] if fail_query else ["iti55", "iti38", "iti39"])
    assert len({call[1] for call in calls}) == 1
    (event,) = events
    assert event["request_type"] == "FLOW"
    assert bool(event["exception"]) == fail_query
    assert event["response_length"] == (0 if fail_query else 123)
    assert event["response_time"] >= 0
    if not fail_query:
        assert f"<xds:DocumentUniqueId>{DOCUMENT}</xds:DocumentUniqueId>" in calls[-1][2]


def test_post_marks_http_200_semantic_failure_and_propagates_trace(loadtest):
    from locust.env import Environment

    user = loadtest.EpicClientUser(Environment(host="http://localhost"))
    response = MagicMock(status_code=200, content=b"not XML", headers={})
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    user.client.post = MagicMock(return_value=response)
    trace_id = "1" * 32
    with pytest.raises(loadtest.JourneyFailure):
        user.post_soap("iti38", "<request/>", loadtest.validate_query, trace_id)
    response.failure.assert_called_once_with("Missing or invalid SOAP envelope")
    kwargs = user.client.post.call_args.kwargs
    assert kwargs["name"] == "/SOAP/iti38"
    assert re.fullmatch(f"00-{trace_id}-[0-9a-f]{{16}}-01", kwargs["headers"]["traceparent"])
    assert NHS not in str(kwargs["context"])


def test_configured_certificate_failure_stops_initialization(loadtest, monkeypatch):
    from locust.event import EventHook
    from locust.exception import StopTest

    monkeypatch.setattr(loadtest, "load_certificate", MagicMock(side_effect=ValueError("private setup detail")))
    event = EventHook()
    event.add_listener(loadtest.configure_worker)
    with pytest.raises(StopTest, match="Unable to load the configured load-test certificate"):
        event.fire(environment=None)
