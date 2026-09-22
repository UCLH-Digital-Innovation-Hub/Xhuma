import json
import os
import subprocess
import tempfile

SCRIPT_PATH = "scripts/deploy/validate_manifest.py"


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
    manifest = {
        "repository": "test/repo",
        "source_commit_sha": "abc1234",
        "workflow_run_id": "999",
        "target_id": "play",
        "azure_tenant_id": "tenant-abc",
        "azure_subscription_id": "sub-123",
        "backend_file": "infra/backends/play.hcl",
        "candidate_image_digest": "sha256:abcd",
    }

    env = {
        "EXPECTED_REPO": "test/repo",
        "EXPECTED_SHA": "abc1234",
        "EXPECTED_RUN_ID": "999",
        "EXPECTED_TARGET": "play",
        "EXPECTED_TENANT": "tenant-abc",
        "EXPECTED_SUB": "sub-123",
        "EXPECTED_BACKEND": "infra/backends/play.hcl",
        "EXPECTED_DIGEST": "sha256:abcd",
    }

    res = run_script(manifest, env)
    assert res.returncode == 0
    assert "Manifest verified successfully" in res.stdout


def test_invalid_manifest():
    manifest = {
        "repository": "test/repo",
        "source_commit_sha": "abc1234",
        "workflow_run_id": "999",
        "target_id": "play",
        "azure_tenant_id": "tenant-abc",
        "azure_subscription_id": "sub-123",
        "backend_file": "infra/backends/play.hcl",
        "candidate_image_digest": "sha256:abcd",
    }

    env = {
        "EXPECTED_REPO": "test/repo",
        "EXPECTED_SHA": "abc1234",
        "EXPECTED_RUN_ID": "999",
        "EXPECTED_TARGET": "play",
        "EXPECTED_TENANT": "tenant-abc",
        "EXPECTED_SUB": "sub-123",
        "EXPECTED_BACKEND": "infra/backends/play.hcl",
        "EXPECTED_DIGEST": "sha256:WRONG",
    }

    res = run_script(manifest, env)
    assert res.returncode == 1
    assert "Manifest mismatch for candidate_image_digest" in res.stdout
