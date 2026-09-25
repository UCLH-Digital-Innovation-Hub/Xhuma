from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.audit.audit import AuditFailureException, attempt_audit
from app.audit.models import AuditOutcome
from app.tests.fixtures.saml_attributes import saml


@pytest.mark.asyncio
async def test_audit_fail_closed_missing_request():
    with pytest.raises(AuditFailureException) as excinfo:
        await attempt_audit(
            request=None,
            nhs_number="1234567890",
            saml=saml,
            action="test_action",
            outcome=AuditOutcome.ok,
        )
    assert "Audit context missing" in str(excinfo.value)


@pytest.mark.asyncio
async def test_audit_fail_closed_missing_sessionlocal():
    mock_request = MagicMock()
    mock_request.app = MagicMock()
    mock_request.app.state = MagicMock()
    mock_request.app.state.SessionLocal = None

    with pytest.raises(AuditFailureException) as excinfo:
        await attempt_audit(
            request=mock_request,
            nhs_number="1234567890",
            saml=saml,
            action="test_action",
            outcome=AuditOutcome.ok,
        )
    assert "Audit persistence context missing" in str(excinfo.value)


@pytest.mark.asyncio
async def test_audit_fail_closed_db_exception():
    mock_request = MagicMock()
    mock_request.app = MagicMock()
    mock_sessionlocal = MagicMock()
    mock_session = AsyncMock()
    mock_session.commit.side_effect = Exception("DB Connection Failed")

    # Setup async context manager
    mock_sessionlocal.return_value.__aenter__.return_value = mock_session
    mock_request.app.state.SessionLocal = mock_sessionlocal

    with pytest.raises(AuditFailureException) as excinfo:
        with patch("app.audit.build.build_audit_event", new_callable=AsyncMock):
            with patch("app.audit.store.insert_audit_event", new_callable=AsyncMock):
                await attempt_audit(
                    request=mock_request,
                    nhs_number="1234567890",
                    saml=saml,
                    action="test_action",
                    outcome=AuditOutcome.ok,
                )

    assert "Failed to persist audit event" in str(excinfo.value)
