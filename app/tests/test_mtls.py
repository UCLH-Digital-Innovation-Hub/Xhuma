from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.mtls import MTLSMiddleware

app = FastAPI()
app.add_middleware(MTLSMiddleware)


@app.get("/")
def root():
    return {"message": "Hello World"}


@app.get("/secure")
def secure():
    return {"message": "Secure Data"}


client = TestClient(app)


def test_mtls_disabled_by_default(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    response = client.get("/secure")
    assert response.status_code == 200


def test_mtls_enabled_no_header(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "true")
    response = client.get("/secure")
    assert response.status_code == 403
    assert response.json() == {"detail": "Client Certificate Required"}


def test_mtls_enabled_with_header(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "true")
    monkeypatch.setattr("app.middleware.mtls._verify_epic_cert", lambda x: True)
    response = client.get("/secure", headers={"X-ARR-ClientCert": "MIID..."})
    assert response.status_code == 200


def test_mtls_public_paths(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "true")
    # / is in public_paths
    response = client.get("/")
    assert response.status_code == 200


def test_mtls_accepts_valid_thumbprint(monkeypatch):
    import base64
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
        .not_valid_after(dt.datetime.now(dt.UTC) + dt.timedelta(days=30))
        .sign(private_key=key, algorithm=hashes.SHA256())
    )
    der_b64 = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode("ascii")
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()

    # The actual implementation calls `_verify_epic_cert` internally, so let's import it
    from app.middleware.mtls import _verify_epic_cert

    # Mock EPIC_CA_CERT so it doesn't fail on CA load
    monkeypatch.setenv("EPIC_CA_CERT", cert.public_bytes(serialization.Encoding.PEM).decode("utf-8"))

    # Set the allowlist to exactly our fingerprint
    monkeypatch.setenv("MTLS_TRUSTED_THUMBPRINTS", fingerprint)

    assert _verify_epic_cert(der_b64) is True


def test_mtls_rejects_invalid_thumbprint(monkeypatch):
    import base64
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
        .not_valid_after(dt.datetime.now(dt.UTC) + dt.timedelta(days=30))
        .sign(private_key=key, algorithm=hashes.SHA256())
    )
    der_b64 = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode("ascii")

    from app.middleware.mtls import _verify_epic_cert

    monkeypatch.setenv("EPIC_CA_CERT", cert.public_bytes(serialization.Encoding.PEM).decode("utf-8"))
    monkeypatch.setenv("MTLS_TRUSTED_THUMBPRINTS", "1234567890abcdef")  # Invalid thumbprint

    assert _verify_epic_cert(der_b64) is False


def test_mtls_lenient_thumbprint_formatting(monkeypatch):
    import base64
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
        .not_valid_after(dt.datetime.now(dt.UTC) + dt.timedelta(days=30))
        .sign(private_key=key, algorithm=hashes.SHA256())
    )
    der_b64 = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode("ascii")

    # Original fingerprint is pure hex lowercase (e.g. b12f5a...)
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()

    # Format it with colons and uppercase: B1:2F:5A...
    formatted_fingerprint = ":".join(fingerprint[i : i + 2] for i in range(0, len(fingerprint), 2)).upper()

    from app.middleware.mtls import _verify_epic_cert

    monkeypatch.setenv("EPIC_CA_CERT", cert.public_bytes(serialization.Encoding.PEM).decode("utf-8"))
    monkeypatch.setenv(
        "MTLS_TRUSTED_THUMBPRINTS",
        f"other_thumbprint, {formatted_fingerprint} , another_one",
    )

    # Should still match
    assert _verify_epic_cert(der_b64) is True
