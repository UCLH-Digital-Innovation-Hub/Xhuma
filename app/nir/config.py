"""Load and validate NIR settings independently of GP Connect and its relay.

All deployment settings use the NIR_ prefix. This lets the same application call
NIR's sandbox while GP Connect uses INT, without sharing credentials or TLS setup.
"""

import math
import os
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

# Each NIR environment has its own host. The client adds transaction paths to
# this base URL, so an override can also point the MVP at a local test server.
NIR_BASE_URLS = {
    "sandbox": "https://sandbox.nir.national.nhs.uk",
    "int": "https://int.nir.national.nhs.uk",
    "production": "https://nir.national.nhs.uk",
}


@dataclass(frozen=True)
class NIRConfig:
    """Keep one validated set of connection settings for a client's lifetime.

    Construction accepts explicit settings for tests or other callers. Environment
    variables are read by from_env(), rather than when this module is imported.
    """

    environment: str = "sandbox"
    base_url: str = ""
    timeout: float = 30.0
    tls_cert_path: str | None = None
    tls_key_path: str | None = None
    ca_bundle_path: str | None = None

    def __post_init__(self):
        """Validate explicit settings as well as values supplied by from_env()."""

        # Resolve the host from this configuration's environment. Using a blank
        # base URL here keeps NIRConfig(environment="int", ...) on the INT host.
        if self.environment not in NIR_BASE_URLS:
            raise ValueError("NIR_ENV must be sandbox, dev, int or production")
        if not self.base_url:
            object.__setattr__(self, "base_url", NIR_BASE_URLS[self.environment])

        # Keep credentials out of the URL and reject query/fragment suffixes;
        # transaction paths are added separately by the client. HTTP is useful
        # for a local sandbox mock, but INT and production require HTTPS.
        url = urlsplit(self.base_url)
        if url.scheme not in ("https", "http") or not url.hostname or url.query or url.fragment or url.username:
            raise ValueError("NIR_BASE_URL must be an HTTP(S) base URL without credentials, query or fragment")
        if self.environment != "sandbox" and url.scheme != "https":
            raise ValueError("NIR INT and production require an HTTPS base URL")

        # Reject unbounded or non-positive request timeouts before opening a
        # connection. The client uses the same timeout for every NIR endpoint.
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("NIR_TIMEOUT must be a positive number of seconds")

        # INT and production authenticate the connection with a NIR client
        # certificate and private key. A partial pair cannot be used for mTLS,
        # and GP Connect's certificates are deliberately never used as fallback.
        if bool(self.tls_cert_path) != bool(self.tls_key_path):
            raise ValueError("Set both NIR_TLS_CERT_PATH and NIR_TLS_KEY_PATH")
        if self.environment != "sandbox" and not self.tls_cert_path:
            raise ValueError("NIR INT and production require NIR_TLS_CERT_PATH and NIR_TLS_KEY_PATH")

    @classmethod
    def from_env(cls) -> "NIRConfig":
        """Read only NIR-prefixed variables and default to the public sandbox."""

        # ENV belongs to the existing GP Connect setup. NIR_ENV is independent,
        # and "dev" is an alias for NIR's published sandbox rather than a host.
        environment = os.getenv("NIR_ENV", "sandbox").strip().lower()
        if environment == "dev":
            environment = "sandbox"
        if environment not in NIR_BASE_URLS:
            raise ValueError("NIR_ENV must be sandbox, dev, int or production")

        # Leave API_KEY, JWTKEY, GP Connect TLS settings and USE_RELAY untouched.
        # Building a fresh immutable config here also allows tests and scripts
        # to set NIR variables after import, before creating their client.
        return cls(
            environment=environment,
            base_url=(os.getenv("NIR_BASE_URL") or NIR_BASE_URLS[environment]).rstrip("/"),
            timeout=float(os.getenv("NIR_TIMEOUT", "30")),
            tls_cert_path=os.getenv("NIR_TLS_CERT_PATH") or None,
            tls_key_path=os.getenv("NIR_TLS_KEY_PATH") or None,
            ca_bundle_path=os.getenv("NIR_CA_BUNDLE_PATH") or None,
        )

    def ssl_context(self) -> ssl.SSLContext:
        """Build server trust and optional client certificate authentication."""

        # A CA bundle controls which server certificates we trust; it does not
        # identify this application. Without an override, use system CA trust.
        context = ssl.create_default_context(cafile=self.ca_bundle_path)
        context.minimum_version = ssl.TLSVersion.TLSv1_2

        # The client certificate/key identify this application to NIR via mTLS.
        # SOAP WS-Security is a separate requirement handled by the caller when
        # preparing a signed envelope for INT or production.
        if self.tls_cert_path:
            context.load_cert_chain(self.tls_cert_path, self.tls_key_path)
        return context
