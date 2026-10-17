from importlib import reload

import pytest


def test_gpconnect_env_dev(monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    import app.gpconnect

    reload(app.gpconnect)
    assert app.gpconnect.IS_DEV is True
    assert app.gpconnect.RELAY_BASE_PATH == "https://proxy.int.spine2.ncrs.nhs.uk"
    assert app.gpconnect.OVER_INTERNET_PATH == "https://proxy.intspineservices.nhs.uk"


def test_gpconnect_env_int(monkeypatch):
    monkeypatch.setenv("ENV", "int")
    import app.gpconnect

    reload(app.gpconnect)
    assert app.gpconnect.IS_DEV is False
    assert app.gpconnect.RELAY_BASE_PATH == "https://proxy.int.spine2.ncrs.nhs.uk"
    assert app.gpconnect.OVER_INTERNET_PATH == "https://proxy.intspineservices.nhs.uk"


def test_gpconnect_env_prod_valid(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("NHS_RELAY_BASE_PATH", "https://relay.prod")
    monkeypatch.setenv("NHS_OVER_INTERNET_PATH", "https://internet.prod")
    import app.gpconnect

    reload(app.gpconnect)
    assert app.gpconnect.IS_DEV is False
    assert app.gpconnect.RELAY_BASE_PATH == "https://relay.prod"
    assert app.gpconnect.OVER_INTERNET_PATH == "https://internet.prod"


def test_gpconnect_env_prod_missing_vars_starts(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.delenv("NHS_RELAY_BASE_PATH", raising=False)
    monkeypatch.delenv("NHS_OVER_INTERNET_PATH", raising=False)
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    monkeypatch.setenv("JWTKEY", pem.decode("utf-8"))

    # Must import main after setting ENV to ensure it loads with prod
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from app.main import app

    with patch("app.main.redis_client", autospec=True):
        with TestClient(app) as client:
            # /health and /jwk should work
            resp = client.get("/health")
            assert resp.status_code == 200

            resp = client.get("/jwk")
            assert resp.status_code == 200
            assert "keys" in resp.json()


def test_gpconnect_env_unknown(monkeypatch):
    monkeypatch.setenv("ENV", "staging")
    with pytest.raises(ValueError, match="Unknown or unsupported environment: staging"):
        import app.gpconnect

        reload(app.gpconnect)
