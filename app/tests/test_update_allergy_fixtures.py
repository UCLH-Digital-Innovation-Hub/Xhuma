import json
from pathlib import Path

import pytest
from fastapi.responses import JSONResponse

from scripts.update_allergy_fixtures import FIXTURES, PATIENTS, refresh_fixture, validate_bundle


@pytest.mark.parametrize("nhs_number", PATIENTS)
def test_saved_test_pack_bundles_pass_refresh_validation(nhs_number):
    validate_bundle((FIXTURES / f"{nhs_number}.json").read_bytes(), nhs_number)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["success", "http_error", "conversion_error", "wrong_patient", "not_bundle", "missing_list", "exception"]
)
async def test_refresh_replaces_only_successful_matching_bundles(tmp_path, outcome):
    nhs_number = "9738345251"
    payload = (FIXTURES / f"{nhs_number}.json").read_bytes()
    if outcome == "wrong_patient":
        payload = (FIXTURES / "9738345278.json").read_bytes()
    elif outcome == "not_bundle":
        payload = b'{"resourceType":"OperationOutcome"}'
    elif outcome == "missing_list":
        data = json.loads(payload)
        data["entry"] = [e for e in data["entry"] if e.get("resource", {}).get("resourceType") != "List"]
        payload = json.dumps(data).encode()
    destination = tmp_path / f"{nhs_number}.json"
    destination.write_bytes(b"original fixture")
    log_paths = []

    async def fetch(nhsno, saml, log_dir, request):
        """Simulate the shared fetcher's logged response and final conversion status."""
        assert nhsno == int(nhs_number)
        log_paths.append(Path(log_dir))
        (Path(log_dir) / "200_response.json").write_bytes(payload)
        if outcome == "exception":
            raise RuntimeError("Request failed")
        if outcome == "http_error":
            return JSONResponse({"success": False}, status_code=403)
        if outcome == "conversion_error":
            return JSONResponse({"success": False}, status_code=500)
        return JSONResponse({"success": True, "document_id": "test"})

    if outcome == "success":
        await refresh_fixture(nhs_number, fetch, None, None, destination)
        assert destination.read_bytes() == payload
    else:
        with pytest.raises((ValueError, RuntimeError)):
            await refresh_fixture(nhs_number, fetch, None, None, destination)
        assert destination.read_bytes() == b"original fixture"
    assert all(not path.exists() for path in log_paths)
    assert list(tmp_path.iterdir()) == [destination]


def test_manifest_coverage_and_supplier_selection():
    from scripts.update_allergy_fixtures import DOMAINS, MANIFEST, select_patients

    manifest = json.loads(MANIFEST.read_text())
    patients = select_patients(manifest, list(DOMAINS))
    assert {domain: sum(p["domain"] == domain for p in patients) for domain in DOMAINS} == {
        "allergies": 13,
        "medication": 2,
        "investigations": 4,
        "immunisations": 0,
        "problems": 0,
        "uncategorised": 2,
        "additional": 3,
    }
    selected = select_patients(manifest, ["medication", "investigations"], "TPP")
    assert {(p["domain"], p["nhs_number"]) for p in selected} == {
        ("medication", "9692136744"),
        ("investigations", "9692136744"),
        ("investigations", "9465699896"),
    }
    assert all(p["sources"] for p in patients)
    assert any("11-digit" in gap["reason"] for gap in manifest["gaps"])
    # Do not permit a transcription error in the reviewed patient manifest.
    from app.ccda.helpers import validateNHSnumber

    assert all(validateNHSnumber(int(p["nhs_number"])) for p in patients)


def test_domain_switch_updates_request_and_conversion_flags(monkeypatch):
    from app.gp_connect_config import build_gp_connect_parameters, get_gp_connect_inclusions
    from scripts.update_allergy_fixtures import DOMAIN_TITLES, configure_domain

    for domain in DOMAIN_TITLES:
        monkeypatch.setenv(f"GP_CONNECT_INCLUDE_{domain.upper()}", "false")
    for domain, parameter in [
        ("allergies", "includeAllergies"),
        ("investigations", "includeInvestigations"),
        ("medication", "includeMedication"),
        ("immunisations", "includeImmunisations"),
        ("problems", "includeProblems"),
    ]:
        inclusions = configure_domain(domain)
        assert inclusions == get_gp_connect_inclusions()
        assert [p["name"] for p in build_gp_connect_parameters(inclusions)] == [parameter]
    assert all(configure_domain("additional").values())
    with pytest.raises(ValueError, match="not supported"):
        configure_domain("uncategorised")


@pytest.mark.parametrize("nhs_number", ["9730333939", "9465700088", "9692136744"])
def test_investigation_validation(nhs_number):
    from scripts.update_allergy_fixtures import BUNDLE_ROOT

    payload = (BUNDLE_ROOT / "investigations" / f"{nhs_number}.json").read_bytes()
    validate_bundle(payload, nhs_number, "investigations")
    with pytest.raises(ValueError, match="domain list"):
        validate_bundle(payload, nhs_number, "allergies")


def test_malformed_investigation_fixture_is_still_rejected():
    from scripts.update_allergy_fixtures import BUNDLE_ROOT

    with pytest.raises(Exception):
        validate_bundle((BUNDLE_ROOT / "investigations/9465699896.json").read_bytes(), "9465699896", "investigations")


def test_replace_creates_domain_directory(tmp_path):
    from scripts.update_allergy_fixtures import replace_fixture

    target = tmp_path / "medication" / "patient.json"
    replace_fixture(target, b"new fixture")
    assert target.read_bytes() == b"new fixture"
    assert list(target.parent.iterdir()) == [target]


def test_dry_run_needs_no_credentials():
    import os
    import subprocess
    import sys

    from scripts.update_allergy_fixtures import ROOT

    env = {k: v for k, v in os.environ.items() if k not in ("API_KEY", "ORG_CODE", "ORG_ASID", "KID")}
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/update_allergy_fixtures.py"),
            "--domain",
            "investigations",
            "--supplier",
            "TPP",
            "--dry-run",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "investigations: 2 patient(s)" in result.stdout
    assert "9465699896" in result.stdout
    assert "EMIS" not in result.stdout
