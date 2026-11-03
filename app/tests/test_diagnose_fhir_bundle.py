import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from fhirclient.models.fhirabstractbase import FHIRValidationError
from fhirclient.models.patient import Patient

from scripts.diagnose_fhir_bundle import INCOMPLETE, UNKNOWN, diagnose, failures, main

FIXTURE = Path(__file__).parent / "fixtures/bundles/investigations/9465699896.json"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/diagnose_fhir_bundle.py"


def test_preserved_tpp_fixture_and_privacy(capsys):
    original = FIXTURE.read_bytes()
    data = json.loads(original)
    before = deepcopy(data)
    report, status = diagnose(data)
    assert status == 1
    assert data == before
    assert "Bundle entries : 37" in report
    assert "Invalid entries: 4" in report
    rows = [line.split() for line in report.splitlines()]
    assert ["DiagnosticReport", "3"] in rows
    assert ["Specimen", "1"] in rows
    assert ["DiagnosticReport", "performer.actor", "missing_required", "3"] in rows
    assert ["Specimen", "note", "invalid_type", "1"] in rows
    assert main([str(FIXTURE)]) == 1
    captured = capsys.readouterr()
    assert captured.out == report + "\n"
    assert captured.err == ""
    assert FIXTURE.read_bytes() == original

    # Check every nontrivial string value, not just a hand-picked NHS number.
    # resourceType is the only fixture value intentionally present in output.
    def private_values(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key != "resourceType":
                    yield from private_values(child)
        elif isinstance(value, list):
            for child in value:
                yield from private_values(child)
        elif isinstance(value, str) and len(value) >= 4:
            yield value

    for value in private_values(data):
        assert value not in captured.out + captured.err
    for value in ("9465699896", "Theresa", "Bibb", "1935-02-18", "LEFT KNEE", "Amount Collected", "West Farm Surgery"):
        assert value not in report


def test_valid_bundle_and_first_comment_compatibility():
    data = {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": {"resourceType": "Patient"}}]}
    assert diagnose(data)[1] == 0
    data["entry"].insert(0, {"fhir_comments": ["PRIVATE"]})
    before = deepcopy(data)
    report, status = diagnose(data)
    assert status == 0
    assert "compatibility): 1" in report
    assert data == before
    data["entry"].append({"fhir_comments": ["PRIVATE"]})
    report, status = diagnose(data)
    assert status == 2
    assert "Invalid entries: 1" in report
    assert INCOMPLETE in report
    assert "PRIVATE" not in report


@pytest.mark.parametrize(
    "entry",
    [
        "PRIVATE",
        {"resource": "PRIVATE"},
        {"resource": {"resourceType": "PRIVATE", "PRIVATE": "PRIVATE"}},
        {"resource": {"resourceType": "Patient", "PRIVATE": "PRIVATE"}},
        {"resource": {"resourceType": "Patient", "birthDate": "PRIVATE"}},
        {"resource": {"resourceType": "Patient", "active": "PRIVATE"}},
    ],
)
def test_adversarial_content_never_reaches_cli(entry, tmp_path):
    source = tmp_path / "PRIVATE.json"
    source.write_text(json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [entry]}))
    result = subprocess.run([sys.executable, str(SCRIPT), str(source)], capture_output=True, text=True)
    assert result.returncode in (1, 2)
    if isinstance(entry, dict) and isinstance(entry.get("resource"), dict) and "birthDate" in entry["resource"]:
        assert result.returncode == 2
        assert "validator emitted suppressed warnings" in result.stdout
    else:
        assert "Invalid entries: 1" in result.stdout
    assert "PRIVATE" not in result.stdout + result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize(
    "payload", ["PRIVATE", "[]", '{"resourceType":"PRIVATE"}', '{"resourceType":"Bundle","entry":{}}']
)
def test_bad_input_is_safe(payload, tmp_path, capsys):
    source = tmp_path / "PRIVATE.json"
    source.write_text(payload)
    assert main([str(source)]) == 2
    source.unlink()
    assert main([str(source)]) == 2
    output = capsys.readouterr()
    assert "PRIVATE" not in output.out + output.err
    assert output.err == ""


def test_unknown_exception_and_unverified_path_fall_back():
    assert list(failures(FHIRValidationError([ValueError("PRIVATE")], "PRIVATE"), Patient())) == [UNKNOWN]
    assert list(failures(KeyError('Non-optional property "PRIVATE" on PRIVATE is missing'), Patient())) == [UNKNOWN]
    report, status = diagnose({"resourceType": "Bundle", "entry": []})
    assert status == 2
    assert INCOMPLETE in report


def test_repeated_failure_counts_entries_not_occurrences():
    entry = {"resource": {"resourceType": "DiagnosticReport", "status": "final", "code": {}, "performer": [{}, {}]}}
    report, status = diagnose({"resourceType": "Bundle", "type": "collection", "entry": [entry, entry]})
    assert status == 1
    assert ["DiagnosticReport", "performer.actor", "missing_required", "2"] in [r.split() for r in report.splitlines()]
