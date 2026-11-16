from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_dev_audit_requires_api_key(monkeypatch):
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    monkeypatch.setenv("API_KEY", "TEST_KEY")

    # We must import app AFTER patching ENV if it depends on it at load time,
    # but `app/main.py` evaluates ENV at load time for `/_dev/audit`.
    # Let's import app and TestClient locally here
    import importlib

    import app.main

    importlib.reload(app.main)

    client = TestClient(app.main.app)

    # Missing API Key
    response = client.get("/_dev/audit")
    assert response.status_code == 401
    assert "Not authenticated" in response.text

    # Invalid API Key
    response = client.get("/_dev/audit", headers={"X-API-Key": "WRONG_KEY"})
    assert response.status_code == 401

    # Valid API Key
    response = client.get("/_dev/audit", headers={"X-API-Key": "TEST_KEY"})
    # It might return 200 or 500 depending on DB connection, but shouldn't be 401
    assert response.status_code != 401


def test_dev_audit_post_requires_api_key(monkeypatch):
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    monkeypatch.setenv("API_KEY", "TEST_KEY")

    import importlib

    import app.main

    importlib.reload(app.main)

    client = TestClient(app.main.app)

    # Missing API Key
    response = client.post("/_dev/audit", data={"query": "test"})
    assert response.status_code == 401
    assert "Not authenticated" in response.text

    # Valid API Key
    response = client.post("/_dev/audit", headers={"X-API-Key": "TEST_KEY"}, data={"query": "test"})
    assert response.status_code != 401


@pytest.mark.parametrize(
    ("failure", "status_code", "body"),
    [
        ("validation", 400, "<h3>Invalid NHS number</h3>"),
        ("query", 500, "<h3>Query failed</h3>"),
    ],
)
def test_dev_audit_errors_do_not_expose_exception_details(monkeypatch, caplog, failure, status_code, body):
    from app.api import dev

    monkeypatch.setenv("API_KEY", "TEST_KEY")
    application = FastAPI()
    application.include_router(dev.router)
    sensitive_detail = "private database parameters <script>alert('secret')</script>"
    subject_ref = MagicMock(return_value="v1:test-subject")
    if failure == "validation":
        subject_ref.side_effect = ValueError(sensitive_detail)
    monkeypatch.setattr(dev, "_subject_ref_from_nhs_number", subject_ref)

    session = AsyncMock()
    session.execute.side_effect = RuntimeError(sensitive_detail)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    application.state.SessionLocal = MagicMock(return_value=session_context)

    with TestClient(application) as client:
        response = client.post(
            "/_dev/audit",
            headers={"X-API-Key": "TEST_KEY"},
            data={"nhs_number": "9690937278"},
        )

    assert response.status_code == status_code
    assert response.text == body
    assert sensitive_detail not in caplog.text
    subject_ref.assert_called_once_with("9690937278", "TEST_KEY")
    if failure == "validation":
        application.state.SessionLocal.assert_not_called()
    else:
        session.execute.assert_awaited_once()
