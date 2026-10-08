#!/usr/bin/env python3
import argparse
import sys

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def validate_key(pem_data: bytes):
    try:
        private_key = serialization.load_pem_private_key(pem_data, password=None, backend=default_backend())
    except Exception as e:
        print(f"FAILED: Unable to parse PEM data: {e}")
        return False

    if not isinstance(private_key, rsa.RSAPrivateKey):
        print(f"FAILED: Key is not an RSA private key (got {type(private_key)})")
        return False

    key_size = private_key.key_size
    if key_size != 4096:
        print(f"FAILED: JWTKEY RSA key size must be 4096 bits. Found: {key_size} bits.")
        return False

    print("SUCCESS: Key is a valid 4096-bit RSA private key.")
    print("NOTE: Ensure that the application is configured to use RS512 for signing assertions.")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate the production JWT/JWKS RSA signing key (must be 4096-bit RSA for RS512)."
    )
    parser.add_argument("key_file", help="Path to the PEM-encoded private key file to validate")
    args = parser.parse_args()

    try:
        with open(args.key_file, "rb") as f:
            pem_data = f.read()
    except Exception as e:
        print(f"Error reading file {args.key_file}: {e}")
        sys.exit(1)

    if validate_key(pem_data):
        sys.exit(0)
    else:
        sys.exit(1)
