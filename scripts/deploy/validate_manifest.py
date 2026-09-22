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

    for key, expected_val in expected.items():
        actual_val = manifest.get(key)
        if str(actual_val) != str(expected_val):
            print(f"::error::Manifest mismatch for {key}. Expected {expected_val}, got {actual_val}")
            sys.exit(1)

    print("Manifest verified successfully.")


if __name__ == "__main__":
    main()
