import os
import base64
import datetime
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding, rsa, ec
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


def _verify_epic_cert(client_cert_b64: str) -> bool:
    try:
        der_cert = base64.b64decode(client_cert_b64)
        client_cert = x509.load_der_x509_certificate(der_cert, default_backend())
    except Exception as e:
        print(
            f"Epic CA Verification: Failed to decode client cert. Error: {str(e)}",
            flush=True,
        )
        return False

    epic_ca_pem = os.getenv("EPIC_CA_CERT")
    if not epic_ca_pem:
        print(
            "Epic CA Verification: EPIC_CA_CERT env var is missing or empty", flush=True
        )
        # Secure default: If we enforce mTLS but have no CA configured, reject.
        return False

    try:
        from app.security import fix_pem_formatting

        epic_ca_str = fix_pem_formatting(epic_ca_pem).encode("utf-8")
        try:
            ca_certs = x509.load_pem_x509_certificates(epic_ca_str, default_backend())
        except AttributeError:
            # Fallback for older cryptography versions
            ca_certs = [x509.load_pem_x509_certificate(epic_ca_str, default_backend())]
    except Exception:
        # Fallback in case they pasted a base64 DER string instead of PEM
        try:
            der_ca = base64.b64decode(epic_ca_pem)
            ca_certs = [x509.load_der_x509_certificate(der_ca, default_backend())]
        except Exception as e:
            print(
                f"Epic CA Verification: Failed to parse EPIC_CA_CERT. Error: {str(e)}",
                flush=True,
            )
            return False

    if not ca_certs:
        print(
            "Epic CA Verification: No valid CA certificates found in EPIC_CA_CERT",
            flush=True,
        )
        return False

    # Check expiry
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        not_before = client_cert.not_valid_before_utc
        not_after = client_cert.not_valid_after_utc
    except AttributeError:
        # Fallback for older cryptography versions
        not_before = client_cert.not_valid_before.replace(tzinfo=datetime.timezone.utc)
        not_after = client_cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)

    if now < not_before or now > not_after:
        print(
            "Epic CA Verification: Client certificate is expired or not yet valid",
            flush=True,
        )
        return False

    # Verify cryptographic signature against any of the CAs in the bundle
    for ca_cert in ca_certs:
        public_key = ca_cert.public_key()
        try:
            if isinstance(public_key, rsa.RSAPublicKey):
                public_key.verify(
                    client_cert.signature,
                    client_cert.tbs_certificate_bytes,
                    padding.PKCS1v15(),
                    client_cert.signature_hash_algorithm,
                )
            elif isinstance(public_key, ec.EllipticCurvePublicKey):
                public_key.verify(
                    client_cert.signature,
                    client_cert.tbs_certificate_bytes,
                    ec.ECDSA(client_cert.signature_hash_algorithm),
                )
            else:
                continue
            return True  # Successfully verified by this CA!
        except Exception:
            continue

    print(
        "Epic CA Verification: Client cert signature did not match any provided CA",
        flush=True,
    )
    return False


class MTLSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        require_mtls = os.getenv("REQUIRE_MTLS", "false").lower() == "true"

        # Public paths that don't need mTLS
        public_paths = [
            "/docs",
            "/openapi.json",
            "/jwk",
            "/health",
            "/_dev/audit",
            "/favicon.ico",
            "/robots",
        ]

        is_public = (request.url.path == "/") or any(
            request.url.path.startswith(p) for p in public_paths
        )

        if not require_mtls or is_public:
            return await call_next(request)

        # Bypass all global mTLS checks for Relay connections.
        # Relay handles its own certificate presence and validation entirely in routes.py
        if request.url.path.startswith("/relay"):
            return await call_next(request)

        # Temporary troubleshooting: Allow anonymous GET requests to /SOAP for WSDL probing
        if request.method == "GET" and request.url.path.startswith("/SOAP"):
            print(
                f"MTLS Middleware: Troubleshooting - Allowing anonymous GET request to {request.url.path} (Query: {request.url.query})",
                flush=True,
            )
            return await call_next(request)

        client_cert = request.headers.get("X-ARR-ClientCert")
        if not client_cert:
            print(
                f"MTLS Middleware: Blocked request to {request.url.path} because X-ARR-ClientCert header is missing",
                flush=True,
            )
            return JSONResponse(
                status_code=403, content={"detail": "Client Certificate Required"}
            )

        # Validate Epic CA for all other protected endpoints (e.g., /soap, /pds)
        if not _verify_epic_cert(client_cert):
            print(
                f"MTLS Middleware: Blocked request to {request.url.path} because Epic CA Verification Failed",
                flush=True,
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Invalid Client Certificate. Epic CA Verification Failed."
                },
            )

        return await call_next(request)
