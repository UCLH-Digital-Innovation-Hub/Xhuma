#!/usr/bin/env python3
"""Prepare NHS mTLS files from an issued certificate and its existing CSR key."""

import argparse
import hashlib
import json
import os
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.request import urlopen

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

NHS_DOWNLOADS = (
    "https://digital.nhs.uk/services/spine/updating-nhs-public-key-infrastructure-certificates/"
    "g2-certificate-technical-implementation#certificate-downloads"
)
AUTHORITY_FILES = {
    "production": (
        (
            "NHS Authentication G2",
            "https://files.digital.nhs.uk/assets/Services/Spine/Base-64%20encoded%20X.509%20certificates/"
            "NHSAuthenticationG2.crt",
        ),
        (
            "NHS Root Authority G2",
            "https://files.digital.nhs.uk/assets/Services/Spine/Base-64%20encoded%20X.509%20certificates/"
            "NHSRootAuthorityG2.crt",
        ),
    ),
    "int": (
        (
            "NHS INT Authentication G2",
            "https://files.digital.nhs.uk/assets/Services/Spine/Path%20to%20live%20certificates/NHSINTAuthG2.crt",
        ),
        (
            "NHS PTL Root Authority G2",
            "https://files.digital.nhs.uk/assets/Services/Spine/Path%20to%20live%20certificates/NHSPTLRootAuthorityG2.crt",
        ),
    ),
}
CERTIFICATE_SUFFIXES = (".crt", ".cer", ".pem", ".txt", ".p7b", ".p7c")
OUTPUT_NAMES = ("client_key.pem", "client_cert.pem", "nhs_bundle.pem", "thumbprints.json")
# The requested container artifacts must be accessible to every container user.
OUTPUT_MODE = 0o777


def parse_certificates(data: bytes) -> list[x509.Certificate]:
    """Accept X.509 and PKCS#7 certificates in PEM or DER, regardless of suffix."""
    try:
        if b"-----BEGIN CERTIFICATE-----" in data:
            certificates = x509.load_pem_x509_certificates(data)
        elif b"-----BEGIN PKCS7-----" in data:
            certificates = pkcs7.load_pem_pkcs7_certificates(data)
        else:
            try:
                certificates = [x509.load_der_x509_certificate(data)]
            except ValueError:
                certificates = pkcs7.load_der_pkcs7_certificates(data)
    except ValueError as exc:
        raise ValueError("Expected an X.509 certificate or PKCS#7 certificate bundle in PEM or DER format") from exc
    if not certificates:
        raise ValueError("The supplied file contains no certificates")
    return certificates


def load_certificates(path: Path) -> list[x509.Certificate]:
    return parse_certificates(path.read_bytes())


def public_key_bytes(key) -> bytes:
    return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def find_certificate(key_path: Path, certificate_path: Path | None) -> Path:
    if certificate_path is None:
        candidates = [
            path
            for path in key_path.parent.iterdir()
            if path.is_file()
            and path.stem == key_path.stem
            and path.suffix.lower() in CERTIFICATE_SUFFIXES
            and path.resolve() != key_path.resolve()
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"Found {len(candidates)} matching certificate files next to {key_path}; "
                "supply the certificate path with --cert"
            )
        certificate_path = candidates[0]
    certificate_path = Path(certificate_path)
    if certificate_path.stem != key_path.stem:
        raise ValueError("The supplied key and certificate must have the same basename")
    if certificate_path.resolve() == key_path.resolve():
        raise ValueError("The certificate and private key must be separate files")
    return certificate_path


def download_authorities(environment: str) -> tuple[x509.Certificate, x509.Certificate]:
    """Fetch the currently published NHS G2 authentication and root certificates."""
    if environment not in AUTHORITY_FILES:
        raise ValueError(f"Unsupported NHS environment: {environment}")
    certificates = []
    for expected_name, url in AUTHORITY_FILES[environment]:
        with urlopen(url, timeout=30) as response:
            data = response.read(1_048_577)
        if len(data) > 1_048_576:
            raise ValueError(f"NHS CA download is unexpectedly large: {expected_name}")
        downloaded = parse_certificates(data)
        if len(downloaded) != 1:
            raise ValueError(f"Expected one NHS CA certificate: {expected_name}")
        certificate = downloaded[0]
        names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if len(names) != 1 or names[0].value != expected_name:
            raise ValueError(f"NHS CA download has an unexpected identity: {expected_name}")
        certificates.append(certificate)
    return certificates[0], certificates[1]


