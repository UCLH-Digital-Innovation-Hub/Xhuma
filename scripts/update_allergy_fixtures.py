#!/usr/bin/env python3
"""Refresh clinical test-pack fixtures through the audited GP Connect path."""

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
BUNDLE_ROOT = ROOT / "app/tests/fixtures/bundles"
MANIFEST = Path(__file__).with_name("test_pack_patients.json")
DOMAIN_TITLES = {
    "allergies": "Allergies and adverse reactions",
    "medication": "Medications and medical devices",
    "investigations": "Investigations and results",
    "immunisations": "Immunisations",
    "problems": "Problems",
}
DOMAINS = (*DOMAIN_TITLES, "uncategorised", "additional")
# Preserve imports used by the original allergy refresh tests and tooling.
FIXTURES = BUNDLE_ROOT / "allergies"
PATIENTS = {
    p["nhs_number"]: p["supplier"] for p in json.loads(MANIFEST.read_text())["patients"] if p["domain"] == "allergies"
}


def select_patients(manifest: dict, domains: list[str], supplier: str | None = None) -> list[dict]:
    """Select unique domain/patient requests from the reviewed spreadsheet manifest."""
    selected = {}
    for patient in manifest["patients"]:
        if patient["domain"] in domains and (supplier is None or patient["supplier"] == supplier):
            selected[(patient["domain"], patient["nhs_number"])] = patient
    return list(selected.values())


def configure_domain(domain: str) -> dict:
    """Select the request and conversion domains together for a sequential refresh.

    Additional tests span clinical domains, so request all supported domains.
    Uncategorised data has no request/converter support in the application yet.
    """
    if domain == "uncategorised":
        raise ValueError("Uncategorised data is not supported by the current GP Connect request configuration")
    inclusions = {}
    for name in DOMAIN_TITLES:
        enabled = domain in (name, "additional")
        os.environ[f"GP_CONNECT_INCLUDE_{name.upper()}"] = str(enabled).lower()
        inclusions[f"include_{name}"] = enabled
    return inclusions


def validate_bundle(payload: bytes, nhs_number: str, domain: str = "allergies") -> None:
    """Require a parseable FHIR bundle for the requested patient with the requested domain list."""
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
    expected = set(DOMAIN_TITLES.values()) if domain == "additional" else {DOMAIN_TITLES[domain]}
    actual = {r.get("title") for r in resources if r.get("resourceType") == "List"}
    if not expected.issubset(actual):
        raise ValueError("Response is missing a requested domain list")
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
        destination.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def refresh_fixture(nhs_number, fetch, request, saml, destination, timeout=120, domain="allergies"):
    """Save the raw response only after the audited request and conversion succeed.

    The shared GP Connect implementation logs response bodies before converting
    them. Its request headers and other temporary logs are removed on exit.
    A failed request, conversion, audit or validation leaves the fixture intact.
    """
    with TemporaryDirectory(prefix="xhuma-fixture-refresh-") as logs:
        response = await asyncio.wait_for(fetch(int(nhs_number), saml, log_dir=logs, request=request), timeout=timeout)
        result = json.loads(response.body)
        if response.status_code != 200 or result.get("success") is not True:
            raise RuntimeError(f"GP Connect failed (HTTP {response.status_code}); fixture retained")
        payload = (Path(logs) / "200_response.json").read_bytes()
        validate_bundle(payload, nhs_number, domain)
        replace_fixture(destination, payload)


async def refresh_all(subject: str, patients: list[dict], output_dir: Path = BUNDLE_ROOT) -> int:
    """Request each selected integration-test patient with normal audit persistence."""
    from fastapi import FastAPI, Request

    from app import gpconnect
    from app.audit.models import SAMLAttributes
    from app.ccda.models.datatypes import CD
    from app.db import make_engine, make_sessionmaker
    from app.gp_connect_config import build_gp_connect_parameters

    engine = make_engine()
    app = FastAPI()
    app.state.SessionLocal = make_sessionmaker(engine)
    app.state.ccda_expiry_hours = 4
    app.state.jwk_json = {"kid": os.environ["KID"]}
    failures = 0
    try:
        for patient in patients:
            nhs_number, vendor, domain = patient["nhs_number"], patient["supplier"], patient["domain"]
            if domain == "uncategorised":
                failures += 1
                print(
                    "SKIPPED uncategorised: application request/converter support is not implemented", file=sys.stderr
                )
                continue
            # GP Connect imports these parameters once; update its local reference
            # as well as the conversion flags. Requests are deliberately sequential.
            gpconnect.GP_CONNECT_PARAMETERS = build_gp_connect_parameters(configure_domain(domain))
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
                subject_id=f"{subject} ({domain} fixture refresh)",
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
            print(f"Requesting {domain} {vendor} {nhs_number}", flush=True)
            try:
                await refresh_fixture(
                    nhs_number,
                    gpconnect._fetch_gpconnect_record,
                    request,
                    saml,
                    output_dir / domain / f"{nhs_number}.json",
                    domain=domain,
                )
            except Exception as exc:
                failures += 1
                print(f"FAILED {nhs_number}: {type(exc).__name__}; existing fixture retained", file=sys.stderr)
            else:
                print(f"Updated {nhs_number}.json", flush=True)
    finally:
        await engine.dispose()
    print(f"Updated {len(patients) - failures}/{len(patients)} fixtures; {failures} failed.")
    return 1 if failures else 0


def main() -> int:
    """Select spreadsheet patients and run an integration-only audited refresh."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-asid", help="Requesting ASID; otherwise use ORG_ASID from the environment/.env")
    parser.add_argument("--subject", default=getpass.getuser(), help="Audit caller identity (default: local username)")
    parser.add_argument(
        "--domain",
        nargs="+",
        choices=("all", *DOMAINS),
        default=["all"],
        help="Domains to refresh (default: all); additional requests all supported domains",
    )
    parser.add_argument("--supplier", choices=("EMIS", "TPP"), help="Limit to one supplier")
    parser.add_argument(
        "--dry-run", action="store_true", help="List requests and coverage gaps without credentials or network calls"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=BUNDLE_ROOT, help="Parent directory for domain fixture folders"
    )
    args = parser.parse_args()
    domains = list(DOMAINS) if "all" in args.domain else args.domain
    manifest = json.loads(MANIFEST.read_text())
    patients = select_patients(manifest, domains, args.supplier)
    for gap in manifest["gaps"]:
        if gap["domain"] in domains and (args.supplier is None or gap["supplier"] == args.supplier):
            print(f"GAP {gap['supplier']} {gap['sheet']}: {gap['reason']}", file=sys.stderr)
    for domain in domains:
        count = sum(p["domain"] == domain for p in patients)
        print(f"{domain}: {count} patient(s)")
    if args.dry_run:
        for patient in patients:
            print(f"{patient['domain']} {patient['supplier']} {patient['nhs_number']}")
        if "uncategorised" in domains:
            print("Uncategorised requests are unsupported and will be skipped.")
        return 0
    if not patients:
        print("No identified patients to refresh for this selection.", file=sys.stderr)
        return 1
    output_dir = args.output_dir.resolve()

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
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)  # Existing signing-key and TLS-certificate paths are relative.
    os.umask(0o077)
    return asyncio.run(refresh_all(args.subject, patients, output_dir))


if __name__ == "__main__":
    raise SystemExit(main())
