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
    if not plan_hash or len(str(plan_hash)) != 64 or not all(c in "0123456789abcdef" for c in str(plan_hash).lower()):
        print(f"::error::Invalid plan_sha256 format (must be 64 hex chars): {plan_hash}")
        sys.exit(1)

    digest = manifest.get("candidate_image_digest")
    if (
        not str(digest).startswith("sha256:")
        or len(str(digest)) != 71
        or not all(c in "0123456789abcdef" for c in str(digest)[7:].lower())
    ):
        print(f"::error::Invalid image digest format (must be sha256: followed by 64 hex chars): {digest}")
        sys.exit(1)

    plan_path = manifest.get("plan_blob_path")
    expected_plan_path = f"{expected['target_id']}-{expected['source_commit_sha']}-{expected['workflow_run_id']}.tfplan"
    if str(plan_path) != expected_plan_path:
        print(f"::error::Invalid plan_blob_path. Expected {expected_plan_path}, got {plan_path}")
        sys.exit(1)

    # Validate backend coordinates against the file
    backend_file = expected["backend_file"]
    if not os.path.exists(backend_file):
        print(f"::error::Backend file does not exist at {backend_file}")
        sys.exit(1)

    backend_coords = {}
    with open(backend_file, "r") as f:
        for line in f:
            if "=" in line:
                key, val = line.split("=", 1)
                backend_coords[key.strip()] = val.strip().strip('"')

    expected_backend_coords = {
        "backend_resource_group": backend_coords.get("resource_group_name"),
        "backend_storage_account": backend_coords.get("storage_account_name"),
        "backend_container": backend_coords.get("container_name"),
        "backend_key": backend_coords.get("key"),
    }

    for key, expected_val in expected_backend_coords.items():
        actual_val = manifest.get(key)
        if actual_val is None or not str(actual_val).strip():
            print(f"::error::Manifest backend field missing or empty: {key}")
            sys.exit(1)
        if str(actual_val) != str(expected_val):
            print(f"::error::Manifest mismatch for backend coordinate {key}. Expected {expected_val}, got {actual_val}")
            sys.exit(1)

    print("Manifest verified successfully.")


if __name__ == "__main__":
    main()
