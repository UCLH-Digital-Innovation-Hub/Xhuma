import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.audit.audit import AuditFailureException, attempt_audit, is_audit_fail_open
from app.audit.models import AuditOutcome
from app.tests.fixtures.saml_attributes import saml

# --- Unit tests for is_audit_fail_open() ---


@pytest.mark.parametrize("env", ["dev", "int", "local", "prod", ""])
def test_is_audit_fail_open_flag_unset(monkeypatch, env):
    """1. Flag unset -> fail closed (returns False)."""
    if env:
        monkeypatch.setenv("ENV", env)
    else:
        monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("AUDIT_FAIL_OPEN", raising=False)
    assert is_audit_fail_open() is False


@pytest.mark.parametrize("false_val", ["false", "0", "no", "off", "FALSE", "Off", "random", ""])
@pytest.mark.parametrize("env", ["dev", "int", "local"])
def test_is_audit_fail_open_flag_false(monkeypatch, env, false_val):
    """2. Flag false -> fail closed (returns False)."""
    monkeypatch.setenv("ENV", env)
    monkeypatch.setenv("AUDIT_FAIL_OPEN", false_val)
    assert is_audit_fail_open() is False


@pytest.mark.parametrize(
    "true_val",
    ["1", "true", "yes", "on", "True", "TRUE", "Yes", "YES", "On", "ON", " 1 ", " true "],
)
@pytest.mark.parametrize("env", ["dev", "int", "local"])
def test_is_audit_fail_open_flag_true_in_non_prod(monkeypatch, env, true_val):
    """3. Flag true in non-prod -> fail open (returns True)."""
    monkeypatch.setenv("ENV", env)
    monkeypatch.setenv("AUDIT_FAIL_OPEN", true_val)
    assert is_audit_fail_open() is True


@pytest.mark.parametrize("true_val", ["1", "true", "yes", "on", "True", "TRUE", "Yes", "YES", "On", "ON"])
@pytest.mark.parametrize("prod_env", ["prod", "PROD", " prod ", "production"])
def test_is_audit_fail_open_flag_true_in_prod(monkeypatch, prod_env, true_val):
    """4. ENV=prod + flag true -> still fail closed (returns False)."""
    monkeypatch.setenv("ENV", prod_env)
    monkeypatch.setenv("AUDIT_FAIL_OPEN", true_val)
    assert is_audit_fail_open() is False


# --- Helpers for attempt_audit failure contexts ---


def _build_db_failure_request():
    mock_request = MagicMock()
    mock_request.app = MagicMock()
    mock_sessionlocal = MagicMock()
    mock_session = AsyncMock()
    mock_session.commit.side_effect = Exception("DB Connection Failed")
    mock_sessionlocal.return_value.__aenter__.return_value = mock_session
    mock_request.app.state.SessionLocal = mock_sessionlocal
    return mock_request


def _build_missing_sessionlocal_request():
    mock_request = MagicMock()
    mock_request.app = MagicMock()
    mock_request.app.state = MagicMock()
    mock_request.app.state.SessionLocal = None
    return mock_request


def _build_missing_app_request():
    class NoAppRequest:
        pass

    return NoAppRequest()


# --- End-to-end tests for attempt_audit across all failure modes ---


