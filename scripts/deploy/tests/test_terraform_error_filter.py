import json
import os
import subprocess
import tempfile


def test_terraform_error_filter():
    # Mixed fixture containing normal planned resources, a warning and a shared-provider error
    lines = [
        {"@level": "info", "@message": "Terraform 1.5.7", "type": "version"},
        {
            "@level": "info",
            "@message": "azurerm_resource_group.rg: Plan to create",
            "type": "resource_drift",
            "change": {"resource": {"addr": "azurerm_resource_group.rg"}},
        },
        {
            "@level": "warn",
            "@message": "Warning: Deprecated feature",
            "type": "diagnostic",
            "diagnostic": {
                "severity": "warning",
                "summary": "Deprecated feature",
                "address": "azurerm_key_vault.local_kv",
            },
        },
        {
            "@level": "error",
            "@message": "Error: authorization failed",
            "type": "diagnostic",
            "diagnostic": {
                "severity": "error",
                "summary": "AuthorizationFailed",
                "detail": 'Code="AuthorizationFailed" Status=403',
                "address": "azurerm_key_vault_access_policy.app_shared_policy",
                "range": {"filename": "main.tf", "start": {"line": 335}},
            },
        },
        # Simulate a crash/fallback text just to ensure it's ignored if it has no labels
        "Some random text that shouldn't match anything",
        'Code="SubscriptionNotFound" Status=404',
    ]

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        for line in lines:
            if isinstance(line, dict):
                f.write(json.dumps(line) + "\n")
            else:
                f.write(line + "\n")
        error_file = f.name

    try:
        cmd = f'python scripts/deploy/filter_tf_error.py 1 "{error_file}"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        output = result.stdout

        # Must show exit code
        assert "Terraform Exit Code: 1" in output

        # Must show the error source and resource, mapped label
        assert "Resource Address: azurerm_key_vault_access_policy.app_shared_policy" in output
        assert "Source Location: main.tf line 335" in output
        assert "Error Type: Authorisation Error" in output

        # Must show the fallback label matching the SubscriptionNotFound string
        assert "Error Type: Azure Error Code: SubscriptionNotFound" in output
        assert "Error Type: Azure Status: 404" in output

        # Must NOT show the planned resources that are not errors
        assert "azurerm_resource_group.rg" not in output

        # Must NOT show the warning
        assert "azurerm_key_vault.local_kv" not in output
        assert "warning" not in output.lower()

        # Must NOT show raw arbitrary text
        assert "Some random text" not in output
        assert "Deprecated feature" not in output

        # Test unrecognized error correctly identified
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f2:
            f2.write(
                json.dumps(
                    {
                        "@level": "error",
                        "type": "diagnostic",
                        "diagnostic": {"severity": "error", "summary": "Something weird happened."},
                    }
                )
                + "\n"
            )
            error_file2 = f2.name

        cmd2 = f'python scripts/deploy/filter_tf_error.py 2 "{error_file2}"'
        result2 = subprocess.run(cmd2, shell=True, capture_output=True, text=True)

        assert (
            "Unrecognised Error: The error could not be safely classified. No persistence mechanism is currently implemented for raw diagnostics."
            in result2.stdout
        )
        assert "Something weird happened" not in result2.stdout
        os.remove(error_file2)

    finally:
        os.remove(error_file)
