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
    "candidate_image_digest": "sha256:abcd",
    "plan_blob_path": "play-abc1234-999.tfplan",
    "plan_sha256": "0" * 64,
}

VALID_ENV = {
    "EXPECTED_REPO": "test/repo",
    "EXPECTED_SHA": "abc1234",
    "EXPECTED_RUN_ID": "999",
    "EXPECTED_TARGET": "play",
    "EXPECTED_TENANT": "tenant-abc",
    "EXPECTED_SUB": "sub-123",
    "EXPECTED_BACKEND": "infra/backends/play.hcl",
    "EXPECTED_DIGEST": "sha256:abcd",
}


def run_script(manifest_dict, env_vars):
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump(manifest_dict, f)
        manifest_path = f.name

    try:
        env = os.environ.copy()
        env.update(env_vars)
        result = subprocess.run(["python", SCRIPT_PATH, manifest_path], env=env, capture_output=True, text=True)
        return result
    finally:
        os.remove(manifest_path)


def test_valid_manifest():
    res = run_script(VALID_MANIFEST, VALID_ENV)
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
    manifest["plan_sha256"] = "short"
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
    manifest["plan_blob_path"] = "play.txt"
    res = run_script(manifest, VALID_ENV)
    assert res.returncode == 1
    assert "Invalid plan_blob_path format" in res.stdout
