#!/usr/bin/env python3
"""Refresh the 13 allergy test-pack fixtures through the audited GP Connect path."""

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "app/tests/fixtures/bundles/allergies"
PATIENTS = {
    "9738345251": "EMIS",
    "9738345367": "EMIS",
    "9738345286": "EMIS",
    "9738345510": "EMIS",
    "9738345308": "EMIS",
    "9738345316": "EMIS",
    "9738345278": "TPP",
    "9738345375": "TPP",
    "9738345324": "TPP",
    "9738345529": "TPP",
    "9738345340": "TPP",
    "9738345359": "TPP",
    "9738345405": "TPP",
}


def validate_bundle(payload: bytes, nhs_number: str) -> None:
    """Require a parseable FHIR bundle for the requested patient with an allergy list."""
    from fhirclient.models.bundle import Bundle

    data = json.loads(payload)
    if data.get("resourceType") != "Bundle":
        raise ValueError("Response is not a FHIR Bundle")
    resources = [entry.get("resource", {}) for entry in data.get("entry", [])]
    patients = [r for r in resources if r.get("resourceType") == "Patient"]
    if len(patients) != 1 or not any(
        i.get("system") == "https://fhir.nhs.uk/Id/nhs-number" and i.get("value") == nhs_number
        for i in patients[0].get("identifier", [])
    ):
        raise ValueError("Response patient does not match the requested NHS number")
    if not any(
        r.get("resourceType") == "List" and r.get("title") == "Allergies and adverse reactions" for r in resources
    ):
        raise ValueError("Response does not contain an allergy list")
    # Mirror the GP Connect parser's handling of a standalone comments entry,
    # without changing the original bytes saved in the fixture.
    for index, entry in enumerate(data.get("entry", [])):
        if "fhir_comments" in entry:
            data["entry"].pop(index)
            break
    Bundle(data)


def replace_fixture(destination: Path, payload: bytes) -> None:
    """Atomically replace a fixture using a temporary file on the same filesystem."""
    temporary = None
    try:
        with NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def refresh_fixture(nhs_number, fetch, request, saml, destination, timeout=120):
    """Save the raw response only after the audited request and conversion succeed.

    The shared GP Connect implementation logs response bodies before converting
    them. Its request headers and other temporary logs are removed on exit.
    A failed request, conversion, audit or validation leaves the fixture intact.
    """
    with TemporaryDirectory(prefix="xhuma-allergy-refresh-") as logs:
        response = await asyncio.wait_for(fetch(int(nhs_number), saml, log_dir=logs, request=request), timeout=timeout)
        result = json.loads(response.body)
        if response.status_code != 200 or result.get("success") is not True:
            raise RuntimeError(f"GP Connect failed (HTTP {response.status_code}); fixture retained")
        payload = (Path(logs) / "200_response.json").read_bytes()
        validate_bundle(payload, nhs_number)
        replace_fixture(destination, payload)


async def refresh_all(subject: str) -> int:
    """Request each fixed integration-test patient with normal audit persistence."""
    from fastapi import FastAPI, Request

    from app.audit.models import SAMLAttributes
    from app.ccda.models.datatypes import CD
    from app.db import make_engine, make_sessionmaker
    from app.gpconnect import _fetch_gpconnect_record

    engine = make_engine()
    app = FastAPI()
    app.state.SessionLocal = make_sessionmaker(engine)
    app.state.ccda_expiry_hours = 4
    app.state.jwk_json = {"kid": os.environ["KID"]}
    failures = 0
    try:
        for nhs_number, vendor in PATIENTS.items():
            request = Request(
                {
                    "type": "http",
                    "app": app,
                    "method": "GET",
                    "scheme": "http",
                    "path": f"/gpconnect/{nhs_number}",
                    "query_string": b"",
                    "headers": [(b"x-request-id", str(uuid4()).encode())],
                    "client": ("127.0.0.1", 0),
                    "server": ("localhost", 0),
                }
            )
            saml = SAMLAttributes(
                subject_id=f"{subject} (allergy fixture refresh)",
                organization="UCLH - University College London Hospitals - TST",
                organization_id="urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
                home_community_id="urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
                role=CD(
                    code="224608005",
                    codeSystem="2.16.840.1.113883.6.96",
                    codeSystemName="SNOMED_CT",
                    displayName="Administrative healthcare staff",
                ),
                purpose_of_use=CD(code="TREATMENT", codeSystem="2.16.840.1.113883.3.18.7.1"),
                resource_id=nhs_number,
            )
            print(f"Requesting {vendor} {nhs_number}", flush=True)
            try:
                await refresh_fixture(
                    nhs_number, _fetch_gpconnect_record, request, saml, FIXTURES / f"{nhs_number}.json"
                )
            except Exception as exc:
                failures += 1
                print(f"FAILED {nhs_number}: {type(exc).__name__}; existing fixture retained", file=sys.stderr)
            else:
                print(f"Updated {nhs_number}.json", flush=True)
    finally:
        await engine.dispose()
    print(f"Updated {len(PATIENTS) - failures}/{len(PATIENTS)} fixtures; {failures} failed.")
    return 1 if failures else 0


def main() -> int:
    """Load local credentials and run an integration-only, active-allergy refresh."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-asid", help="Requesting ASID; otherwise use ORG_ASID from the environment/.env")
    parser.add_argument("--subject", default=getpass.getuser(), help="Audit caller identity (default: local username)")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if args.org_asid:
        os.environ["ORG_ASID"] = args.org_asid
    for name in ("API_KEY", "ORG_CODE", "ORG_ASID", "KID"):
        if not os.environ.get(name, "").strip():
            parser.error(f"Missing {name}; configure it in the environment or .env")
    # Set these before importing GP Connect: its endpoint and request parameters
    # are calculated at import time. This script always uses direct integration TLS.
    os.environ["ENV"] = "int"
    os.environ["USE_RELAY"] = "false"
    os.environ["GP_CONNECT_INCLUDE_ALLERGIES"] = "true"
    for domain in ("MEDICATION", "PROBLEMS", "INVESTIGATIONS", "IMMUNISATIONS"):
        os.environ[f"GP_CONNECT_INCLUDE_{domain}"] = "false"
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)  # Existing signing-key and TLS-certificate paths are relative.
    os.umask(0o077)
    return asyncio.run(refresh_all(args.subject))


if __name__ == "__main__":
    raise SystemExit(main())
