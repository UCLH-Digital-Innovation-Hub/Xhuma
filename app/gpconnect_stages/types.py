"""Non-validating data shared by GP Connect stages."""

from dataclasses import dataclass
from typing import Any


@dataclass
class DiagnosticStep:
    """Request-local last operation, retained even when transport raises or is cancelled."""

    value: str


@dataclass(frozen=True, repr=False)
class ProviderRoute:
    """Resolved provider address and uncoerced SDS ASID; no added validation."""

    endpoint_url: str
    asid: Any
