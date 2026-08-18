from fastapi.testclient import TestClient


def test_dev_audit_requires_api_key(monkeypatch):
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    monkeypatch.setenv("API_KEY", "TEST_KEY")

    # We must import app AFTER patching ENV if it depends on it at load time,
    # but `app/main.py` evaluates ENV at load time for `/_dev/audit`.
    # Let's import app and TestClient locally here
    import app.main
    import importlib

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

    import app.main
    import importlib

    importlib.reload(app.main)

    client = TestClient(app.main.app)

    # Missing API Key
    response = client.post("/_dev/audit", data={"query": "test"})
    assert response.status_code == 401
    assert "Not authenticated" in response.text

    # Valid API Key
    response = client.post(
        "/_dev/audit", headers={"X-API-Key": "TEST_KEY"}, data={"query": "test"}
    )
    assert response.status_code != 401
