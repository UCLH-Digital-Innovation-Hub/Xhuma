# JWTKEY Environment Variable

The `JWTKEY` environment variable is critical for the Xhuma application's security and proper functioning. This document explains how it should be formatted and used in different environments.

## Format Requirements

The `JWTKEY` must be a valid PEM-formatted RSA private key. This means:

1. It must include the proper PEM headers and footers:
   ```
   -----BEGIN RSA PRIVATE KEY-----
   [base64-encoded key data]
   -----END RSA PRIVATE KEY-----
   ```
   OR
   ```
   -----BEGIN PRIVATE KEY-----
   [base64-encoded key data]
   -----END PRIVATE KEY-----
   ```

2. The key must include proper line breaks (every 64 characters in the base64-encoded data)
3. It must be a valid RSA private key (not EC or other formats)
4. The key should be unencrypted (no password protection)

## Common Issues

### Line Breaks in Environment Variables

When setting the `JWTKEY` in environment variables, especially in CI/CD pipelines, the line breaks must be preserved. There are several ways to handle this:

1. **In shell scripts or terminal**:
   ```bash
   export JWTKEY="-----BEGIN RSA PRIVATE KEY-----
   MIIEpAIBAAKCAQEA0Gjl7L0pV+...
   ...
   -----END RSA PRIVATE KEY-----"
   ```

2. **In GitHub Actions secrets**:
   Paste the entire key including line breaks. GitHub Actions will preserve the formatting.

3. **In Terraform**:
   Use heredoc syntax:
   ```hcl
   jwtkey = <<-EOT
   -----BEGIN RSA PRIVATE KEY-----
   MIIEpAIBAAKCAQEA0Gjl7L0pV+...
   ...
   -----END RSA PRIVATE KEY-----
   EOT
   ```

4. **In Docker Compose**:
   ```yaml
   environment:
     - JWTKEY=-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA0Gjl7L0pV+...\n...\n-----END RSA PRIVATE KEY-----
   ```
   Or using an external .env file.

### Troubleshooting Key Errors

If you see errors like:

```
Error loading RSA private key: Could not deserialize key data. The data may be in an incorrect format...
```

Check:
1. **Headers and footers**: Ensure the "BEGIN" and "END" lines are present and formatted correctly
2. **Line breaks**: Verify line breaks are properly included (every 64 characters)
3. **Encoding**: Make sure there's no extra encoding (e.g., double-encoding as base64)
4. **Key type**: Confirm you're using an RSA key, not EC or another type

## Key Generation for Development

For development purposes, you can generate a suitable RSA key with:

```bash
# Generate a 2048-bit RSA private key
openssl genrsa -out private_key.pem 2048

# To check the key is valid
openssl rsa -in private_key.pem -check
```

Then set it in your `.env` file:

```
JWTKEY=$(cat private_key.pem)
```

## Production Security Best Practices

1. **Never commit the private key** to version control
2. Use a secrets manager like Azure Key Vault or HashiCorp Vault
3. Rotate keys periodically
4. Set appropriate access controls to the environment variables
5. Monitor for key usage and unauthorized access attempts

## Azure Container Apps Integration

When deploying to Azure Container Apps, the `JWTKEY` is securely passed through:

1. **GitHub Secrets**: Stored as a GitHub Secret
2. **Terraform**: Passed via the TF_VAR_jwtkey variable
3. **Container Environment**: Injected as an environment variable by the deployment process

The application is designed to:
- Load the key lazily (only when needed)
- Validate the key format before attempting to use it
- Provide clear error messages if the key is missing or invalid
- Show key status in the `/ready` endpoint for diagnostics