def validate_chain(leaf: x509.Certificate, intermediate: x509.Certificate, root: x509.Certificate) -> None:
    now = datetime.now(timezone.utc)
    for certificate in (leaf, intermediate, root):
        if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
            raise ValueError(f"Certificate expired or not yet valid: {certificate.subject.rfc4514_string()}")
    for certificate, expected_ca in ((leaf, False), (intermediate, True), (root, True)):
        try:
            constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
        except x509.ExtensionNotFound as exc:
            raise ValueError("Certificate is missing basic constraints") from exc
        if constraints.ca != expected_ca:
            raise ValueError("The certificate chain has an unexpected CA or client certificate")
    try:
        usage = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        usage = None
    if usage is not None and not any(
        oid in usage for oid in (ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.ANY_EXTENDED_KEY_USAGE)
    ):
        raise ValueError("The supplied certificate does not permit TLS client authentication")
    try:
        leaf.verify_directly_issued_by(intermediate)
        intermediate.verify_directly_issued_by(root)
        root.verify_directly_issued_by(root)
    except (ValueError, InvalidSignature) as exc:
        raise ValueError("The certificate does not chain to the selected NHS authorities") from exc

    # OpenSSL also checks client purpose, key usage and CA path constraints.
    with TemporaryDirectory(prefix="nhs-chain-check-") as directory:
        paths = [Path(directory) / name for name in ("client_cert.pem", "intermediate.pem", "root.pem")]
        for path, certificate in zip(paths, (leaf, intermediate, root), strict=True):
            path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        result = subprocess.run(
            [
                "openssl",
                "verify",
                "-purpose",
                "sslclient",
                "-CAfile",
                str(paths[2]),
                "-untrusted",
                str(paths[1]),
                str(paths[0]),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise ValueError(f"NHS client certificate chain verification failed: {result.stderr.strip()}")


def generate_client_files(
    key_path: Path,
    certificate_path: Path | None = None,
    *,
    output_root: Path | None = None,
    environment: str = "production",
    overwrite: bool = False,
) -> Path:
    key_path = Path(key_path)
    certificate_path = find_certificate(key_path, certificate_path)
    destination = Path(output_root or key_path.parent) / key_path.stem
    if destination.is_symlink():
        raise ValueError("The output folder must not be a symbolic link")
    sources = {key_path.resolve(), certificate_path.resolve()}
    if any((destination / name).resolve() in sources for name in OUTPUT_NAMES):
        raise ValueError("Output files would replace the supplied key or certificate; choose another --output-root")
    existing = [destination / name for name in OUTPUT_NAMES if (destination / name).exists()]
    if existing and not overwrite:
        raise FileExistsError(f"Output files already exist in {destination}; use --overwrite to replace them")

    key_data = key_path.read_bytes()
    try:
        if b"-----BEGIN" in key_data:
            key = serialization.load_pem_private_key(key_data, password=None)
        else:
            key = serialization.load_der_private_key(key_data, password=None)
    except (ValueError, TypeError) as exc:
        raise ValueError("Supply an unencrypted PEM or DER private key from the original CSR") from exc
    matching = [
        certificate
        for certificate in load_certificates(certificate_path)
        if public_key_bytes(certificate.public_key()) == public_key_bytes(key.public_key())
    ]
    if len(matching) != 1:
        raise ValueError("Expected exactly one client certificate matching the supplied private key")
    leaf = matching[0]
    intermediate, root = download_authorities(environment)
    validate_chain(leaf, intermediate, root)

    pem = serialization.Encoding.PEM
    files = {
        "client_key.pem": (
            key_data
            if b"-----BEGIN" in key_data
            else key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        ),
        "client_cert.pem": leaf.public_bytes(pem) + intermediate.public_bytes(pem),
        "nhs_bundle.pem": intermediate.public_bytes(pem) + root.public_bytes(pem),
    }
    # Confirm the exported files load together before touching the destination.
    with TemporaryDirectory(prefix="nhs-client-check-") as directory:
        for name, data in files.items():
            (Path(directory) / name).write_bytes(data)
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(Path(directory) / "client_cert.pem"), str(Path(directory) / "client_key.pem"))
        context.load_verify_locations(str(Path(directory) / "nhs_bundle.pem"))

    report = {
        "environment": environment,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "certificate_source": str(certificate_path.resolve()),
        "private_key_source": str(key_path.resolve()),
        "authority_source": NHS_DOWNLOADS,
        "certificate_matches_private_key": True,
        "chain_verified_for_client_authentication": True,
        "certificates": [
            {
                "name": name,
                "subject": certificate.subject.rfc4514_string(),
                "issuer": certificate.issuer.rfc4514_string(),
                "not_before_utc": certificate.not_valid_before_utc.isoformat(),
                "not_after_utc": certificate.not_valid_after_utc.isoformat(),
                "thumbprint_sha256": certificate.fingerprint(hashes.SHA256()).hex().upper(),
                "thumbprint_sha1": certificate.fingerprint(hashes.SHA1()).hex().upper(),
            }
            for name, certificate in (("client", leaf), ("authentication", intermediate), ("root", root))
        ],
        "public_key_fingerprint_sha256": hashlib.sha256(public_key_bytes(key.public_key())).hexdigest().upper(),
        "public_file_checksums_sha256": {
            name: hashlib.sha256(data).hexdigest().upper() for name, data in files.items() if name != "client_key.pem"
        },
        "output_permissions": "0777",
        "verification_scope": "Local chain and key checks; no revocation check or live NHS request performed.",
    }
    files["thumbprints.json"] = (json.dumps(report, indent=2) + "\n").encode()
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(OUTPUT_MODE)
    temporary_paths = []
    try:
        for name, data in files.items():
            with NamedTemporaryFile(dir=destination, prefix=f".{name}.", delete=False) as stream:
                path = Path(stream.name)
                temporary_paths.append(path)
                stream.write(data)
            path.chmod(OUTPUT_MODE)
        for path, name in zip(temporary_paths, files, strict=True):
            os.replace(path, destination / name)
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key", type=Path, help="Existing private key used to generate the CSR")
    parser.add_argument("--cert", type=Path, help="Same-basename issued certificate; autodetected next to the key")
    parser.add_argument(
        "--output-root", type=Path, help="Parent of the key-named output folder; defaults to key folder"
    )
    parser.add_argument("--environment", choices=AUTHORITY_FILES, default="production")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing generated output files")
    args = parser.parse_args(argv)
    try:
        destination = generate_client_files(
            args.key, args.cert, output_root=args.output_root, environment=args.environment, overwrite=args.overwrite
        )
    except (OSError, ValueError, ssl.SSLError) as exc:
        print(f"Unable to prepare NHS client files: {exc}", file=sys.stderr)
        return 1
    print(f"Verified NHS {args.environment} client files written to {destination.resolve()}")
    for name in OUTPUT_NAMES:
        print(f"  {destination / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
