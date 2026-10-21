"""Non-validating data shared by GP Connect stages."""

from dataclasses import dataclass


@dataclass
class DiagnosticStep:
    """Request-local last operation, retained even when transport raises or is cancelled."""

    value: str
