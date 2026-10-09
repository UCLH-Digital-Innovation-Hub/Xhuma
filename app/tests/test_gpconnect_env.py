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


def test_api_key_separation_prod_fails(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("API_KEY", "same-secret")
    monkeypatch.setenv("NHS_API_KEY", "same-secret")
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

    import app.main

    reload(app.main)
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    with pytest.raises(RuntimeError, match="CRITICAL: API_KEY and NHS_API_KEY must not be identical"):
        with patch("app.main.redis_client", autospec=True):
            with TestClient(app.main.app):
                pass


def test_api_key_separation_dev_warns(monkeypatch, capsys):
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("API_KEY", "same-secret")
    monkeypatch.setenv("NHS_API_KEY", "same-secret")

    import app.main

    reload(app.main)
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    # Should not raise
    with patch("app.main.redis_client", autospec=True):
        with TestClient(app.main.app):
            pass

    captured = capsys.readouterr()
    assert "Warning: API_KEY and NHS_API_KEY are identical" in captured.out


def test_jwk_kid_is_stable_and_deterministic():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwcrypto import jwk

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    jwk1 = jwk.JWK.from_pem(private_pem)
    jwk2 = jwk.JWK.from_pem(private_pem)

    kid1 = jwk1.export_public(as_dict=True).get("kid")
    kid2 = jwk2.export_public(as_dict=True).get("kid")

    assert kid1 == kid2
    assert kid1 is not None
