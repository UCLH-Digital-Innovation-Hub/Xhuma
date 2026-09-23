import os
import subprocess
import tempfile


def test_terraform_error_filter():
    synthetic_error = """Error: Reference to undeclared input variable
  on main.tf line 5, in module "network":
   5:   secret_token = var.my_super_secret_token
A managed resource "azurerm_key_vault_secret" "db_password" has been declared with sensitive value "MySecurePassword123!"
More errors here.
client_id = "abc"
tenant_id = "xyz"
subscription_id = "123"
MyUnrecognizedErrorHappened
AloneSecret123!
Code="AuthorizationFailed"
Status=403
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(synthetic_error)
        error_file = f.name

    try:
        cmd = f'python scripts/deploy/filter_tf_error.py 1 "{error_file}"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        output = result.stdout

        # Check allowlisted things appear
        assert "Terraform Exit Code: 1" in output
        assert "Source Location: main.tf line 5" in output
        assert "Resource Address: azurerm_key_vault_secret.db_password" in output
        assert "Azure Error Code: AuthorizationFailed" in output
        assert "Azure Status: 403" in output

        # Check arbitrary errors and secrets DO NOT appear
        assert "MyUnrecognizedErrorHappened" not in output
        assert "AloneSecret123!" not in output
        assert "MySecurePassword123!" not in output
        assert "secret_token" not in output
        assert "client_id" not in output

        # Test unrecognized error case
        unrecognized_error = "Some arbitrary failure\nWith no known patterns."
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f2:
            f2.write(unrecognized_error)
            error_file2 = f2.name

        cmd2 = f'python scripts/deploy/filter_tf_error.py 2 "{error_file2}"'
        result2 = subprocess.run(cmd2, shell=True, capture_output=True, text=True)
        assert "Unrecognised Error: The error could not be safely classified" in result2.stdout
        assert "Some arbitrary failure" not in result2.stdout
        os.remove(error_file2)

    finally:
        os.remove(error_file)