@pytest.mark.asyncio
async def test_audit_fail_closed_missing_request(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
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
async def test_audit_fail_closed_missing_sessionlocal(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    mock_request = _build_missing_sessionlocal_request()

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
async def test_audit_fail_closed_db_exception(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    mock_request = _build_db_failure_request()

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_kind,get_req,expected_msg",
    [
        ("missing_request", lambda: None, "Audit context missing"),
        ("missing_app", _build_missing_app_request, "Audit context missing"),
        ("missing_sessionlocal", _build_missing_sessionlocal_request, "Audit persistence context missing"),
        ("db_failure", _build_db_failure_request, "Failed to persist audit event"),
    ],
)
async def test_attempt_audit_flag_unset_fails_closed(monkeypatch, failure_kind, get_req, expected_msg):
    """1. Flag unset -> fail closed across all failure modes in non-prod."""
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.delenv("AUDIT_FAIL_OPEN", raising=False)

    with pytest.raises(AuditFailureException) as excinfo:
        with patch("app.audit.build.build_audit_event", new_callable=AsyncMock):
            with patch("app.audit.store.insert_audit_event", new_callable=AsyncMock):
                await attempt_audit(
                    request=get_req(),
                    nhs_number="1234567890",
                    saml=saml,
                    action="test_action",
                    outcome=AuditOutcome.ok,
                )
    assert expected_msg in str(excinfo.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("false_val", ["false", "0", "no", "off"])
@pytest.mark.parametrize(
    "failure_kind,get_req,expected_msg",
    [
        ("missing_request", lambda: None, "Audit context missing"),
        ("missing_app", _build_missing_app_request, "Audit context missing"),
        ("missing_sessionlocal", _build_missing_sessionlocal_request, "Audit persistence context missing"),
        ("db_failure", _build_db_failure_request, "Failed to persist audit event"),
    ],
)
async def test_attempt_audit_flag_false_fails_closed(monkeypatch, false_val, failure_kind, get_req, expected_msg):
    """2. Flag false -> fail closed across all failure modes in non-prod."""
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("AUDIT_FAIL_OPEN", false_val)

    with pytest.raises(AuditFailureException) as excinfo:
        with patch("app.audit.build.build_audit_event", new_callable=AsyncMock):
            with patch("app.audit.store.insert_audit_event", new_callable=AsyncMock):
                await attempt_audit(
                    request=get_req(),
                    nhs_number="1234567890",
                    saml=saml,
                    action="test_action",
                    outcome=AuditOutcome.ok,
                )
    assert expected_msg in str(excinfo.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("true_val", ["true", "1", "yes", "on", "TRUE", "YES"])
@pytest.mark.parametrize("env", ["dev", "int"])
@pytest.mark.parametrize(
    "failure_kind,get_req,expected_msg",
    [
        ("missing_request", lambda: None, "Audit context missing"),
        ("missing_app", _build_missing_app_request, "Audit context missing"),
        ("missing_sessionlocal", _build_missing_sessionlocal_request, "Audit persistence context missing"),
        ("db_failure", _build_db_failure_request, "Failed to persist audit event"),
    ],
)
async def test_attempt_audit_flag_true_in_non_prod_fails_open(
    monkeypatch, caplog, true_val, env, failure_kind, get_req, expected_msg
):
    """3. Flag true in non-prod -> fail open (logged as warning, does not raise)."""
    monkeypatch.setenv("ENV", env)
    monkeypatch.setenv("AUDIT_FAIL_OPEN", true_val)

    with caplog.at_level(logging.WARNING):
        with patch("app.audit.build.build_audit_event", new_callable=AsyncMock):
            with patch("app.audit.store.insert_audit_event", new_callable=AsyncMock):
                # Must NOT raise:
                await attempt_audit(
                    request=get_req(),
                    nhs_number="1234567890",
                    saml=saml,
                    action="test_action",
                    outcome=AuditOutcome.ok,
                )

    # Check warning was logged with fail-open note
    assert any(
        record.levelno == logging.WARNING
        and expected_msg in record.message
        and "AUDIT_FAIL_OPEN enabled" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("true_val", ["true", "1", "yes", "on"])
@pytest.mark.parametrize(
    "failure_kind,get_req,expected_msg",
    [
        ("missing_request", lambda: None, "Audit context missing"),
        ("missing_app", _build_missing_app_request, "Audit context missing"),
        ("missing_sessionlocal", _build_missing_sessionlocal_request, "Audit persistence context missing"),
        ("db_failure", _build_db_failure_request, "Failed to persist audit event"),
    ],
)
async def test_attempt_audit_prod_with_flag_true_still_fails_closed(
    monkeypatch, caplog, true_val, failure_kind, get_req, expected_msg
):
    """4. ENV=prod + flag true -> still fail closed (raises AuditFailureException)."""
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("AUDIT_FAIL_OPEN", true_val)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(AuditFailureException) as excinfo:
            with patch("app.audit.build.build_audit_event", new_callable=AsyncMock):
                with patch("app.audit.store.insert_audit_event", new_callable=AsyncMock):
                    await attempt_audit(
                        request=get_req(),
                        nhs_number="1234567890",
                        saml=saml,
                        action="test_action",
                        outcome=AuditOutcome.ok,
                    )
        assert expected_msg in str(excinfo.value)

    assert any(record.levelno == logging.ERROR and expected_msg in record.message for record in caplog.records)
