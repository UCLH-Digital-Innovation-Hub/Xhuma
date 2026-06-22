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
    except Exception:
        return False

    epic_ca_pem = os.getenv("EPIC_CA_CERT")
    if not epic_ca_pem:
        # Secure default: If we enforce mTLS but have no CA configured, reject.
        return False

    try:
        from app.security import fix_pem_formatting

        epic_ca_str = fix_pem_formatting(epic_ca_pem).encode("utf-8")
        ca_cert = x509.load_pem_x509_certificate(epic_ca_str, default_backend())
    except Exception:
        # Fallback in case they pasted a base64 DER string instead of PEM
        try:
            der_ca = base64.b64decode(epic_ca_pem)
            ca_cert = x509.load_der_x509_certificate(der_ca, default_backend())
        except Exception:
            print("Warning: Failed to parse EPIC_CA_CERT")
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
        return False

    # Verify cryptographic signature
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
            return False
        return True
    except Exception:
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

        client_cert = request.headers.get("X-ARR-ClientCert")
        if not client_cert:
            return JSONResponse(
                status_code=403, content={"detail": "Client Certificate Required"}
            )

        # Bypass Epic validation for Relay connections (Relay handles its own validation in routes.py)
        if request.url.path.startswith("/relay"):
            return await call_next(request)

        # Validate Epic CA for all other protected endpoints (e.g., /soap, /pds)
        if not _verify_epic_cert(client_cert):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Invalid Client Certificate. Epic CA Verification Failed."
                },
            )

        return await call_next(request)
