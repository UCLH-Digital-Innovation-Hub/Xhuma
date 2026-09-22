import json
import os
import sys


def main():
    if len(sys.argv) != 2:
        print("Usage: validate_manifest.py <manifest.json>")
        sys.exit(1)

    manifest_path = sys.argv[1]
    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    expected = {
        "repository": os.environ.get("EXPECTED_REPO"),
        "source_commit_sha": os.environ.get("EXPECTED_SHA"),
        "workflow_run_id": os.environ.get("EXPECTED_RUN_ID"),
        "target_id": os.environ.get("EXPECTED_TARGET"),
        "azure_tenant_id": os.environ.get("EXPECTED_TENANT"),
        "azure_subscription_id": os.environ.get("EXPECTED_SUB"),
        "backend_file": os.environ.get("EXPECTED_BACKEND"),
        "candidate_image_digest": os.environ.get("EXPECTED_DIGEST"),
    }

    # Verify all expected values are provided in the environment
    for key, val in expected.items():
        if not val or not str(val).strip():
            print(f"::error::Missing expected environment value for {key}. Operator prerequisite missing?")
            sys.exit(1)

    for key, expected_val in expected.items():
        actual_val = manifest.get(key)
        if actual_val is None or not str(actual_val).strip():
            print(f"::error::Manifest field missing or empty: {key}")
            sys.exit(1)
        if str(actual_val) != str(expected_val):
            print(f"::error::Manifest mismatch for {key}. Expected {expected_val}, got {actual_val}")
            sys.exit(1)

    # Validate specific formats
    plan_hash = manifest.get("plan_sha256")
    if not plan_hash or len(str(plan_hash)) != 64 or not str(plan_hash).isalnum():
        print(f"::error::Invalid plan_sha256 format: {plan_hash}")
        sys.exit(1)

    digest = manifest.get("candidate_image_digest")
    if not str(digest).startswith("sha256:"):
        print(f"::error::Invalid image digest format: {digest}")
        sys.exit(1)

    plan_path = manifest.get("plan_blob_path")
    if not plan_path or not plan_path.endswith(".tfplan"):
        print(f"::error::Invalid plan_blob_path format: {plan_path}")
        sys.exit(1)

    print("Manifest verified successfully.")


if __name__ == "__main__":
    main()
