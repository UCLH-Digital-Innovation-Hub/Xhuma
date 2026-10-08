import subprocess
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate_csr(out_dir: str, fqdn: str):
    out_path = Path(out_dir)
    key_path = out_path / f"{fqdn}.key"
    csr_path = out_path / f"{fqdn}.csr"

    # check if private key already exists
    if key_path.exists():
        print(f"⚠️ Private key already exists: {key_path}")
        return
    else:
        print(f"🔑 Generating private key at: {key_path}")
        # 1. Generate RSA private key
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with open(key_path, "wb") as f:
            f.write(
                private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )
        print(f"✅ Private key written: {key_path}")

    # 2. Create CSR with Common Name (CN) = FQDN and Country = GB and SAN = FQDN
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "GB"),
                    x509.NameAttribute(NameOID.COMMON_NAME, fqdn),
                ]
            )
        )
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(fqdn)]), critical=False)
        .sign(private_key, hashes.SHA256())
    )

    with open(csr_path, "wb") as f:
        f.write(csr.public_bytes(serialization.Encoding.PEM))

    print(f"📄 CSR written: {csr_path}")


def generate_pfx_from_cert_chain(fqdn: str, cert_dir: str):
    cert_dir = Path(cert_dir)

    # Input files
    endpoint_cert = cert_dir / "endpoint_certificate.crt"
    subca_cert = cert_dir / "nhs_sub.crt"
    rootca_cert = cert_dir / "nhs_root.crt"
    private_key = cert_dir / f"{fqdn}.key"

    # Intermediate/Output files
    chain_file = cert_dir / "xhuma_cert_chain.txt"
    pfx_file = cert_dir / "endpoint_chain.pfx"
    client_cert = cert_dir / "client_cert.pem"
    client_key = cert_dir / "client_key.pem"
    nhs_bundle = cert_dir / "nhs_bundle.pem"

    # Check all files exist
    for f in [endpoint_cert, subca_cert, rootca_cert, private_key]:
        if not f.exists():
            raise FileNotFoundError(f"Required file not found: {f}")

    print("✅ All required certificate files found.")

    # Combine certs into chain
    with open(chain_file, "w") as outfile:
        for cert in [endpoint_cert, subca_cert, rootca_cert]:
            with open(cert, "r") as infile:
                outfile.write(infile.read())
                outfile.write("\n")

    print(f"🔗 Combined certs into: {chain_file}")

    # Generate PFX file
    command = [
        "openssl",
        "pkcs12",
        "-export",
        "-inkey",
        str(private_key),
        "-in",
        str(chain_file),
        "-out",
        str(pfx_file),
        "-passout",
        "pass:",  # empty password for testing, change as needed
    ]

    print("🔐 Generating PFX file using OpenSSL...")
    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        print("❌ OpenSSL Error:")
        print(result.stderr)
        raise RuntimeError("Failed to generate .pfx file")

    print(f"✅ PFX file created: {pfx_file}")

    # Extract client cert (with chain)
    command = [
        "openssl",
        "pkcs12",
        "-in",
        str(pfx_file),
        "-clcerts",
        "-nokeys",
        "-out",
        str(client_cert),
        "-passin",
        "pass:",
    ]
    print("📤 Extracting client certificate with chain...")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print("❌ OpenSSL Error (cert extract):")
        print(result.stderr)
        raise RuntimeError("Failed to extract client cert")
    print(f"✅ Client cert PEM created: {client_cert}")

    # Extract private key
    command = [
        "openssl",
        "pkcs12",
        "-in",
        str(pfx_file),
        "-nocerts",
        "-nodes",
        "-out",
        str(client_key),
        "-passin",
        "pass:",
    ]
    print("🔑 Extracting private key...")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print("❌ OpenSSL Error (key extract):")
        print(result.stderr)
        raise RuntimeError("Failed to extract private key")
    print(f"✅ Client key PEM created: {client_key}")

    # Combine SubCA + RootCA into a bundle for httpx verify
    with open(nhs_bundle, "w") as out:
        for cert in [subca_cert, rootca_cert]:
            with open(cert, "r") as f:
                out.write(f.read())
                out.write("\n")
    print(f"🔐 NHS CA bundle created: {nhs_bundle}")

    print("\n🎉 All artifacts ready:")
    print(f"  🔐 PFX: {pfx_file}")
    print(f"  📄 client_cert.pem: {client_cert}")
    print(f"  🔑 client_key.pem: {client_key}")
    print(f"  🛡️  nhs_bundle.pem (use in httpx verify): {nhs_bundle}")


