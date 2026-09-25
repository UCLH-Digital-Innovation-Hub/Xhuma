import json
import subprocess


def test_jq_kv_refs_validation():
    jq_script = """
    .value[]
    | select((.properties.vaultName // "" | ascii_downcase) == ($shared | ascii_downcase))
    | select(.properties.status != "Resolved")
    | .name
    """

    mock_api_response = {
        "value": [
            {"name": "SETTING_OK_SHARED", "properties": {"vaultName": "xhuma-shared-kv-int", "status": "Resolved"}},
            {
                "name": "SETTING_FAIL_SHARED",
                "properties": {"vaultName": "xhuma-shared-kv-int", "status": "SecretNotFound"},
            },
            {
                "name": "SETTING_FAIL_SHARED_CASE",
                "properties": {"vaultName": "Xhuma-SHARED-kv-INT", "status": "AccessDenied"},
            },
            {"name": "EPIC_CA_CERT", "properties": {"vaultName": "xhuma-play-kv", "status": "SecretNotFound"}},
            {"name": "NON_KV_SETTING", "properties": {"value": "some-value"}},
        ]
    }

    shared_kv = "xhuma-shared-kv-int"

    process = subprocess.run(
        ["jq", "-r", "--arg", "shared", shared_kv, jq_script],
        input=json.dumps(mock_api_response),
        capture_output=True,
        text=True,
        check=True,
    )

    failed_refs = [line.strip() for line in process.stdout.strip().split("\n") if line.strip()]

    # We expect only SETTING_FAIL_SHARED and SETTING_FAIL_SHARED_CASE.
    # EPIC_CA_CERT fails but points to xhuma-play-kv, so it should be ignored.
    # SETTING_OK_SHARED points to shared kv but is Resolved, so it should be ignored.
    # NON_KV_SETTING doesn't have vaultName, ignored.
    assert len(failed_refs) == 2
    assert "SETTING_FAIL_SHARED" in failed_refs
    assert "SETTING_FAIL_SHARED_CASE" in failed_refs
