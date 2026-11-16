"""Run one cold and one warm journey using the benchmark's semantic validators."""

import importlib.util
import sys
from pathlib import Path

import httpx

SPEC = importlib.util.spec_from_file_location(
    "soap_journey", Path(__file__).resolve().parents[2] / "tests/load_tests/locustfile.py"
)
journey = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(journey)
PATIENT = "9690937286"


def check(app_url, mock_url):
    with httpx.Client(base_url=app_url, timeout=60, trust_env=False) as client:
        client.get("/health").raise_for_status()
        jwks = client.get("/jwk")
        jwks.raise_for_status()
        journey.require(bool(jwks.json().get("keys")), "Missing runtime signing key")

        def post(endpoint, body, content_type="application/soap+xml; charset=UTF-8"):
            response = client.post(f"/SOAP/{endpoint}", content=body.encode(), headers={"Content-Type": content_type})
            return journey.response_envelope(response)

        for label in ("cold", "warm"):
            community = journey.validate_discovery(post("iti55", journey.discovery_payload(PATIENT)), PATIENT)
            repository, document = journey.validate_query(post("iti38", journey.query_payload(PATIENT)))
            body, content_type = journey.retrieval_payload(PATIENT, community, repository, document)
            size = journey.validate_retrieval(post("iti39", body, content_type), PATIENT, document)
            print(f"{label}: ITI-55 → ITI-38 → ITI-39 succeeded; decoded C-CDA {size} bytes", flush=True)

    calls = httpx.get(f"{mock_url}/calls", trust_env=False).json()
    for operation in (
        "pds-token",
        "pds",
        "sds-provider-device",
        "sds-provider-endpoint",
        "sds-consumer-device",
        "sds-consumer-endpoint",
        "gpconnect",
        "terminology-token",
        "terminology",
    ):
        journey.require(calls.get(operation, 0) > 0, f"Upstream path was not exercised: {operation}")
    journey.require(calls["gpconnect"] == 1, "Warm journey did not use the document cache")
    print("Cold upstream fetch, terminology conversion and warm document cache verified", flush=True)


if __name__ == "__main__":
    check(*sys.argv[1:])
