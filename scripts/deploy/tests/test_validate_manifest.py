import json
import os
import subprocess
import tempfile
from copy import deepcopy

SCRIPT_PATH = "scripts/deploy/validate_manifest.py"

VALID_MANIFEST = {
    "repository": "test/repo",
    "source_commit_sha": "abc1234",
    "workflow_run_id": "999",
    "target_id": "play",
    "azure_tenant_id": "tenant-abc",
    "azure_subscription_id": "sub-123",
    "backend_file": "infra/backends/play.hcl",
    "candidate_image_digest": "sha256:abcd" + "e" * 60,
    "plan_blob_path": "play-abc1234-999.tfplan",
    "plan_sha256": "0" * 64,
    "backend_resource_group": "rg-xhuma-play",
    "backend_storage_account": "xtfrgxhumaplay",
    "backend_container": "tfstate",
    "backend_key": "terraform.tfstate",
}

VALID_ENV = {
    "EXPECTED_REPO": "test/repo",
    "EXPECTED_SHA": "abc1234",
    "EXPECTED_RUN_ID": "999",
    "EXPECTED_TARGET": "play",
    "EXPECTED_TENANT": "tenant-abc",
    "EXPECTED_SUB": "sub-123",
    "EXPECTED_BACKEND": "infra/backends/play.hcl",
    "EXPECTED_DIGEST": "sha256:abcd" + "e" * 60,
}


def run_script(manifest_dict, env_vars, mock_backend=None):
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump(manifest_dict, f)
        manifest_path = f.name

    backend_path = None
    if mock_backend:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".hcl") as f:
            f.write(mock_backend)
            backend_path = f.name
        manifest_dict["backend_file"] = backend_path
        env_vars["EXPECTED_BACKEND"] = backend_path
        # write it again since we mutated it
        with open(manifest_path, "w") as f:
            json.dump(manifest_dict, f)

    try:
        env = os.environ.copy()
        env.update(env_vars)
        result = subprocess.run(["python", SCRIPT_PATH, manifest_path], env=env, capture_output=True, text=True)
        return result
    finally:
        os.remove(manifest_path)
        if backend_path:
            os.remove(backend_path)


def get_mock_backend():
    return """
resource_group_name  = "rg-xhuma-mock"
storage_account_name = "xtfrgxhumamock"
container_name       = "tfstate"
key                  = "terraform.tfstate"
"""


def test_valid_manifest_fixture_one():
    # Uses play.hcl already in repo
    res = run_script(VALID_MANIFEST, VALID_ENV)
    assert res.returncode == 0
    assert "Manifest verified successfully" in res.stdout


def test_valid_manifest_fixture_two():
    manifest = deepcopy(VALID_MANIFEST)
    env = deepcopy(VALID_ENV)
    manifest.update(
        {
            "backend_resource_group": "rg-xhuma-mock",
            "backend_storage_account": "xtfrgxhumamock",
        }
    )
    res = run_script(manifest, env, mock_backend=get_mock_backend())
    assert res.returncode == 0
    assert "Manifest verified successfully" in res.stdout


def test_missing_expected_value():
    env = deepcopy(VALID_ENV)
    env["EXPECTED_TENANT"] = ""
    res = run_script(VALID_MANIFEST, env)
    assert res.returncode == 1
    assert "Missing expected environment value" in res.stdout


def test_missing_manifest_field():
    manifest = deepcopy(VALID_MANIFEST)
    manifest["target_id"] = ""
    res = run_script(manifest, VALID_ENV)
    assert res.returncode == 1
    assert "Manifest field missing or empty" in res.stdout


def test_mismatched_fields():
    fields_to_test = list(VALID_ENV.keys())
    for env_key in fields_to_test:
        manifest_key = env_key.replace("EXPECTED_", "").lower()
        if manifest_key == "repo":
            manifest_key = "repository"
        if manifest_key == "sha":
            manifest_key = "source_commit_sha"
        if manifest_key == "run_id":
            manifest_key = "workflow_run_id"
        if manifest_key == "target":
            manifest_key = "target_id"
        if manifest_key == "tenant":
            manifest_key = "azure_tenant_id"
        if manifest_key == "sub":
            manifest_key = "azure_subscription_id"
        if manifest_key == "digest":
            manifest_key = "candidate_image_digest"

        env = deepcopy(VALID_ENV)
        env[env_key] = "WRONG_VALUE"
        res = run_script(VALID_MANIFEST, env)
        assert res.returncode == 1
        assert "Manifest mismatch" in res.stdout


def test_invalid_plan_hash_format():
    manifest = deepcopy(VALID_MANIFEST)
    manifest["plan_sha256"] = "z" * 64
    res = run_script(manifest, VALID_ENV)
    assert res.returncode == 1
    assert "Invalid plan_sha256 format" in res.stdout


def test_invalid_digest_format():
    manifest = deepcopy(VALID_MANIFEST)
    manifest["candidate_image_digest"] = "abcd123"
    env = deepcopy(VALID_ENV)
    env["EXPECTED_DIGEST"] = "abcd123"
    res = run_script(manifest, env)
    assert res.returncode == 1
    assert "Invalid image digest format" in res.stdout


def test_invalid_plan_path():
    manifest = deepcopy(VALID_MANIFEST)
    manifest["plan_blob_path"] = "../other.tfplan"
    res = run_script(manifest, VALID_ENV)
    assert res.returncode == 1
    assert "Invalid plan_blob_path" in res.stdout


def test_missing_backend_coordinates():
    manifest = deepcopy(VALID_MANIFEST)
    del manifest["backend_resource_group"]
    res = run_script(manifest, VALID_ENV)
    assert res.returncode == 1
    assert "Manifest backend field missing" in res.stdout


def test_wrong_backend_coordinates():
    manifest = deepcopy(VALID_MANIFEST)
    manifest["backend_resource_group"] = "rg-wrong"
    res = run_script(manifest, VALID_ENV)
    assert res.returncode == 1
    assert "Manifest mismatch for backend coordinate" in res.stdout