def validate_csr(csr_path: str):
    from cryptography.hazmat.backends import default_backend

    try:
        with open(csr_path, "rb") as f:
            csr_data = f.read()
        csr = x509.load_pem_x509_csr(csr_data, default_backend())
    except Exception as e:
        print(f"❌ Failed to load CSR: {e}")
        return False

    print("\n🔍 Validating CSR...")

    # Check signature validity
    if not csr.is_signature_valid:
        print("❌ Signature: INVALID")
        signature_valid = False
    else:
        print("✅ Signature: Valid")
        signature_valid = True

    # Check key size
    public_key = csr.public_key()
    if isinstance(public_key, rsa.RSAPublicKey):
        key_size = public_key.key_size
        if key_size == 2048:
            print(f"✅ Key Size: RSA {key_size}")
        else:
            print(f"❌ Key Size: RSA {key_size} (Expected 2048)")
    else:
        key_size = 0
        print("❌ Key Type: Not RSA")

    # Check subject CN and Country
    subject = csr.subject
    cn = subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    country = subject.get_attributes_for_oid(x509.NameOID.COUNTRY_NAME)

    cn_value = cn[0].value if cn else None
    c_value = country[0].value if country else None

    if cn_value == "GPC-Z6G1Z.uclhinnovationhub.nhs.uk":
        print(f"✅ Subject CN: {cn_value}")
    else:
        print(f"❌ Subject CN: {cn_value} (Expected GPC-Z6G1Z.uclhinnovationhub.nhs.uk)")

    if c_value == "GB":
        print(f"✅ Country: {c_value}")
    else:
        print(f"❌ Country: {c_value} (Expected GB)")

    # Check SAN DNS names
    try:
        san_ext = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        dns_names = san_ext.value.get_values_for_type(x509.DNSName)
        print(f"ℹ️  SAN DNS names: {', '.join(dns_names)}")
    except x509.ExtensionNotFound:
        print("ℹ️  SAN DNS names: None present")

    print("\nOverall Pass/Fail against NHSE Requirements:")
    if signature_valid and key_size == 2048 and cn_value == "GPC-Z6G1Z.uclhinnovationhub.nhs.uk" and c_value == "GB":
        print("✅ PASS")
        return True
    else:
        print("❌ FAIL")
        return False


def match_key(cert_path: str, key_path: str):

    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization

    try:
        with open(cert_path, "rb") as f:
            cert_data = f.read()
        cert = x509.load_pem_x509_certificate(cert_data, default_backend())

        with open(key_path, "rb") as f:
            key_data = f.read()
        private_key = serialization.load_pem_private_key(key_data, password=None, backend=default_backend())
    except Exception as e:
        print(f"❌ Failed to load certificate or key: {e}")
        return False

    print("\n🔍 Matching Certificate and Private Key...")

    # Certificate Subject
    print(f"Certificate Subject: {cert.subject.rfc4514_string()}")

    # SHA-256 Fingerprint
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    print(f"Certificate SHA-256 Fingerprint: {fingerprint}")

    # Match private key to certificate public key
    try:
        cert_pub_numbers = cert.public_key().public_numbers()
        priv_pub_numbers = private_key.public_key().public_numbers()

        if cert_pub_numbers == priv_pub_numbers:
            print("✅ PASS: Private key matches certificate.")
            return True
        else:
            print("❌ FAIL: Private key does NOT match certificate.")
            return False
    except AttributeError:
        print("❌ FAIL: Key types do not match or are unsupported.")
        return False


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Xhuma NHS Certificate Utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate-csr", help="Validate a CSR against NHSE production requirements")
    validate_parser.add_argument("csr_path", help="Path to the .csr file")

    match_parser = subparsers.add_parser("match-key", help="Match a certificate and private key")
    match_parser.add_argument("cert_path", help="Path to the .pem certificate")
    match_parser.add_argument("key_path", help="Path to the .pem private key")

    generate_parser = subparsers.add_parser("generate-csr", help="Generate a new RSA 2048 private key and CSR")
    generate_parser.add_argument("--out-dir", required=True, help="Output directory")
    generate_parser.add_argument("--fqdn", required=True, help="Fully Qualified Domain Name for the CN")

    pfx_parser = subparsers.add_parser("generate-pfx", help="Generate PFX and PEM assets from certificates")
    pfx_parser.add_argument("--fqdn", required=True, help="FQDN used for the private key filename")
    pfx_parser.add_argument("--cert-dir", required=True, help="Directory containing the certificates and private key")

    args = parser.parse_args()

    if args.command == "validate-csr":
        success = validate_csr(args.csr_path)
        sys.exit(0 if success else 1)
    elif args.command == "match-key":
        success = match_key(args.cert_path, args.key_path)
        sys.exit(0 if success else 1)
    elif args.command == "generate-csr":
        generate_csr(args.out_dir, args.fqdn)
    elif args.command == "generate-pfx":
        generate_pfx_from_cert_chain(args.fqdn, args.cert_dir)
