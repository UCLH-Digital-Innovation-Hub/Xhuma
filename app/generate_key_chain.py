import os
import subprocess
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pathlib import Path

def generate_csr(out_dir: str = ".",fqdn: str = "gpc-int-RRV00.xhuma.thirdparty.nhs.uk"):
    out_path = Path(out_dir)
    key_path = out_path / f"{fqdn}.key"
    csr_path = out_path / f"{fqdn}.csr"

    # 1. Generate RSA private key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with open(key_path, "wb") as f:
        f.write(private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()
        ))
    print(f"✅ Private key written: {key_path}")

    # 2. Create CSR with Common Name (CN) = FQDN and Country = GB
    csr = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "GB"),
        x509.NameAttribute(NameOID.COMMON_NAME, fqdn)
    ])).sign(private_key, hashes.SHA256())

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
        "openssl", "pkcs12",
        "-export",
        "-inkey", str(private_key),
        "-in", str(chain_file),
        "-out", str(pfx_file),
        "-passout", "pass:"  # empty password for testing, change as needed
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
        "openssl", "pkcs12",
        "-in", str(pfx_file),
        "-clcerts",
        "-nokeys",
        "-out", str(client_cert),
        "-passin", "pass:"
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
        "openssl", "pkcs12",
        "-in", str(pfx_file),
        "-nocerts",
        "-nodes",
        "-out", str(client_key),
        "-passin", "pass:"
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

# Example usage
if __name__ == "__main__":
    generate_pfx_from_cert_chain("gpc-int-RRV00.xhuma.thirdparty.nhs.uk", "keys/nhs_certs")