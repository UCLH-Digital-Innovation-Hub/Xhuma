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
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(synthetic_error)
        error_file = f.name

    try:
        cmd = f'grep -E "Error:" -A 10 -B 2 "{error_file}" | grep -E -iv "password|secret|key|token|client|tenant|subscription" || tail -n 20 "{error_file}" | grep -E -iv "password|secret|key|token|client|tenant|subscription" || true'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        output = result.stdout
        assert "Error: Reference to undeclared input variable" in output
        assert "More errors here." in output

        # Check sensitive values are absent
        assert "MySecurePassword123!" not in output
        assert "secret_token" not in output
        assert "client_id" not in output
        assert "tenant_id" not in output
        assert "subscription_id" not in output
    finally:
        os.remove(error_file)
