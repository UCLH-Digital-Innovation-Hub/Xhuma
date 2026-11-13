import os

USE_RELAY = os.getenv("USE_RELAY", "false").lower() in ("1", "true", "yes", "on")
RELAY_TIMEOUT = int(os.getenv("RELAY_TIMEOUT", 75))
EXTERNAL_RELAY_URL = os.getenv("EXTERNAL_RELAY_URL")
EXTERNAL_RELAY_CLIENT_ID = os.getenv("EXTERNAL_RELAY_CLIENT_ID", "client1")
EXTERNAL_RELAY_TOKEN = os.getenv("EXTERNAL_RELAY_TOKEN", "")

_TRUTHY = ("1", "true", "yes", "on")


def diag_compare_enabled() -> bool:
    """Whether the FHIR-vs-XML comparison diagnostics (capture + ``/_diag/compare``) are active.

    Always on in the non-production environments (``dev``/``local``/``int``) where the view is
    useful for every request. In production it is OFF unless ``DIAG_COMPARE_ENABLED`` is explicitly
    set -- intended only for deliberate, time-boxed PRD smoke testing, after which the flag should
    be removed again. Read live (not cached at import) so tests and runtime restarts pick up the
    current environment.
    """
    env = os.getenv("ENV", "prod").lower()
    if env in ("dev", "local", "int"):
        return True
    return os.getenv("DIAG_COMPARE_ENABLED", "false").lower() in _TRUTHY


def diag_ttl_minutes() -> int:
    """TTL (minutes) for the captured FHIR bundle / C-CDA diagnostic pair.

    Kept short and independent of ``CCDA_EXPIRY_HOURS`` so these PHI payloads self-expire quickly.
    Falls back to 30 minutes for any missing, non-numeric or non-positive value.
    """
    try:
        minutes = int(os.getenv("DIAG_TTL_MIN", "30"))
    except (TypeError, ValueError):
        return 30
    return minutes if minutes > 0 else 30
